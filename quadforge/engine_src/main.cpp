// Forge engine command line.
//
//   forge remesh --in a.fmesh --out b.fmesh --faces 20000 [--mode tri|quad] [--threads N]
//                [--no-project] [--corner-angle 35] [--stats] [--check]
//
// stdout protocol (one line each, flushed):
//   P <percent> <message>     progress
//   R <json>                  repair report
//   S <json>                  statistics of the result
//   OK                        finished
//   ERR <message>             failure (exit code 2)
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "metrics.hpp"
#include <memory>
#include <unordered_map>
#include "quad.hpp"
#include "extract.hpp"
#include "sizefield.hpp"
#include "capping.hpp"
#include "param.hpp"
#include "meshio.hpp"
#include "parallel.hpp"
#include "remesh.hpp"
#include "repair.hpp"

using namespace forge;

static void say(const char* tag, const std::string& s) {
    std::printf("%s %s\n", tag, s.c_str());
    std::fflush(stdout);
}

static void usage() {
    std::puts("usage: forge remesh --in FILE --out FILE --faces N [--mode tri|quad] [--threads N]\n"
              "              [--no-project] [--no-retry] [--corner-angle DEG] [--iterations N] [--stats] [--check]");
}

static std::string jsonSafe(std::string t) { for (auto& c : t) if (c == '"' || c == '\\' || static_cast<unsigned char>(c) < 32) c = ' '; return t; }

// wantRetry: set (and 3 returned) when the grid method failed on a mesh whose thin parts were refined; the caller
// then runs again with the thickness-based refinement off, which keeps the grid method usable.
static int runMain(int argc, char** argv, bool noAdaptive, bool* wantRetry) {
    if (argc < 2 || std::strcmp(argv[1], "remesh") != 0) { usage(); return 1; }
    std::string in, out, mode = "tri";
    RemeshParams prm;
    prm.threads = defaultThreads();
    bool stats = false;
    int relaxIters = 30;
    double fieldAlign = 0.5;
    bool useField = true;
    double poleWeight = 10.0;
    int poleSweeps = 150;
    int cyclePairs = 5;
    std::string quadMethod = "auto";
    int exIters = 60;
    int exSubdiv = 1;
    double exDensity = 6.0;
    bool useParam = true;
    bool alignFeat = true;
    bool withRetry = false;   // experimental second grid solve with another cell size
    bool noRetry = false;     // guide lines only (symmetry planes): the second grid solve gains nothing
    bool adaptive = true;
    double timeLimit = 900.0;
    double adaptRatio = 1.3, adaptMin = 0.5, curvAngle = 0.0;
    for (int i = 2; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> const char* { return i + 1 < argc ? argv[++i] : ""; };
        if (a == "--in") in = next();
        else if (a == "--out") out = next();
        else if (a == "--faces") prm.targetFaces = std::atoi(next());
        else if (a == "--mode") mode = next();
        else if (a == "--threads") prm.threads = std::max(1, std::atoi(next()));
        else if (a == "--no-project") prm.project = false;
        else if (a == "--no-retry") noRetry = true;
        else if (a == "--retry") withRetry = true;
        else if (a == "--corner-angle") prm.cornerAngleDeg = std::atof(next());
        else if (a == "--iterations") prm.iterations = std::max(1, std::atoi(next()));
        else if (a == "--stats") stats = true;
        else if (a == "--check") prm.check = true;
        else if (a == "--relax") relaxIters = std::atoi(next());
        else if (a == "--field-align") fieldAlign = std::atof(next());
        else if (a == "--no-field") useField = false;
        else if (a == "--pole-weight") poleWeight = std::atof(next());
        else if (a == "--pole-sweeps") poleSweeps = std::atoi(next());
        else if (a == "--cycle-pairs") cyclePairs = std::atoi(next());
        else if (a == "--quad-method") quadMethod = next();
        else if (a == "--ex-subdiv") exSubdiv = std::atoi(next());
        else if (a == "--ex-iters") exIters = std::atoi(next());
        else if (a == "--ex-density") exDensity = std::atof(next());
        else if (a == "--no-param") useParam = false;
        else if (a == "--no-align") alignFeat = false;
        else if (a == "--no-adaptive") adaptive = false;
        else if (a == "--time-limit") timeLimit = std::atof(next());
        else if (a == "--adaptive-ratio") adaptRatio = std::atof(next());
        else if (a == "--adaptive-min") adaptMin = std::atof(next());
        else if (a == "--curv-adapt") curvAngle = std::atof(next());
        else { usage(); return 1; }
    }
    if (const char* e = std::getenv("FORGE_ADAPT_RATIO")) adaptRatio = std::atof(e);
    if (const char* e = std::getenv("FORGE_ADAPT_MIN")) adaptMin = std::atof(e);
    if (const char* e = std::getenv("FORGE_CURV")) curvAngle = std::atof(e);
    if (noAdaptive) adaptive = false;
    if (prm.targetFaces < 1000) curvAngle = 0;   // too few faces for creases to matter; the extra detail only upsets the layout
    if (in.empty() || out.empty() || prm.targetFaces < 4) { usage(); return 1; }
    prm.progress = [](int pct, const std::string& msg) { say("P", std::to_string(pct) + " " + msg); };

    try {
        say("P", "2 Reading");
        PolyMesh input = readFmesh(in);
        say("P", "6 Checking the mesh");
        RepairReport rep;
        TriSoup soup = repairMesh(input, rep);
        char buf[512];
        std::snprintf(buf, sizeof buf,
            "{\"input_polygons\":%d,\"repeated_vertex_faces\":%d,\"duplicate_faces\":%d,"
            "\"nonmanifold_edges\":%d,\"conflicting_edges\":%d,\"flipped_faces\":%d,"
            "\"pinched_vertices\":%d,\"collision_faces\":%d,\"unused_vertices\":%d,\"faces\":%zu}",
            rep.inputPolygons, rep.repeatedVertexFaces, rep.duplicateFaces, rep.nonManifoldEdges,
            rep.conflictingEdges, rep.flippedFaces, rep.pinchedVertices, rep.collisionFaces,
            rep.unusedVertices, soup.F.size());
        say("R", buf);

        const bool quadMode = (mode == "quad");
        const int stitched = stitchSlits(soup);
        if (stitched && std::getenv("FORGE_DBG")) std::fprintf(stderr, "removed %d flat sliver triangles\n", stitched);
        const int requested = prm.targetFaces;
        // The global lattice needs a surface without borders or marked edges: a closed loop of
        // fixed length cannot hold a whole number of cells. Those meshes use the pairing path.
        // share of marked edges: many marked edges mean the user wants a hard-surface layout, which the
        // pairing path keeps exactly; a few stray ones are snapped onto at the end
        const double featureShare = soup.F.empty() ? 0.0 : static_cast<double>(soup.features.size()) / (1.5 * soup.F.size());
        bool hasBorder = false;
        {
            std::unordered_map<uint64_t, int> cnt;
            for (const auto& t : soup.F)
                for (int k = 0; k < 3; ++k) {
                    const uint64_t a = static_cast<uint32_t>(t[k]), b = static_cast<uint32_t>(t[(k + 1) % 3]);
                    cnt[(std::min(a, b) << 32) | std::max(a, b)]++;
                }
            for (const auto& kv : cnt) if (kv.second == 1) { hasBorder = true; break; }
        }
        const bool alignMode = useParam && alignFeat && !std::getenv("FORGE_NOPARAM");
        // open borders: close them with caps, align the grid to the border lines, drop the caps afterwards
        std::string fallbackReason; std::string densityNote;
        CapInfo capInfo;
        TriSoup origSoup;
        bool capped = false;
        int closedHoles = 0;
        double capAreaFrac = 0;
        if (quadMode && hasBorder && alignMode && (quadMethod == "auto" || quadMethod == "extract") && !std::getenv("FORGE_NOCAP")) {
            double area0 = 0;
            for (const auto& t : soup.F) area0 += 0.5 * length(cross(soup.P[t[1]] - soup.P[t[0]], soup.P[t[2]] - soup.P[t[0]]));
            const double rho0 = std::sqrt(area0 / std::max(1.0, requested / 4.0));
            TriSoup trial = soup;
            origSoup = soup;
            if (capBorders(trial, 6.0 * rho0, capInfo) && capInfo.loops > 0 && capInfo.removable == 0) {
                // only small holes: close them (the result is a closed surface, the holes are filled)
                soup = std::move(trial);
                hasBorder = false;
                closedHoles = capInfo.loops;
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "closed %d small holes\n", capInfo.loops);
            } else if (capInfo.loops > 0 && capInfo.removable > 0) {
                soup = std::move(trial);
                hasBorder = false;
                capped = true;
                double aAll = 0, aCap = 0;
                for (size_t i = 0; i < soup.F.size(); ++i) {
                    const auto& t = soup.F[i];
                    const double a = 0.5 * length(cross(soup.P[t[1]] - soup.P[t[0]], soup.P[t[2]] - soup.P[t[0]]));
                    aAll += a;
                    if (capInfo.removableFace[i]) aCap += a;
                }
                capAreaFrac = aAll > 0 ? aCap / aAll : 0;
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "capped %d border loops (%d removable)\n", capInfo.loops, capInfo.removable);
            }
        }
        bool extract = quadMode && (quadMethod == "extract" || (quadMethod == "auto" && (input.density.empty() || alignMode) && !hasBorder && (alignMode || featureShare < 0.01)));
        if (quadMode && !extract) fallbackReason = quadMethod == "pair" ? "pairing method requested" : hasBorder ? "open borders that could not be closed" : (!alignMode ? "grid alignment disabled" : "mesh not suited to the grid method");
        // The lattice path remeshes without the marked edges (they would add poles around them)
        // and snaps the result onto them at the end.
        bool adaptApplied = false;
        if (extract && adaptive && !std::getenv("FORGE_NOADAPT")) {
            // thin parts get smaller quads: the density (edge length multiplier) is lowered there
            double area0 = 0;
            for (const auto& t : soup.F) area0 += 0.5 * length(cross(soup.P[t[1]] - soup.P[t[0]], soup.P[t[2]] - soup.P[t[0]]));
            SizeFieldParams sp;
            sp.rho = std::sqrt(area0 / std::max(1, requested));
            sp.ratio = adaptRatio; sp.minScale = adaptMin; sp.threads = prm.threads;
            SizeFieldStats sfs;
            std::vector<float> s = thicknessSizeField(soup, sp, sfs);
            if (curvAngle > 0) {
                SizeFieldStats cs;
                SizeFieldParams spc = sp;
                spc.minScale = std::getenv("FORGE_CURV_MIN") ? std::atof(std::getenv("FORGE_CURV_MIN")) : 0.3;
                const auto tc0 = std::chrono::steady_clock::now();
                const std::vector<float> sc = curvatureSizeField(soup, spc, curvAngle, cs);
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "curvature size field: %.2fs\n", std::chrono::duration<double>(std::chrono::steady_clock::now() - tc0).count());
                if (cs.refined > 0) {
                    if (s.size() == sc.size()) for (size_t v = 0; v < s.size(); ++v) s[v] = std::min(s[v], sc[v]);
                    sfs.refined = std::max(sfs.refined, cs.refined);
                    sfs.minSeen = std::min(sfs.minSeen, cs.minSeen);
                }
                if (std::getenv("FORGE_GAP")) {
                    SizeFieldParams spg = sp; spg.ratio = std::atof(std::getenv("FORGE_GAP")); spg.minScale = 0.3;
                    SizeFieldStats gs;
                    const std::vector<float> sg = gapSizeField(soup, spg, gs);
                    if (gs.refined > 0 && s.size() == sg.size()) { for (size_t v = 0; v < s.size(); ++v) s[v] = std::min(s[v], sg[v]); sfs.refined = std::max(sfs.refined, gs.refined); sfs.minSeen = std::min(sfs.minSeen, gs.minSeen); }
                    if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "gap adaptive: refined %d vertices, smallest scale %.2f\n", gs.refined, gs.minSeen);
                }
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "curvature adaptive: refined %d vertices, smallest scale %.2f\n", cs.refined, cs.minSeen);
            }
            if (sfs.refined > 0) {
                adaptApplied = true;
                if (soup.density.empty()) soup.density.assign(soup.P.size(), 1.0f);
                for (size_t v = 0; v < s.size(); ++v) soup.density[v] *= s[v];
            }
            if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "adaptive: refined %d of %zu vertices, smallest scale %.2f\n", sfs.refined, s.size(), sfs.minSeen);
        }
        TriSoup freeSoup = soup;
        freeSoup.features.clear();
        TriSoup featSoup = freeSoup;     // only the long chains of marked edges
        SegmentIndex markedLines;
        {
            // keep only long chains of marked edges: short ones are mostly noise and would only
            // drag vertices around
            double area = 0;
            for (const auto& t : soup.F) area += 0.5 * length(cross(soup.P[t[1]] - soup.P[t[0]], soup.P[t[2]] - soup.P[t[0]]));
            const double rho0 = std::sqrt(area / std::max(1.0, requested / 4.0));
            std::vector<int> parent(soup.P.size());
            for (size_t i = 0; i < parent.size(); ++i) parent[i] = static_cast<int>(i);
            auto findp = [&](int x) { while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
            for (const auto& e : soup.features) parent[findp(e[0])] = findp(e[1]);
            std::unordered_map<int, double> chainLength;
            for (const auto& e : soup.features) chainLength[findp(e[0])] += distance(soup.P[e[0]], soup.P[e[1]]);
            std::vector<std::array<Vec3, 2>> segs;
            for (const auto& e : soup.features)
                if (chainLength[findp(e[0])] >= 6.0 * rho0) segs.push_back({soup.P[e[0]], soup.P[e[1]]});
            if (!segs.empty()) markedLines.build(std::move(segs));
            for (const auto& e : soup.features)
                if (chainLength[findp(e[0])] >= 6.0 * rho0) featSoup.features.push_back(e);
        }
        std::unique_ptr<Remesher> rem;
        PolyMesh result;
        RemeshStats st;
        auto doRemesh = [&](int target) {
            prm.targetFaces = target;
            rem.reset(new Remesher(extract ? (alignMode ? featSoup : freeSoup) : soup, prm));
            return rem->run(result, st);
        };
        const int pairTarget = std::max(4, requested / 2);    // every pair of triangles becomes 4 quads
        const int extractTarget = std::max(16, static_cast<int>(requested * exDensity / (exSubdiv ? 4.0 : 1.0)));
        if (!doRemesh(quadMode ? (extract ? extractTarget : pairTarget) : requested)) {
            say("ERR", st.error.empty() ? "remesh failed" : st.error);
            return 2;
        }
        QuadStats qs;
        std::string usedMethod = quadMode ? (extract ? "extract" : "pair") : "tri";
        if (extract) {
            ExtractParams ep;
            double area = 0;
            for (int f = 0; f < rem->mesh.numFaceSlots(); ++f) if (rem->mesh.falive[f]) {
                double sc = 1.0;
                if (!rem->mesh.vdens.empty()) sc = (rem->mesh.vdens[rem->mesh.V[3*f]] + rem->mesh.vdens[rem->mesh.V[3*f+1]] + rem->mesh.vdens[rem->mesh.V[3*f+2]]) / 3.0;
                area += 0.5 * length(rem->mesh.faceNormalUnnormalized(f)) / (sc * sc);
            }
            ep.subdivide = exSubdiv != 0;
            ep.rho = 0.98 * std::sqrt(area * (1.0 - capAreaFrac) / (exSubdiv ? requested / 4.0 : requested));   // the first pass tends to give ~3% too few quads
            ep.iterations = exIters;
            ep.progress = prm.progress;
            // the output size follows from the lattice spacing; correct it up to twice
            PolyMesh best;
            ExtractStats bestStats;
            double bestErr = 1e300;
            bool any = false;
            std::string qerr;
            ep.threads = prm.threads;
            ep.paramTimeLimit = timeLimit;
            ep.useParam = useParam && !std::getenv("FORGE_NOPARAM");
            ep.alignFeatures = ep.useParam && alignMode && !featSoup.features.empty();
            ep.seamRetry = withRetry && !noRetry;
            SegmentIndex guideIdx;
            if (!input.guides.empty()) {
                std::vector<std::array<Vec3, 2>> gs(input.guides.begin(), input.guides.end());
                guideIdx.build(std::move(gs));
                ep.guides = &guideIdx;
                ep.guideStrength = std::getenv("FORGE_GUIDE") ? std::atof(std::getenv("FORGE_GUIDE")) : 8.0;
                ep.guideRadius = std::getenv("FORGE_GUIDER") ? std::atof(std::getenv("FORGE_GUIDER")) : 4.0;
            }
            if (const char* e = std::getenv("FORGE_POLEMOVES")) ep.poleMoves = std::atof(e);
            if (curvAngle > 0) {
                ep.curvAlign = std::getenv("FORGE_CALIGN") ? std::atof(std::getenv("FORGE_CALIGN")) : 4.0;
                ep.curvGate = std::getenv("FORGE_CGATE") ? std::atof(std::getenv("FORGE_CGATE")) : 0.2;
            }
            Extractor ex(rem->mesh, rem->ref, markedLines, ep);
            std::unique_ptr<Remesher> coarse;
            if (rem->mesh.aliveFaces > 12000) {
                RemeshParams cp = prm;
                cp.progress = nullptr;
                cp.targetFaces = std::max(2000, rem->mesh.aliveFaces / 4);
                coarse.reset(new Remesher(ep.alignFeatures ? featSoup : freeSoup, cp));
                PolyMesh tmp;
                RemeshStats cst;
                if (coarse->run(tmp, cst)) ex.setFieldMesh(&coarse->mesh);
            }
            SurfaceIndex capIdx;
            if (capped) capIdx.build(soup.P, soup.F);
            for (int pass = 0; pass < 3; ++pass) {
                ExtractStats es;
                PolyMesh quads;
                if (!ex.run(quads, es, qerr)) { fallbackReason = "grid solve failed: " + (qerr.empty() ? std::string("no detail") : qerr); break; }
                if (capped) {
                    // drop the quads that lie on a cap. Vertices on the border line itself are neutral;
                    // a quad goes if one of its vertices is on the cap side, or if all lie on the line
                    std::vector<int> remapV(quads.P.size(), -1);
                    std::vector<uint8_t> side(quads.P.size(), 0);      // 0 real, 1 cap, 2 on the border line
                    for (size_t v = 0; v < quads.P.size(); ++v) {
                        const auto hl = markedLines.closest(quads.P[v]);
                        if (hl.seg >= 0 && distance(hl.p, quads.P[v]) < 0.08 * ep.rho) { side[v] = 2; continue; }
                        const auto h = capIdx.closest(quads.P[v]);
                        side[v] = (h.tri >= 0 && capInfo.removableFace[h.tri]) ? 1 : 0;
                    }
                    PolyMesh cut;
                    size_t at0 = 0;
                    for (int sz : quads.sizes) {
                        bool anyCap = false, anyReal = false;
                        for (int k = 0; k < sz; ++k) {
                            const uint8_t s = side[quads.idx[at0 + k]];
                            if (s == 1) anyCap = true;
                            if (s == 0) anyReal = true;
                        }
                        if (!anyCap && anyReal) {
                            cut.sizes.push_back(sz);
                            for (int k = 0; k < sz; ++k) {
                                const int v = quads.idx[at0 + k];
                                if (remapV[v] < 0) { remapV[v] = static_cast<int>(cut.P.size()); cut.P.push_back(quads.P[v]); }
                                cut.idx.push_back(remapV[v]);
                            }
                        }
                        at0 += sz;
                    }
                    quads = std::move(cut);
                    {   // the first row along a border can end up squeezed onto the line: weld very short edges
                        std::unordered_map<uint64_t, uint8_t> seenE;
                        double sum = 0; size_t cntE = 0;
                        size_t a1 = 0;
                        std::vector<std::array<int, 2>> edgeList;
                        for (int sz : quads.sizes) {
                            for (int k = 0; k < sz; ++k) {
                                const int a = quads.idx[a1 + k], b = quads.idx[a1 + (k + 1) % sz];
                                const uint64_t key = (static_cast<uint64_t>(std::min(a, b)) << 32) | static_cast<uint32_t>(std::max(a, b));
                                if (seenE.emplace(key, 1).second) { edgeList.push_back({a, b}); sum += distance(quads.P[a], quads.P[b]); ++cntE; }
                            }
                            a1 += sz;
                        }
                        const double thr = cntE ? 0.2 * sum / cntE : 0.0;
                        std::vector<int> par(quads.P.size());
                        for (size_t i = 0; i < par.size(); ++i) par[i] = static_cast<int>(i);
                        auto fnd = [&](int x) { while (par[x] != x) { par[x] = par[par[x]]; x = par[x]; } return x; };
                        int welded = 0;
                        for (const auto& e : edgeList)
                            if (distance(quads.P[e[0]], quads.P[e[1]]) < thr) { const int ra = fnd(e[0]), rb = fnd(e[1]); if (ra != rb) { par[ra] = rb; ++welded; } }
                        {   // vertices on the border line that sit on top of each other (not necessarily joined by an edge)
                            std::vector<int> onLine;
                            for (size_t v = 0; v < quads.P.size(); ++v) {
                                const auto hl = markedLines.closest(quads.P[v]);
                                if (hl.seg >= 0 && distance(hl.p, quads.P[v]) < 0.08 * ep.rho) onLine.push_back(static_cast<int>(v));
                            }
                            if (onLine.size() <= 6000)
                                for (size_t i = 0; i < onLine.size(); ++i)
                                    for (size_t j = i + 1; j < onLine.size(); ++j)
                                        if (distance(quads.P[onLine[i]], quads.P[onLine[j]]) < thr) {
                                            const int ra = fnd(onLine[i]), rb = fnd(onLine[j]);
                                            if (ra != rb) { par[ra] = rb; ++welded; }
                                        }
                        }
                        if (welded) {
                            std::vector<Vec3> acc(quads.P.size());
                            std::vector<int> n(quads.P.size(), 0);
                            for (size_t i = 0; i < quads.P.size(); ++i) { const int r = fnd(static_cast<int>(i)); acc[r] += quads.P[i]; ++n[r]; }
                            PolyMesh w;
                            std::vector<int> rid(quads.P.size(), -1);
                            size_t a2 = 0;
                            for (int sz : quads.sizes) {
                                std::vector<int> poly;
                                for (int k = 0; k < sz; ++k) {
                                    const int r = fnd(quads.idx[a2 + k]);
                                    if (poly.empty() || poly.back() != r) poly.push_back(r);
                                }
                                if (poly.size() > 1 && poly.front() == poly.back()) poly.pop_back();
                                a2 += sz;
                                std::vector<int> uniq;
                                for (int r : poly) if (std::find(uniq.begin(), uniq.end(), r) == uniq.end()) uniq.push_back(r);
                                if (uniq.size() != poly.size() || poly.size() < 3) continue;
                                w.sizes.push_back(static_cast<int>(poly.size()));
                                for (int r : poly) {
                                    if (rid[r] < 0) { rid[r] = static_cast<int>(w.P.size()); w.P.push_back(acc[r] / static_cast<double>(n[r])); }
                                    w.idx.push_back(rid[r]);
                                }
                            }
                            // keep the welded mesh only if it is still a clean manifold
                            std::unordered_map<uint64_t, int> dir;
                            bool clean = true;
                            size_t a3 = 0;
                            for (int sz : w.sizes) {
                                for (int k = 0; k < sz && clean; ++k) {
                                    const uint64_t a = static_cast<uint32_t>(w.idx[a3 + k]), b = static_cast<uint32_t>(w.idx[a3 + (k + 1) % sz]);
                                    if (++dir[(a << 32) | b] > 1) clean = false;
                                }
                                a3 += sz;
                            }
                            for (const auto& kv : dir) {
                                const uint64_t a = kv.first >> 32, b = kv.first & 0xffffffffu;
                                if (a < b && dir.count((b << 32) | a) && false) clean = false;
                            }
                            if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "border: welded %d short edges, clean %d\n", welded, clean);
                            if (clean) quads = std::move(w);
                        }
                    }
                }
                {   // the input is closed, so every open edge in the result is a defect: do not accept it
                    std::unordered_map<uint64_t, int> ec;
                    size_t at = 0;
                    for (int sz : quads.sizes) {
                        for (int k = 0; k < sz; ++k) {
                            const uint64_t a = static_cast<uint32_t>(quads.idx[at + k]), b = static_cast<uint32_t>(quads.idx[at + (k + 1) % sz]);
                            ec[(std::min(a, b) << 32) | std::max(a, b)]++;
                        }
                        at += sz;
                    }
                    int open = 0;
                    for (const auto& kv : ec) if (kv.second == 1) ++open;
                    if (open > 0 && !capped) {
                        if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "extract: result has %d open edges, rejected\n", open);
                        ep.rho *= 0.97;     // a slightly different grid often closes
                        ex.setRho(ep.rho);
                        continue;
                    }
                }
                const double ratio = static_cast<double>(quads.sizes.size()) / requested;
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "pass %d: %zu quads for %d requested (ratio %.3f)\n", pass, quads.sizes.size(), requested, ratio);
                const double err = std::fabs(ratio - 1.0);
                if (err < bestErr) { bestErr = err; best = std::move(quads); bestStats = es; any = true; }
                if (err < (requested >= 30000 ? 0.06 : 0.04)) break;
                ep.rho *= std::sqrt(ratio);
                ex.setRho(ep.rho);
            }
            if (any && capped && bestErr > 0.25) {
                // the capped surface did not give a usable result at this size: use the pairing path instead
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "capped extraction off by %.0f%%, falling back\n", 100 * bestErr);
                any = false; fallbackReason = "open-border result too far from the requested count";
            }
            if (any && !soup.density.empty()) {
                // does the grid follow the requested size variation? (a smooth shape with few
                // singularities cannot always do it, and then the pairing path is the honest answer)
                SurfaceIndex dsi;
                dsi.build(soup.P, soup.F);
                std::vector<std::pair<double, double>> se;      // (wanted size, achieved edge)
                size_t at = 0;
                for (int sz : best.sizes) {
                    Vec3 c; double el = 0;
                    for (int k = 0; k < sz; ++k) {
                        const Vec3& a = best.P[best.idx[at + k]];
                        const Vec3& b = best.P[best.idx[at + (k + 1) % sz]];
                        c += a; el += distance(a, b);
                    }
                    c = c / static_cast<double>(sz); el /= sz; at += sz;
                    const auto h = dsi.closest(c);
                    if (h.tri < 0) continue;
                    const auto& t = soup.F[h.tri];
                    const double s = (1 - h.u - h.v) * soup.density[t[0]] + h.u * soup.density[t[1]] + h.v * soup.density[t[2]];
                    se.push_back({s, el});
                }
                if (se.size() > 100) {
                    std::sort(se.begin(), se.end());
                    const size_t n30 = se.size() * 3 / 10;
                    auto med = [&](size_t b0, size_t e0, bool second) {
                        std::vector<double> v;
                        for (size_t i = b0; i < e0; ++i) v.push_back(second ? se[i].second : se[i].first);
                        std::nth_element(v.begin(), v.begin() + v.size() / 2, v.end());
                        return v[v.size() / 2];
                    };
                    const double wanted = med(se.size() - n30, se.size(), false) / med(0, n30, false);
                    const double got = med(se.size() - n30, se.size(), true) / med(0, n30, true);
                    if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "density follow: wanted ratio %.2f got %.2f\n", wanted, got);
                    if (wanted > 1.05 && got < 1.0 + 0.5 * (wanted - 1.0)) densityNote = "partial";
                }
            }
            if (any) {
                result = std::move(best);
                const ExtractStats& es = bestStats;
                qs.fieldSeconds = es.fieldSeconds; qs.matchSeconds = es.positionSeconds; qs.relaxSeconds = es.graphSeconds;
                qs.irregularBefore = es.tris; qs.irregularAfter = es.other; qs.pairs = es.cells; qs.leftover = es.droppedLoops;
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "extract: cells %d edges %d quads %d tris %d other %d dropped %d filled %d resid %.3f\n", es.cells, es.edges, es.quads, es.tris, es.other, es.droppedLoops, es.filled, es.residual);
            } else {
                // the thin-part refinement is the usual reason the grid fails: try again without it before giving up
                if (adaptApplied && wantRetry && !noAdaptive) { *wantRetry = true; return 3; }
                // fall back to the pairing path on a mesh made for it
                extract = false;
                usedMethod = "pair";
                if (fallbackReason.empty()) fallbackReason = "no usable grid result (open edges or count off after 3 passes)";
                if (capped) { soup = origSoup; capped = false; }
                if (!doRemesh(pairTarget)) { say("ERR", st.error.empty() ? "remesh failed" : st.error); return 2; }
            }
        }
        if (!extract && quadMode) {
            QuadParams qp;
            qp.threads = prm.threads;
            qp.project = prm.project;
            qp.progress = prm.progress;
            qp.relaxIterations = relaxIters;
            qp.fieldAlign = fieldAlign;
            qp.useField = useField;
            qp.poleWeight = poleWeight;
            qp.poleSweeps = poleSweeps;
            qp.cyclePairs = cyclePairs;
            Quadifier quad(rem->mesh, rem->ref, rem->featureLines, rem->baseLength, qp);
            PolyMesh quads;
            std::string qerr;
            if (!quad.run(quads, qs, qerr)) { say("ERR", qerr); return 2; }
            result = std::move(quads);
        }
        writeFmesh(out, result);
        if (stats) {
            std::string js = metricsJson(result, &rem->ref);
            js.insert(js.size() - 1, ",\"method\":\"" + usedMethod + "\",\"closed_holes\":" + std::to_string(closedHoles) + ",\"removed_slivers\":" + std::to_string(stitched) + ",\"density_note\":\"" + densityNote + "\",\"fallback\":\"" + jsonSafe(fallbackReason) + "\",\"seconds\":" + std::to_string(st.seconds) +
                                     ",\"edge_length\":" + std::to_string(st.edgeLength) +
                                     ",\"rounds\":" + std::to_string(st.rounds) +
                                     ",\"pairs\":" + std::to_string(qs.pairs) + ",\"leftover_tris\":" + std::to_string(qs.leftover) +
                                     ",\"field_s\":" + std::to_string(qs.fieldSeconds) + ",\"match_s\":" + std::to_string(qs.matchSeconds) +
                                     ",\"relax_s\":" + std::to_string(qs.relaxSeconds) +
                                     ",\"irregular_before\":" + std::to_string(qs.irregularBefore) +
                                     ",\"irregular_after\":" + std::to_string(qs.irregularAfter));
            say("S", js);
        }
        say("OK", "");
        return 0;
    } catch (const std::exception& e) {
        say("ERR", e.what());
        return 2;
    }
}

int main(int argc, char** argv) {
    bool retry = false;
    int rc = runMain(argc, argv, false, &retry);
    if (rc == 3 && retry) {
        if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "grid method failed with thickness refinement: running again without it\n");
        rc = runMain(argc, argv, true, nullptr);
    }
    return rc;
}
