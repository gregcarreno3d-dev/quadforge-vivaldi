// Forge engine - global quad extraction (experimental, "--quad-method extract").
//
// Instead of pairing triangles of the base mesh (whose valence irregularity fixes
// the poles), this stage lays a global grid over the surface:
//   1. a 4-direction field per vertex (from the smooth face cross field)
//   2. a position field o_v: the origin of a rho-spaced square lattice at every vertex,
//      optimised so neighbouring lattices share nodes (Gauss-Seidel, Jakob et al. 2015)
//   3. every vertex snaps to the nearest node of its lattice; vertices that snap to the
//      same node form one cell, vertices that snap to neighbouring nodes create an edge
//   4. faces are traced in the rotation system of the cell graph; loops of 4 are quads
// The whole thing is a heuristic: whatever does not close into a clean quad stays an
// n-gon, and the caller falls back to the pairing path when the result is poor.
#pragma once
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <chrono>
#include <numeric>
#include <random>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "bvh.hpp"
#include "cmesh.hpp"
#include "field.hpp"
#include "meshio.hpp"
#include "param.hpp"
#include "parallel.hpp"
#include "quadopt.hpp"
#include "quadquality.hpp"

namespace forge {

struct ExtractParams {
    double rho = 0;            // lattice spacing
    int iterations = 60;       // position field sweeps
    int smooth = 6;            // final cell relaxation passes
    bool subdivide = false;    // split every face into quads (all-quad output)
    double poleMoves = 0;      // chord rotation moves per face (0 = off)
    bool constrainLattice = false;   // hard lattice constraints on features (only good when the feature lengths fit the grid)
    int threads = 1;
    int finalSmooth = 15;      // relaxation passes after subdividing
    bool useParam = false;     // integer-grid parametrization instead of the position field
    double paramTimeLimit = 0;    // seconds for one parametrization run (0 = no limit); on expiry the caller falls back
    bool alignFeatures = false;   // keep marked edges and align the grid to them (needs useParam)
    double curvAlign = 0;         // pull of the cross field towards creases (0 = off)
    double curvGate = 0.2;        // bending angle that counts as a crease
    const SegmentIndex* guides = nullptr;   // user flow guides (soft hints)
    double guideStrength = 0;
    double guideRadius = 4.0;     // reach of a guide in mean edge lengths of the field mesh
    bool seamRetry = false;       // off: solve again with a slightly different cell size when junctions did not close (measured: no gain, 2-3x the time)
    std::function<void(int, const std::string&)> progress;
};

struct ExtractStats {
    int filled = 0, cells = 0, quads = 0, tris = 0, other = 0, droppedLoops = 0, edges = 0;
    double residual = 0;       // mean lattice mismatch / rho after the last sweep
    double fieldSeconds = 0, positionSeconds = 0, graphSeconds = 0;
};

class Extractor {
public:
    Extractor(const CMesh& m, const SurfaceIndex& ref, const SegmentIndex& feat, const ExtractParams& p) : m_(m), ref_(ref), feat_(feat), prm_(p) {}
    bool run(PolyMesh& out, ExtractStats& st, std::string& err);
    void setRho(double rho) { prm_.rho = rho; }
    // solve the cross field on a coarser mesh of the same surface and transfer it (much faster)
    void setFieldMesh(const CMesh* coarse) { coarse_ = coarse; }

private:
    const CMesh& m_;
    const SurfaceIndex& ref_;
    const SegmentIndex& feat_;
    ExtractParams prm_;
    std::vector<int> ids_;                 // live vertices
    std::vector<Vec3> N_, Q_, R_, O_;      // normal, lattice axes, lattice origin per vertex
    std::vector<int> adjStart_, adj_;
    std::vector<Vec3> singC_;
    std::vector<uint8_t> featFlag_;
    bool prepared_ = false;
    int paramRuns_ = 0;
    double densPow_ = std::getenv("FORGE_DENSPOW") ? std::atof(std::getenv("FORGE_DENSPOW")) : 1.0;     // how much of the density the grid can follow (lowered when the fit fails)
    ParamResult pr_;
    std::vector<cplx> zf_;                 // cross field of the working mesh
    const CMesh* coarse_ = nullptr;

    Vec3 node(const Vec3& o, const Vec3& q, const Vec3& r, const Vec3& x) const {
        const Vec3 d = x - o;
        const double a = std::round(dot(d, q) / prm_.rho), b = std::round(dot(d, r) / prm_.rho);
        return o + q * (a * prm_.rho) + r * (b * prm_.rho);
    }
    void report(int pct, const std::string& s) const { if (prm_.progress) prm_.progress(pct, s); }
};

inline bool Extractor::run(PolyMesh& out, ExtractStats& st, std::string& err) {
    using clock = std::chrono::steady_clock;
    auto secs = [](clock::time_point a, clock::time_point b) { return std::chrono::duration<double>(b - a).count(); };
    double rho = prm_.rho;
    const double kMerge = 0.6, kLo = 0.4, kHi = 1.6;   // snap thresholds in units of the lattice spacing
    const int nVs = m_.numVertSlots();
    const int nF = m_.numFaceSlots();
    auto t0 = clock::now();

    // ---- 1. field -------------------------------------------------------------
    if (!prepared_) {
    ids_.clear();
    FaceFrames fr = buildFrames(m_);
    std::vector<cplx> z;
    if (!coarse_) z = solveCrossField(m_, fr, prm_.alignFeatures, prm_.curvAlign, prm_.curvGate, prm_.guides, prm_.guideStrength, prm_.guideRadius);
    else {
        FaceFrames cf = buildFrames(*coarse_);
        const std::vector<cplx> cz = solveCrossField(*coarse_, cf, prm_.alignFeatures, prm_.curvAlign, prm_.curvGate, prm_.guides, prm_.guideStrength, prm_.guideRadius);
        std::vector<Vec3> cp = coarse_->P;
        std::vector<std::array<int, 3>> ct;
        std::vector<int> cface;
        for (int f = 0; f < coarse_->numFaceSlots(); ++f)
            if (coarse_->falive[f]) { ct.push_back({coarse_->V[3*f], coarse_->V[3*f+1], coarse_->V[3*f+2]}); cface.push_back(f); }
        SurfaceIndex cidx;
        cidx.build(cp, ct);
        z.assign(nF, cplx(1, 0));
        parallelFor(nF, prm_.threads, [&](int b, int e, int) {
            for (int f = b; f < e; ++f) {
                if (!m_.falive[f]) continue;
                const Vec3 c = (m_.P[m_.V[3*f]] + m_.P[m_.V[3*f+1]] + m_.P[m_.V[3*f+2]]) / 3.0;
                const auto h = cidx.closest(c);
                if (h.tri < 0) continue;
                const int g = cface[h.tri];
                const Vec3 a = axisFromZ(cf, g, cz[g]);
                z[f] = std::polar(1.0, 4.0 * std::atan2(dot(a, fr.e2[f]), dot(a, fr.e1[f])));
            }
        });
        if (prm_.alignFeatures) {
            // faces next to a marked edge keep the exact alignment (the coarse mesh only knows it approximately)
            for (int f = 0; f < nF; ++f) {
                if (!m_.falive[f]) continue;
                bool have = false, conflict = false;
                cplx target;
                for (int k = 0; k < 3; ++k) {
                    if (!fr.feat[f][k]) continue;
                    const cplx t = std::polar(1.0, 4.0 * fr.alpha[f][k]);
                    if (!have) { target = t; have = true; }
                    else if (std::real(std::conj(target) * t) < 0.5) conflict = true;
                }
                if (have && !conflict) z[f] = target;
            }
        }
    }
    zf_ = z;
    N_.assign(nVs, Vec3());
    Q_.assign(nVs, Vec3());
    R_.assign(nVs, Vec3());
    O_.assign(nVs, Vec3());
    std::vector<cplx> acc(nVs, cplx(0, 0));
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f]) continue;
        const Vec3 nn = m_.faceNormalUnnormalized(f);
        for (int k = 0; k < 3; ++k) N_[m_.V[3 * f + k]] += nn;
    }
    std::vector<Vec3> T1(nVs);
    for (int v = 0; v < nVs; ++v) {
        if (!m_.valive[v]) continue;
        ids_.push_back(v);
        N_[v] = normalized(N_[v]);
        const Vec3 helper = std::fabs(N_[v].x) < 0.9 ? Vec3(1, 0, 0) : Vec3(0, 1, 0);
        T1[v] = normalized(cross(N_[v], helper));
    }
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f]) continue;
        const Vec3 a = axisFromZ(fr, f, z[f]);
        const double w = length(m_.faceNormalUnnormalized(f));
        for (int k = 0; k < 3; ++k) {
            const int v = m_.V[3 * f + k];
            Vec3 ap = a - N_[v] * dot(N_[v], a);
            const double l = length(ap);
            if (l < 1e-6) continue;
            ap = ap / l;
            const double phi = std::atan2(dot(ap, cross(N_[v], T1[v])), dot(ap, T1[v]));
            acc[v] += std::polar(w * l, 4.0 * phi);
        }
    }
    for (int v : ids_) {
        const double th = std::abs(acc[v]) > 1e-30 ? std::arg(acc[v]) / 4.0 : 0.0;
        const Vec3 t2 = cross(N_[v], T1[v]);
        Q_[v] = T1[v] * std::cos(th) + t2 * std::sin(th);
        R_[v] = cross(N_[v], Q_[v]);
        O_[v] = m_.P[v];
    }
    // features: neighbours across feature edges / borders, lattice aligned to the crease
    std::vector<std::vector<int>> featNb(nVs);
    for (int c = 0; c < nF * 3; ++c) {
        if (!m_.falive[CMesh::faceOf(c)] || !m_.E[c]) continue;
        const int a = m_.V[CMesh::nxt(c)], b = m_.V[CMesh::prv(c)];
        if (std::find(featNb[a].begin(), featNb[a].end(), b) == featNb[a].end()) featNb[a].push_back(b);
        if (std::find(featNb[b].begin(), featNb[b].end(), a) == featNb[b].end()) featNb[b].push_back(a);
    }
    std::vector<uint8_t> ftype(nVs, 0);   // 0 free, 1 crease (line), 2 corner (point)
    for (int v : ids_) {
        if (featNb[v].empty()) continue;
        if (featNb[v].size() != 2 || m_.vtype[v] == V_FIXED) { ftype[v] = 2; continue; }
        ftype[v] = 1;
        const Vec3 d0 = featNb[v][1] == featNb[v][0] ? Vec3() : m_.P[featNb[v][1]] - m_.P[featNb[v][0]];
        Vec3 d = d0 - N_[v] * dot(N_[v], d0);
        if (length(d) < 1e-12) { ftype[v] = 2; continue; }
        d = normalized(d);
        if (std::fabs(dot(d, Q_[v])) >= std::fabs(dot(d, R_[v]))) Q_[v] = dot(d, Q_[v]) >= 0 ? d : -d;
        else { const Vec3 q = cross(N_[v], d); Q_[v] = dot(q, Q_[v]) >= 0 ? q : -q; }
        R_[v] = cross(N_[v], Q_[v]);
    }
    featFlag_ = ftype;
    if (!prm_.constrainLattice && !prm_.alignFeatures) std::fill(featFlag_.begin(), featFlag_.end(), 0);
    // adjacency
    {
        adjStart_.assign(nVs + 1, 0);
        std::vector<CMesh::Nb> nb;
        std::vector<int> scratch;
        std::vector<std::vector<int>> tmp(nVs);
        for (int v : ids_) {
            m_.neighbors(v, nb, scratch);
            for (const auto& e : nb) tmp[v].push_back(e.v);
        }
        for (int v = 0; v < nVs; ++v) adjStart_[v + 1] = adjStart_[v] + static_cast<int>(tmp[v].size());
        adj_.reserve(adjStart_.back());
        for (int v = 0; v < nVs; ++v) adj_.insert(adj_.end(), tmp[v].begin(), tmp[v].end());
    }
    prepared_ = true;
    } else {
        for (int v : ids_) O_[v] = m_.P[v];
    }
    auto t1 = clock::now();
    st.fieldSeconds = secs(t0, t1);

    // ---- 2. position field ----------------------------------------------------
    ParamResult& pr = pr_;
    pr.uv.clear(); pr.singular.clear();
    bool paramOk = false;
    if (prm_.useParam) {
        FaceFrames pfr = buildFrames(m_);
        bool hasDens = false;
        for (int v : ids_) if (std::fabs(m_.vdens[v] - 1.0f) > 1e-3f) { hasDens = true; break; }
        if (std::getenv("FORGE_DBG")) { float lo = 1e9f, hi = -1e9f; for (int v : ids_) { lo = std::min(lo, m_.vdens[v]); hi = std::max(hi, m_.vdens[v]); } std::fprintf(stderr, "vdens range %.3f..%.3f hasDens %d pow %.2f\n", lo, hi, hasDens, densPow_); }
        std::vector<std::pair<double, double>> tried;   // (density power, fit) of the attempts that were too poor
        for (int attempt = 0; attempt < 5; ++attempt) {
            IntegerParam::Options po;
            po.alignFeatures = prm_.alignFeatures;
            po.densityPower = densPow_;
            po.timeLimit = prm_.paramTimeLimit;
            po.threads = prm_.threads;
            if (paramRuns_++ == 0) po.progress = prm_.progress;
            IntegerParam ip(m_, pfr, zf_, po);
            paramOk = ip.run(rho, pr);
            // a size gradient the surface cannot follow (it needs singularities the field does not have)
            // makes the fit fail: follow only part of it instead
            if (!paramOk || !hasDens || densPow_ <= 0.0 || pr.faceError <= 0.45) break;
            if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: density fit %.3f too poor at power %.2f, softening\n", pr.faceError, densPow_);
            tried.push_back({densPow_, pr.faceError});
            densPow_ = densPow_ > 0.3 ? densPow_ * 0.5 : 0.0;
            pr.warm.clear(); pr.warmRho = 0;
        }
        // The 0.45 limit is absolute, but a model with many constrained borders (zones, creases) can already sit above
        // it with little or no density. Then judge the density by what it adds: if a larger power costs only a little
        // more than the plain fit, use it.
        if (paramOk && hasDens && !tried.empty()) {
            const double base = pr.faceError;
            for (const auto& t : tried) {
                if (t.second <= base * 1.12 + 0.02) {
                    if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: plain fit %.3f, taking density power %.2f (fit %.3f)\n", base, t.first, t.second);
                    densPow_ = t.first;
                    IntegerParam::Options po;
                    po.alignFeatures = prm_.alignFeatures;
                    po.densityPower = densPow_;
                    po.timeLimit = prm_.paramTimeLimit;
                    po.threads = prm_.threads;
                    ParamResult again;
                    IntegerParam ip(m_, pfr, zf_, po);
                    if (ip.run(rho, again)) pr = again; else densPow_ = std::max(0.0, densPow_ * 0.5);
                    break;
                }
            }
        }
        if (paramOk && pr.badSeams > 0 && ((prm_.seamRetry && !std::getenv("FORGE_NORETRY")) || std::getenv("FORGE_RETRY"))) {
            // a few cut junctions did not close (integer defect): a slightly different cell size usually rounds cleanly.
            // Off by default: on the hand and the full plate the result was identical or worse and took 2-3x as long
            ParamResult bestPr = pr;
            double bestRho = rho;
            const double variants[2] = {1.012, 0.988};
            for (int vi = 0; vi < 2 && bestPr.badSeams > 0; ++vi) {
                ParamResult alt = pr;
                IntegerParam::Options po;
                po.alignFeatures = prm_.alignFeatures;
                po.densityPower = densPow_;
                po.timeLimit = prm_.paramTimeLimit;
                po.threads = prm_.threads;
                IntegerParam ip2(m_, pfr, zf_, po);
                const double r2 = rho * variants[vi];
                const bool ok2 = ip2.run(r2, alt);
                if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: retry rho x%.3f: ok %d bad seams %d (was %d)\n", variants[vi], ok2, ok2 ? alt.badSeams : -1, bestPr.badSeams);
                if (ok2 && alt.badSeams < bestPr.badSeams && alt.faceError <= 0.5) { bestPr = std::move(alt); bestRho = r2; }
            }
            pr = std::move(bestPr);
            rho = bestRho;
        }
        if (!paramOk && pr.error == "time limit") { err = "the grid solve took longer than the time limit"; return false; }
        if (paramOk && hasDens && pr.faceError > 0.5) {
            err = "the size variation is too strong for a regular grid on this shape";
            return false;
        }
        if (std::getenv("FORGE_DBG")) std::fprintf(stderr, "param: ok %d '%s' sing %zu cut %d faceErr %.3f seamErr %.1e solves %d\n", paramOk, pr.error.c_str(), pr.singular.size(), pr.cutEdges, pr.faceError, pr.seamError, pr.solves);
    }
    report(78, "Optimising the quad grid");
    std::vector<int> order = ids_;
    std::mt19937 rng(12345);
    std::shuffle(order.begin(), order.end(), rng);
    auto bestFrame = [&](int i, int j, Vec3& qj, Vec3& rj) {
        // the rotation of j's axes closest to i's axes
        const Vec3 cand[4] = {Q_[j], R_[j], -Q_[j], -R_[j]};
        int best = 0; double bd = -2;
        for (int k = 0; k < 4; ++k) { const double d = dot(cand[k], Q_[i]); if (d > bd) { bd = d; best = k; } }
        qj = cand[best];
        rj = cross(N_[j], qj);
    };
    auto fixO = [&](int j, Vec3 o) {
        if (featFlag_[j] == 2) return m_.P[j];
        const Vec3 d0 = o - m_.P[j];
        const Vec3 d = d0 - N_[j] * dot(N_[j], d0);
        if (featFlag_[j] == 1) {
            const double a = std::round(dot(d, Q_[j]) / rho);
            return m_.P[j] + Q_[j] * (dot(d, Q_[j]) - a * rho);
        }
        const double a = std::round(dot(d, Q_[j]) / rho), b = std::round(dot(d, R_[j]) / rho);
        return m_.P[j] + d - Q_[j] * (a * rho) - R_[j] * (b * rho);
    };
    {   // initial guess: propagate the lattice along a breadth first tree
        std::vector<uint8_t> seen(nVs, 0);
        std::vector<int> queue;
        for (int s : order) {
            if (seen[s]) continue;
            seen[s] = 1; queue.assign(1, s);
            for (size_t h = 0; h < queue.size(); ++h) {
                const int i = queue[h];
                for (int e = adjStart_[i]; e < adjStart_[i + 1]; ++e) {
                    const int j = adj_[e];
                    if (seen[j]) continue;
                    seen[j] = 1; queue.push_back(j);
                    Vec3 qj, rj;
                    bestFrame(i, j, qj, rj);
                    const Vec3 mid = (m_.P[i] + m_.P[j]) * 0.5;
                    const Vec3 ti = node(O_[i], Q_[i], R_[i], mid);
                    const Vec3 tj = node(O_[j], qj, rj, ti);
                    O_[j] = fixO(j, O_[j] + (ti - tj));
                }
            }
        }
    }
    if (getenv("FORGE_DBG")) {
        auto rotIdx = [&](int i, int j) {
            const Vec3 cand[4] = {Q_[j], R_[j], -Q_[j], -R_[j]};
            int best = 0; double bd = -2;
            for (int k = 0; k < 4; ++k) { const double d = dot(cand[k], Q_[i]); if (d > bd) { bd = d; best = k; } }
            return best;
        };
        int sing = 0, faces = 0;
        singC_.clear();
        for (int f = 0; f < nF; ++f) {
            if (!m_.falive[f]) continue;
            ++faces;
            const int a = m_.V[3*f], b = m_.V[3*f+1], c = m_.V[3*f+2];
            const int r = rotIdx(a, b) + rotIdx(b, c) + rotIdx(c, a);
            // r_ij is the rotation taking j to i; going around the triangle the sum must be 0 mod 4
            if (r % 4 != 0) { ++sing; singC_.push_back((m_.P[a] + m_.P[b] + m_.P[c]) / 3.0); }
        }
        std::fprintf(stderr, "field singular faces %d of %d\n", sing, faces);
    }
    for (int it = 0; it < (paramOk ? 0 : prm_.iterations); ++it) {
        double resid = 0; long cnt = 0;
        for (int idx = 0; idx < static_cast<int>(order.size()); ++idx) {
            const int i = (it & 1) ? order[order.size() - 1 - idx] : order[idx];
            Vec3 sum; double ws = 0;
            for (int e = adjStart_[i]; e < adjStart_[i + 1]; ++e) {
                const int j = adj_[e];
                Vec3 qj, rj;
                bestFrame(i, j, qj, rj);
                const Vec3 mid = (m_.P[i] + m_.P[j]) * 0.5;
                const Vec3 ti = node(O_[i], Q_[i], R_[i], mid);
                const Vec3 tj = node(O_[j], qj, rj, ti);
                sum += O_[i] + (tj - ti);
                ws += 1;
                resid += length(tj - ti); ++cnt;
            }
            if (ws == 0) continue;
            O_[i] = fixO(i, sum / ws);
        }
        st.residual = cnt ? resid / (cnt * rho) : 0;
        if (getenv("FORGE_DBG") && (it < 5 || it % 20 == 0)) std::fprintf(stderr, "it %d resid %.4f\n", it, st.residual);
        if (it % 10 == 0) report(78 + (it * 12) / std::max(1, prm_.iterations), "Optimising the quad grid");
    }
    auto t2 = clock::now();
    st.positionSeconds = secs(t1, t2);

    // ---- 3. snap and merge -----------------------------------------------------
    report(91, "Extracting quads");
    std::vector<Vec3> S(nVs);
    for (int v : ids_) S[v] = node(O_[v], Q_[v], R_[v], m_.P[v]);
    std::vector<int> dsu(nVs);
    std::iota(dsu.begin(), dsu.end(), 0);
    std::vector<int> cellIds = ids_;          // vertices that can claim a lattice point (the mesh vertices, plus virtual ones)
    std::vector<Vec3> exN;                    // normals of the virtual vertices (index v - nVs)
    auto find = [&](int x) { while (dsu[x] != x) { dsu[x] = dsu[dsu[x]]; x = dsu[x]; } return x; };
    struct Cand { int a, b; };
    std::vector<Cand> links;
    if (paramOk) {
        for (int v : ids_) {
            const int c = m_.vcorner[v];
            const int f = c / 3, k = c % 3;
            const double u = pr.uv[f][k][0], w = pr.uv[f][k][1];
            double rl = rho * (m_.vdens.empty() ? 1.0 : static_cast<double>(m_.vdens[v]));
            if (pr.densityPower != 1.0 && !m_.vdens.empty()) rl = rho * std::pow(static_cast<double>(m_.vdens[v]), pr.densityPower);
            S[v] = m_.P[v] + pr.U[f] * ((std::round(u) - u) * rl) + pr.V[f] * ((std::round(w) - w) * rl);
        }
        // Where the parametrization stretches the grid, the mesh has fewer vertices than the lattice has points and
        // some points would get no cell (holes). Subdivide the triangles whose edges span a lattice step; the
        // map is linear inside a triangle, so the new vertices need no new solve.
        struct Tri { int v[3]; double uv[3][2]; int f; };
        std::vector<Tri> tris;
        for (int f = 0; f < nF; ++f) {
            if (!m_.falive[f]) continue;
            Tri t;
            t.f = f;
            for (int k = 0; k < 3; ++k) { t.v[k] = m_.V[3 * f + k]; t.uv[k][0] = pr.uv[f][k][0]; t.uv[k][1] = pr.uv[f][k][1]; }
            tris.push_back(t);
        }
        std::vector<Vec3> exP;
        std::vector<double> exScale;
        std::unordered_map<uint64_t, int> midOf;
        auto ekey2 = [](int a, int b) { return (static_cast<uint64_t>(static_cast<uint32_t>(std::min(a, b))) << 32) | static_cast<uint32_t>(std::max(a, b)); };
        auto posOf = [&](int v) { return v < nVs ? m_.P[v] : exP[v - nVs]; };
        auto nrmOf = [&](int v) { return v < nVs ? N_[v] : exN[v - nVs]; };
        auto scaleOf = [&](int v) {
            double sc = 1.0;
            if (v < nVs) { if (!m_.vdens.empty()) sc = m_.vdens[v]; } else sc = exScale[v - nVs];
            if (pr.densityPower != 1.0) sc = std::pow(sc, pr.densityPower);
            return sc;
        };
        int addedTris = 0;
        if (!std::getenv("FORGE_NOSUBDIV")) {
            for (int round = 0; round < 3; ++round) {
                std::unordered_set<uint64_t> split;
                for (const Tri& t : tris)
                    for (int k = 0; k < 3; ++k) {
                        const int k2 = (k + 1) % 3;
                        if (std::max(std::fabs(t.uv[k2][0] - t.uv[k][0]), std::fabs(t.uv[k2][1] - t.uv[k][1])) > 0.8) split.insert(ekey2(t.v[k], t.v[k2]));
                    }
                if (split.empty()) break;
                std::vector<Tri> next;
                next.reserve(tris.size() + tris.size() / 8);
                for (const Tri& t : tris) {
                    int m[3] = {-1, -1, -1};
                    double muv[3][2];
                    int nsplit = 0;
                    for (int k = 0; k < 3; ++k) {
                        const int k2 = (k + 1) % 3;
                        const uint64_t key = ekey2(t.v[k], t.v[k2]);
                        if (!split.count(key)) continue;
                        ++nsplit;
                        muv[k][0] = 0.5 * (t.uv[k][0] + t.uv[k2][0]);
                        muv[k][1] = 0.5 * (t.uv[k][1] + t.uv[k2][1]);
                        auto it = midOf.find(key);
                        if (it == midOf.end()) {
                            Vec3 pm = (posOf(t.v[k]) + posOf(t.v[k2])) * 0.5;
                            Vec3 nm = normalized(nrmOf(t.v[k]) + nrmOf(t.v[k2]));
                            const auto h = ref_.closest(pm);
                            if (h.tri >= 0) pm = h.p;
                            exP.push_back(pm); exN.push_back(nm);
                            exScale.push_back(0.5 * ((t.v[k] < nVs ? (m_.vdens.empty() ? 1.0 : m_.vdens[t.v[k]]) : exScale[t.v[k] - nVs]) + (t.v[k2] < nVs ? (m_.vdens.empty() ? 1.0 : m_.vdens[t.v[k2]]) : exScale[t.v[k2] - nVs])));
                            const int id = nVs + static_cast<int>(exP.size()) - 1;
                            S.push_back(Vec3());
                            dsu.push_back(id);
                            cellIds.push_back(id);
                            // snapped position from this triangle's frame
                            const double rl = rho * scaleOf(id);
                            S[id] = pm + pr.U[t.f] * ((std::round(muv[k][0]) - muv[k][0]) * rl) + pr.V[t.f] * ((std::round(muv[k][1]) - muv[k][1]) * rl);
                            it = midOf.emplace(key, id).first;
                        }
                        m[k] = it->second;
                    }
                    auto push = [&](int a, double ua, double va, int b, double ub, double vb, int c, double uc, double vc) {
                        Tri n; n.f = t.f; n.v[0] = a; n.v[1] = b; n.v[2] = c;
                        n.uv[0][0] = ua; n.uv[0][1] = va; n.uv[1][0] = ub; n.uv[1][1] = vb; n.uv[2][0] = uc; n.uv[2][1] = vc;
                        next.push_back(n);
                    };
                    if (nsplit == 0) { next.push_back(t); continue; }
                    // rotate so that the pattern is canonical: the unsplit (or the only split) edge is edge 0
                    int r = 0;
                    if (nsplit == 1) { while (m[r] < 0) ++r; }
                    else if (nsplit == 2) { while (m[(r + 2) % 3] >= 0 || m[r] < 0 || m[(r + 1) % 3] < 0) ++r; }
                    const int i0 = r, i1 = (r + 1) % 3, i2 = (r + 2) % 3;
                    const int V0 = t.v[i0], V1 = t.v[i1], V2 = t.v[i2];
                    const double* U0 = t.uv[i0]; const double* U1 = t.uv[i1]; const double* U2 = t.uv[i2];
                    const int M0 = m[i0], M1 = m[i1], M2 = m[i2];
                    const double* Mu0 = muv[i0]; const double* Mu1 = muv[i1]; const double* Mu2 = muv[i2];
                    if (nsplit == 3) {
                        push(V0, U0[0], U0[1], M0, Mu0[0], Mu0[1], M2, Mu2[0], Mu2[1]);
                        push(M0, Mu0[0], Mu0[1], V1, U1[0], U1[1], M1, Mu1[0], Mu1[1]);
                        push(M2, Mu2[0], Mu2[1], M1, Mu1[0], Mu1[1], V2, U2[0], U2[1]);
                        push(M0, Mu0[0], Mu0[1], M1, Mu1[0], Mu1[1], M2, Mu2[0], Mu2[1]);
                    } else if (nsplit == 2) {      // edges 0 and 1 are split, edge 2 is not
                        push(M0, Mu0[0], Mu0[1], V1, U1[0], U1[1], M1, Mu1[0], Mu1[1]);
                        push(V0, U0[0], U0[1], M0, Mu0[0], Mu0[1], M1, Mu1[0], Mu1[1]);
                        push(V0, U0[0], U0[1], M1, Mu1[0], Mu1[1], V2, U2[0], U2[1]);
                    } else {                       // only edge 0 is split
                        push(V0, U0[0], U0[1], M0, Mu0[0], Mu0[1], V2, U2[0], U2[1]);
                        push(M0, Mu0[0], Mu0[1], V1, U1[0], U1[1], V2, U2[0], U2[1]);
                    }
                }
                addedTris += static_cast<int>(next.size()) - static_cast<int>(tris.size());
                tris.swap(next);
            }
        }
        if (getenv("FORGE_DBG")) std::fprintf(stderr, "lattice subdivision: %zu virtual vertices, %d extra triangles\n", exP.size(), addedTris);
        for (const Tri& t : tris) {
            for (int k = 0; k < 3; ++k) {
                const int k2 = (k + 1) % 3;
                const int a = t.v[k], b = t.v[k2];
                const int da = static_cast<int>(std::lround(t.uv[k2][0])) - static_cast<int>(std::lround(t.uv[k][0]));
                const int db = static_cast<int>(std::lround(t.uv[k2][1])) - static_cast<int>(std::lround(t.uv[k][1]));
                if (da == 0 && db == 0) dsu[find(a)] = find(b);
                else if (std::abs(da) + std::abs(db) == 1) links.push_back({a, b});
            }
        }
    } else
    for (int i : ids_) {
        for (int e = adjStart_[i]; e < adjStart_[i + 1]; ++e) {
            const int j = adj_[e];
            if (j < i) continue;
            const Vec3 d = S[j] - S[i];
            const double dl = length(d);
            if (dl < kMerge * rho) { dsu[find(i)] = find(j); continue; }
            const double a = dot(d, Q_[i]) / rho, b = dot(d, R_[i]) / rho;
            const int ra = static_cast<int>(std::lround(a)), rb = static_cast<int>(std::lround(b));
            if (std::abs(ra) + std::abs(rb) == 1 && dl > kLo * rho && dl < kHi * rho) links.push_back({i, j});
        }
    }
    std::unordered_map<int, int> cellOf;
    std::vector<int> cellVerts;       // representative vertex per cell
    std::vector<Vec3> cpos, cn;
    std::vector<int> cnt;
    std::vector<uint8_t> cfeat;
    std::vector<int> ccorner;
    for (int v : cellIds) {
        const int r = find(v);
        auto it = cellOf.find(r);
        int c;
        if (it == cellOf.end()) {
            c = static_cast<int>(cellVerts.size());
            cellOf[r] = c;
            cellVerts.push_back(v);
            cpos.push_back(Vec3());
            cn.push_back(Vec3());
            cnt.push_back(0);
            cfeat.push_back(0);
            ccorner.push_back(-1);
        } else c = it->second;
        cpos[c] += S[v];
        cn[c] += (v < nVs ? N_[v] : exN[v - nVs]);
        cnt[c]++;
        if (v < nVs && featFlag_[v] > cfeat[c]) cfeat[c] = featFlag_[v];
        if (v < nVs && featFlag_[v] == 2 && ccorner[c] < 0) ccorner[c] = v;
    }
    const int nC = static_cast<int>(cellVerts.size());
    for (int c = 0; c < nC; ++c) {
        cpos[c] = cpos[c] / cnt[c];
        cn[c] = normalized(cn[c]);
        if (cfeat[c] == 2 && ccorner[c] >= 0) cpos[c] = m_.P[ccorner[c]];
        else if (cfeat[c] >= 1 && !feat_.empty()) { const auto h = feat_.closest(cpos[c]); if (h.seg >= 0) cpos[c] = h.p; }
    }
    st.cells = nC;
    if (getenv("FORGE_DBG")) { int c2 = 0, c1 = 0, c2ok = 0; for (int c = 0; c < nC; ++c) { if (cfeat[c] == 2) { ++c2; if (ccorner[c] >= 0) ++c2ok; } else if (cfeat[c] == 1) ++c1; } std::fprintf(stderr, "feature cells: corner %d (with vertex %d) crease %d\n", c2, c2ok, c1); }
    std::vector<std::vector<int>> nbr(nC);
    {
        std::unordered_set<uint64_t> seen;
        for (const auto& l : links) {
            const int a = cellOf[find(l.a)], b = cellOf[find(l.b)];
            if (a == b) continue;
            const uint64_t key = (static_cast<uint64_t>(std::min(a, b)) << 32) | static_cast<uint32_t>(std::max(a, b));
            if (!seen.insert(key).second) continue;
            nbr[a].push_back(b);
            nbr[b].push_back(a);
            st.edges++;
        }
    }
    // prune dangling cells
    {
        std::vector<int> stack;
        std::vector<uint8_t> dead(nC, 0);
        for (int c = 0; c < nC; ++c) if (nbr[c].size() < 2) stack.push_back(c);
        while (!stack.empty()) {
            const int c = stack.back(); stack.pop_back();
            if (dead[c]) continue;
            dead[c] = 1;
            for (int d : nbr[c]) {
                auto& v = nbr[d];
                v.erase(std::remove(v.begin(), v.end(), c), v.end());
                if (!dead[d] && v.size() < 2) stack.push_back(d);
            }
            nbr[c].clear();
        }
    }

    // ---- 4. faces from the rotation system -------------------------------------
    // sort neighbours counter clockwise around the cell normal
    for (int c = 0; c < nC; ++c) {
        if (nbr[c].empty()) continue;
        const Vec3 n = cn[c];
        const Vec3 helper = std::fabs(n.x) < 0.9 ? Vec3(1, 0, 0) : Vec3(0, 1, 0);
        const Vec3 t1 = normalized(cross(n, helper)), t2 = cross(n, t1);
        std::vector<std::pair<double, int>> key;
        for (int d : nbr[c]) {
            const Vec3 e = cpos[d] - cpos[c];
            key.push_back({std::atan2(dot(e, t2), dot(e, t1)), d});
        }
        std::sort(key.begin(), key.end());
        for (size_t k = 0; k < key.size(); ++k) nbr[c][k] = key[k].second;
    }
    auto posIn = [&](int c, int d) {
        const auto& v = nbr[c];
        return static_cast<int>(std::find(v.begin(), v.end(), d) - v.begin());
    };
    // visited half edges: key (a, index of b in nbr[a])
    std::vector<std::vector<uint8_t>> used(nC);
    for (int c = 0; c < nC; ++c) used[c].assign(nbr[c].size(), 0);
    std::vector<std::vector<int>> faces;
    for (int a = 0; a < nC; ++a) {
        for (size_t s = 0; s < nbr[a].size(); ++s) {
            if (used[a][s]) continue;
            std::vector<int> loop;
            int u = a, k = static_cast<int>(s);
            bool ok = true;
            for (int guard = 0; guard < 64; ++guard) {
                if (used[u][k]) { ok = false; break; }
                used[u][k] = 1;
                loop.push_back(u);
                const int v = nbr[u][k];
                // at v, continue with the neighbour just before u in counter clockwise order
                const int pu = posIn(v, u);
                const int deg = static_cast<int>(nbr[v].size());
                const int nk = (pu + deg - 1) % deg;
                u = v; k = nk;
                if (u == a && k == static_cast<int>(s)) break;
                if (guard == 63) ok = false;
            }
            if (!ok || loop.size() < 3) { st.droppedLoops++; if (getenv("FORGE_DBG")) std::fprintf(stderr, "dropped loop (open walk, %zu cells) at (%.1f,%.1f,%.1f)\n", loop.size(), cpos[a].x, cpos[a].y, cpos[a].z); continue; }
            // keep counter clockwise loops only
            Vec3 nsum, ctr;
            for (int c : loop) ctr += cpos[c];
            ctr = ctr / static_cast<double>(loop.size());
            double area = 0;
            for (size_t q = 0; q < loop.size(); ++q) {
                const Vec3 p0 = cpos[loop[q]] - ctr, p1 = cpos[loop[(q + 1) % loop.size()]] - ctr;
                nsum += cross(p0, p1);
            }
            Vec3 navg;
            for (int c : loop) navg += cn[c];
            area = dot(nsum, navg);
            if (area <= 0 || loop.size() > 8) { st.droppedLoops++; if (getenv("FORGE_DBG")) std::fprintf(stderr, "dropped loop (%s, %zu cells) at (%.1f,%.1f,%.1f)\n", area <= 0 ? "reversed" : "long", loop.size(), cpos[loop[0]].x, cpos[loop[0]].y, cpos[loop[0]].z); continue; }
            faces.push_back(std::move(loop));
        }
    }

    if (getenv("FORGE_DBG")) {
        std::vector<int> deg(nC, 0);
        for (const auto& f : faces) for (int c : f) deg[c]++;
        int irr = 0, near1 = 0, near2 = 0, near4 = 0;
        for (int c = 0; c < nC; ++c) {
            if (!deg[c] || deg[c] == 4) continue;
            ++irr;
            double md = 1e300;
            for (const auto& p : singC_) md = std::min(md, distance(p, cpos[c]));
            if (md < rho) ++near1;
            if (md < 2 * rho) ++near2;
            if (md < 4 * rho) ++near4;
        }
        int irrF = 0;
        for (const auto& f : faces) if (f.size() != 4) ++irrF;
        std::fprintf(stderr, "irregular cells %d (<1rho %d, <2rho %d, <4rho %d) nonquad faces %d singular %zu\n", irr, near1, near2, near4, irrF, singC_.size());
    }
    // fill the small holes left by loops that did not close (never along real borders)
    {
        std::unordered_map<uint64_t, int> hasEdge;
        auto ekey = [](int a, int b) { return (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b); };
        for (const auto& f : faces) for (size_t k = 0; k < f.size(); ++k) hasEdge[ekey(f[k], f[(k + 1) % f.size()])] = 1;
        std::unordered_map<int, std::vector<int>> out_;   // boundary edges by start cell
        for (const auto& f : faces)
            for (size_t k = 0; k < f.size(); ++k) {
                const int a = f[k], b = f[(k + 1) % f.size()];
                if (!hasEdge.count(ekey(b, a))) out_[a].push_back(b);
            }
        std::unordered_set<uint64_t> done;
        const int maxHole = 64;
        std::vector<std::vector<int>> extra;
        for (auto& kv : out_) {
            for (int b0 : kv.second) {
                const int a0 = kv.first;
                if (done.count(ekey(a0, b0))) continue;
                std::vector<int> loop{a0};
                int a = a0, b = b0;
                bool ok = true;
                for (int guard = 0; guard < maxHole + 1; ++guard) {
                    done.insert(ekey(a, b));
                    if (b == a0) break;
                    loop.push_back(b);
                    auto it = out_.find(b);
                    int nx = -1;
                    if (it != out_.end()) for (int c : it->second) if (!done.count(ekey(b, c))) { nx = c; break; }
                    if (nx < 0) { ok = false; break; }
                    a = b; b = nx;
                    if (guard == maxHole) ok = false;
                }
                if (!ok || loop.size() < 3 || static_cast<int>(loop.size()) > maxHole) continue;
                std::reverse(loop.begin(), loop.end());
                extra.push_back(std::move(loop));
            }
        }
        if (getenv("FORGE_DBG")) for (auto& l : extra) { std::vector<int> t = l; std::sort(t.begin(), t.end()); const int d = static_cast<int>(std::unique(t.begin(), t.end()) - t.begin()); std::fprintf(stderr, "hole loop %zu cells, %d distinct\n", l.size(), d); }
        for (auto& l : extra) { faces.push_back(std::move(l)); st.filled++; }
        if (getenv("FORGE_DBG")) {
            std::unordered_map<uint64_t, int> he2;
            for (const auto& f : faces) for (size_t k = 0; k < f.size(); ++k) he2[ekey(f[k], f[(k + 1) % f.size()])] = 1;
            int bnd = 0, nonman = 0;
            std::unordered_map<int, int> outdeg;
            for (const auto& f : faces) for (size_t k = 0; k < f.size(); ++k) {
                const int a = f[k], b = f[(k + 1) % f.size()];
                if (!he2.count(ekey(b, a))) { ++bnd; outdeg[a]++; }
            }
            for (auto& kv : outdeg) if (kv.second > 1) ++nonman;
            std::fprintf(stderr, "after filling: boundary edges %d, boundary vertices with several out edges %d\n", bnd, nonman);
        }
    }
    // ---- 5. relax the cell positions on the surface ----------------------------
    std::vector<uint8_t> inFace(nC, 0);
    for (const auto& f : faces) for (int c : f) inFace[c] = 1;
    for (int it = 0; it < prm_.smooth; ++it) {
        std::vector<Vec3> np = cpos;
        for (int c = 0; c < nC; ++c) {
            if (!inFace[c] || nbr[c].empty()) continue;
            Vec3 avg;
            int k = 0;
            for (int d : nbr[c]) if (inFace[d] && (cfeat[c] == 0 || cfeat[d] >= 1)) { avg += cpos[d]; ++k; }
            if (!k) continue;
            avg = avg / k;
            Vec3 p = cpos[c] + (avg - cpos[c]) * 0.5;
            if (cfeat[c] == 2) continue;
            if (cfeat[c] == 1) { const auto h = feat_.closest(p); np[c] = h.seg >= 0 ? h.p : p; continue; }
            const auto h = ref_.closest(p, &cn[c], 0.0);
            np[c] = h.tri >= 0 ? h.p : p;
        }
        cpos.swap(np);
    }
    // ---- output ------------------------------------------------------------------
    std::vector<int> remap(nC, -1);
    out.P.clear(); out.sizes.clear(); out.idx.clear(); out.features.clear(); out.density.clear();
    for (const auto& f : faces) {
        for (int c : f) {
            if (remap[c] < 0) { remap[c] = static_cast<int>(out.P.size()); out.P.push_back(cpos[c]); }
        }
        out.sizes.push_back(static_cast<int>(f.size()));
        for (int c : f) out.idx.push_back(remap[c]);
        if (f.size() == 4) st.quads++; else if (f.size() == 3) st.tris++; else st.other++;
    }
    if (prm_.subdivide && !faces.empty()) {
        PolyMesh sub;
        sub.P = out.P;
        std::vector<uint8_t> subFeat(out.P.size(), 0);
        for (int c = 0; c < nC; ++c) if (remap[c] >= 0) subFeat[remap[c]] = cfeat[c];
        std::unordered_map<uint64_t, int> mid;
        auto midpoint = [&](int a, int b) {
            const uint64_t key = (static_cast<uint64_t>(std::min(a, b)) << 32) | static_cast<uint32_t>(std::max(a, b));
            auto it = mid.find(key);
            if (it != mid.end()) return it->second;
            const int id = static_cast<int>(sub.P.size());
            const Vec3 mp = (out.P[a] + out.P[b]) * 0.5;
            sub.P.push_back(mp);
            uint8_t ff = 0;
            if (subFeat[a] >= 1 && subFeat[b] >= 1 && !feat_.empty()) {
                const auto h = feat_.closest(mp);
                if (h.seg >= 0 && distance(h.p, mp) < 0.15 * rho) ff = 1;
            }
            subFeat.push_back(ff);
            mid[key] = id;
            return id;
        };
        size_t at = 0;
        for (int sz : out.sizes) {
            Vec3 ctr;
            for (int k = 0; k < sz; ++k) ctr += out.P[out.idx[at + k]];
            const int cid = static_cast<int>(sub.P.size());
            sub.P.push_back(ctr / static_cast<double>(sz));
            subFeat.push_back(0);
            for (int k = 0; k < sz; ++k) {
                const int a = out.idx[at + k], b = out.idx[at + (k + 1) % sz], z = out.idx[at + (k + sz - 1) % sz];
                sub.sizes.push_back(4);
                sub.idx.push_back(a);
                sub.idx.push_back(midpoint(a, b));
                sub.idx.push_back(cid);
                sub.idx.push_back(midpoint(z, a));
            }
            at += sz;
        }
        // relax: average neighbours, snap back to the surface
        const int nv = static_cast<int>(sub.P.size());
        std::vector<std::vector<int>> nb(nv);
        for (size_t q = 0; q + 3 < sub.idx.size() + 0; q += 4)
            for (int k = 0; k < 4; ++k) {
                const int a = sub.idx[q + k], b = sub.idx[q + (k + 1) % 4];
                if (std::find(nb[a].begin(), nb[a].end(), b) == nb[a].end()) nb[a].push_back(b);
                if (std::find(nb[b].begin(), nb[b].end(), a) == nb[b].end()) nb[b].push_back(a);
            }
        if (!feat_.empty()) {
            // marked edges: every vertex close to a feature line joins it
            const double snapDist = 0.3 * rho;
            for (size_t v = 0; v < sub.P.size(); ++v) {
                const auto h = feat_.closest(sub.P[v]);
                if (subFeat[v] == 2) continue;
                if (h.seg >= 0 && distance(h.p, sub.P[v]) < snapDist) { sub.P[v] = h.p; subFeat[v] = 1; }
            }
        }
        auto relaxSub = [&](int iters) {
            for (int it = 0; it < iters; ++it) {
                std::vector<Vec3> np = sub.P;
                for (int v = 0; v < nv; ++v) {
                    if (nb[v].empty()) continue;
                    if (subFeat[v] == 2) continue;
                    Vec3 avg;
                    int k = 0;
                    for (int w : nb[v]) if (subFeat[v] == 0 || subFeat[w] >= 1) { avg += sub.P[w]; ++k; }
                    if (!k) continue;
                    avg = avg / static_cast<double>(k);
                    const Vec3 p = sub.P[v] + (avg - sub.P[v]) * 0.6;
                    if (subFeat[v] == 1) { const auto h = feat_.closest(p); np[v] = h.seg >= 0 ? h.p : p; continue; }
                    const auto h = ref_.closest(p);
                    np[v] = h.tri >= 0 ? h.p : p;
                }
                sub.P.swap(np);
            }
        };
        relaxSub(prm_.finalSmooth);
        const double optMoves = prm_.poleMoves;
        if (optMoves > 0) {
            std::vector<std::array<int, 4>> F4(sub.idx.size() / 4);
            for (size_t q = 0; q < F4.size(); ++q) F4[q] = {sub.idx[4*q], sub.idx[4*q+1], sub.idx[4*q+2], sub.idx[4*q+3]};
            QuadOptStats qo;
            optimizePoles(sub.P, F4, optMoves, 0.15, 7, qo);
            for (size_t q = 0; q < F4.size(); ++q) for (int k = 0; k < 4; ++k) sub.idx[4*q+k] = F4[q][k];
            if (getenv("FORGE_DBG")) std::fprintf(stderr, "pole opt: moves %d cost %d -> %d\n", qo.moves, qo.poleCostBefore, qo.poleCostAfter);
            // neighbours changed
            for (auto& v : nb) v.clear();
            for (size_t q = 0; q < F4.size(); ++q)
                for (int k = 0; k < 4; ++k) {
                    const int a = F4[q][k], b = F4[q][(k + 1) % 4];
                    if (std::find(nb[a].begin(), nb[a].end(), b) == nb[a].end()) nb[a].push_back(b);
                    if (std::find(nb[b].begin(), nb[b].end(), a) == nb[b].end()) nb[b].push_back(a);
                }
            relaxSub(prm_.finalSmooth);
        }
        for (int mergeRound = 0; mergeRound < (std::getenv("FORGE_MROUNDS") ? std::atoi(std::getenv("FORGE_MROUNDS")) : 6) && !std::getenv("FORGE_NOMERGE2"); ++mergeRound) {   // merge the two quads around a valence-2 (dead-end) vertex into one quad
            const int nvS = static_cast<int>(sub.P.size());
            std::vector<std::array<int, 4>> F4(sub.idx.size() / 4);
            for (size_t q = 0; q < F4.size(); ++q) F4[q] = {sub.idx[4*q], sub.idx[4*q+1], sub.idx[4*q+2], sub.idx[4*q+3]};
            std::vector<std::vector<int>> inc(nvS);
            for (size_t q = 0; q < F4.size(); ++q) for (int k = 0; k < 4; ++k) inc[F4[q][k]].push_back(static_cast<int>(q));
            std::vector<uint8_t> deadQ(F4.size(), 0), deadV(nvS, 0), modQ(F4.size(), 0);
            int merged = 0;
            for (int v = 0; v < nvS; ++v) {
                if (inc[v].size() != 2 || subFeat[v] >= 1) continue;
                const int qa = inc[v][0], qb = inc[v][1];
                if (deadQ[qa] || deadQ[qb] || modQ[qa] || modQ[qb]) continue;
                auto rot = [&](std::array<int, 4> q) { while (q[0] != v) std::rotate(q.begin(), q.begin() + 1, q.end()); return q; };
                const auto A = rot(F4[qa]), B = rot(F4[qb]);
                // A = (v,a,b,c) and B = (v,c,d,a)
                if (A[1] != B[3] || A[3] != B[1]) continue;
                {   // the merged quad must have four different corners and must not collapse onto an existing quad
                    const int c4[4] = {A[1], A[2], A[3], B[2]};
                    bool distinct = true;
                    for (int i1 = 0; i1 < 4; ++i1) for (int i2 = i1 + 1; i2 < 4; ++i2) if (c4[i1] == c4[i2]) distinct = false;
                    if (!distinct) continue;
                }
                F4[qa] = {A[1], A[2], A[3], B[2]};
                deadQ[qb] = 1; deadV[v] = 1; modQ[qa] = 1; ++merged;
            }
            if (!merged) break;
            {
                std::vector<int> remapV(nvS, -1);
                PolyMesh cl;
                std::vector<uint8_t> cf;
                for (int v = 0; v < nvS; ++v) if (!deadV[v]) { remapV[v] = static_cast<int>(cl.P.size()); cl.P.push_back(sub.P[v]); cf.push_back(subFeat[v]); }
                for (size_t q = 0; q < F4.size(); ++q) {
                    if (deadQ[q]) continue;
                    cl.sizes.push_back(4);
                    for (int k = 0; k < 4; ++k) cl.idx.push_back(remapV[F4[q][k]]);
                }
                sub = std::move(cl);
                subFeat = std::move(cf);
                if (getenv("FORGE_DBG")) std::fprintf(stderr, "merged %d dead-end vertices\n", merged);
            }
        }
        if (!std::getenv("FORGE_NOSTAR")) {
            // Stars: a vertex with many spokes (a fan that closed a hole) is replaced by a regular k x l patch of quads
            // whose corners are chosen where the neighbouring valences suffer least.
            const int nvS = static_cast<int>(sub.P.size());
            std::vector<std::array<int, 4>> F4(sub.idx.size() / 4);
            for (size_t q = 0; q < F4.size(); ++q) F4[q] = {sub.idx[4*q], sub.idx[4*q+1], sub.idx[4*q+2], sub.idx[4*q+3]};
            std::vector<std::vector<int>> inc(nvS);
            for (size_t q = 0; q < F4.size(); ++q) for (int k = 0; k < 4; ++k) inc[F4[q][k]].push_back(static_cast<int>(q));
            // boundary vertices of the quad mesh (an edge used once)
            std::unordered_map<uint64_t, int> ecount;
            auto ek = [](int a, int b) { return (static_cast<uint64_t>(static_cast<uint32_t>(std::min(a, b))) << 32) | static_cast<uint32_t>(std::max(a, b)); };
            for (const auto& q : F4) for (int k = 0; k < 4; ++k) ecount[ek(q[k], q[(k + 1) & 3])]++;
            std::vector<uint8_t> onBorder(nvS, 0);
            for (const auto& kv : ecount) if (kv.second == 1) { onBorder[kv.first >> 32] = 1; onBorder[kv.first & 0xffffffffu] = 1; }
            std::vector<int> order(nvS);
            std::iota(order.begin(), order.end(), 0);
            std::sort(order.begin(), order.end(), [&](int a, int b) { return inc[a].size() > inc[b].size(); });
            std::vector<uint8_t> deadQ(F4.size(), 0), deadV(nvS, 0), touched(nvS, 0);
            std::vector<std::array<int, 4>> newQ;
            std::vector<Vec3> newP;
            std::vector<uint8_t> newFeat;
            int replaced = 0;
            const int starMin = std::getenv("FORGE_STARMIN") ? std::atoi(std::getenv("FORGE_STARMIN")) : 6;
            for (int v : order) {
                const int n = static_cast<int>(inc[v].size());
                if (n < starMin) break;
                if (onBorder[v] || subFeat[v] >= 1 || touched[v]) { if (getenv("FORGE_DBG") && n >= 12) std::fprintf(stderr, "star %d skipped: border %d feat %d touched %d\n", n, onBorder[v], subFeat[v], touched[v]); continue; }
                // quads around v in order: (v, a_i, b_i, a_{i+1})
                std::vector<std::array<int, 4>> st;
                bool ok = true;
                for (int qi : inc[v]) { if (deadQ[qi]) ok = false; auto q = F4[qi]; while (q[0] != v) std::rotate(q.begin(), q.begin() + 1, q.end()); st.push_back(q); }
                if (!ok) continue;
                std::vector<std::array<int, 4>> seq;
                std::vector<uint8_t> used(n, 0);
                seq.push_back(st[0]); used[0] = 1;
                for (int step = 1; step < n && ok; ++step) {
                    const int want = seq.back()[3];
                    int found = -1;
                    for (int i = 0; i < n; ++i) if (!used[i] && st[i][1] == want) { found = i; break; }
                    if (found < 0) { ok = false; break; }
                    used[found] = 1; seq.push_back(st[found]);
                }
                if (!ok || seq.back()[3] != seq[0][1]) { if (getenv("FORGE_DBG") && n >= 12) std::fprintf(stderr, "star %d skipped: chain\n", n); continue; }
                std::vector<int> ring;      // a0 b0 a1 b1 ...
                for (const auto& q : seq) { ring.push_back(q[1]); ring.push_back(q[2]); }
                const int L = 2 * n;
                {   std::vector<int> tmp = ring; std::sort(tmp.begin(), tmp.end());
                    if (std::adjacent_find(tmp.begin(), tmp.end()) != tmp.end()) { if (getenv("FORGE_DBG") && n >= 12) { std::fprintf(stderr, "star %d skipped: repeated ring vertex:", n); for (int jj = 0; jj < (int)ring.size(); ++jj) std::fprintf(stderr, " %d(%zu)", ring[jj], inc[ring[jj]].size()); std::fprintf(stderr, "\n"); } continue; } }
                bool bad = false;
                for (int u : ring) if (onBorder[u] || touched[u] || deadV[u]) bad = true;
                if (bad) { if (getenv("FORGE_DBG") && n >= 12) std::fprintf(stderr, "star %d skipped: ring on border/touched\n", n); continue; }
                // outside degree of every ring vertex, and the cost of the choices
                std::vector<int> outd(L);
                for (int j = 0; j < L; ++j) outd[j] = static_cast<int>(inc[ring[j]].size()) - ((j & 1) ? 2 : 3);
                double oldCost = double(n - 4) * (n - 4);
                for (int u : ring) { const int d = static_cast<int>(inc[u].size()) - 4; oldCost += double(d) * d; }
                double h = 0;
                for (int j = 0; j < L; ++j) h += distance(sub.P[ring[j]], sub.P[ring[(j + 1) % L]]);
                h /= L;
                double bestCost = 1e300, bestVal = 1e300; int bestP = 0, bestK = 0;
                for (int k = 1; k <= n - 1; ++k) {
                    const int l = n - k;
                    if (std::min(k, l) < (std::getenv("FORGE_STARK") ? std::atoi(std::getenv("FORGE_STARK")) : 2)) continue;
                    for (int p = 0; p < L; ++p) {
                        const int c[4] = {p, (p + k) % L, (p + k + l) % L, (p + 2 * k + l) % L};
                        double cost = 0;
                        for (int j = 0; j < L; ++j) {
                            const bool corner = j == c[0] || j == c[1] || j == c[2] || j == c[3];
                            const int val = outd[j] + (corner ? 2 : 3);
                            cost += double(val - 4) * (val - 4);
                        }
                        // shape: the chord of every side should be about (cells on that side) x (edge length)
                        const int cnt[4] = {k, l, k, l};
                        double shape = 0;
                        for (int s4 = 0; s4 < 4; ++s4) {
                            const double ch = distance(sub.P[ring[c[s4]]], sub.P[ring[c[(s4 + 1) & 3]]]);
                            const double r = ch / (cnt[s4] * h) - 1.0;
                            shape += r * r;
                        }
                        const double total = cost + 8.0 * shape * n / 4.0;
                        if (total < bestCost) { bestCost = total; bestVal = cost; bestP = p; bestK = k; }
                    }
                }
                double ringBefore = oldCost - double(n - 4) * (n - 4);
                if (!(bestVal + 0.5 < oldCost) || bestVal > ringBefore + 0.25 * double(n - 4) * (n - 4)) { if (getenv("FORGE_DBG") && n >= 12) std::fprintf(stderr, "star %d skipped: cost %.0f -> %.0f\n", n, oldCost, bestCost); continue; }
                const int k = bestK, l = n - bestK, p = bestP;
                // boundary positions of the patch, bottom (i), right (j), top (i reversed), left (j reversed)
                auto at = [&](int off) { return sub.P[ring[(p + off) % L]]; };
                std::vector<Vec3> B(k + 1), R(l + 1), T(k + 1), Lf(l + 1);
                for (int i = 0; i <= k; ++i) B[i] = at(i);
                for (int j = 0; j <= l; ++j) R[j] = at(k + j);
                for (int i = 0; i <= k; ++i) T[i] = at(k + l + (k - i));
                for (int j = 0; j <= l; ++j) Lf[j] = at(2 * k + l + (l - j));
                std::vector<std::vector<int>> grid(k + 1, std::vector<int>(l + 1, -1));
                for (int i = 0; i <= k; ++i) { grid[i][0] = ring[(p + i) % L]; grid[i][l] = ring[(p + k + l + (k - i)) % L]; }
                for (int j = 0; j <= l; ++j) { grid[0][j] = ring[(p + 2 * k + l + (l - j)) % L]; grid[k][j] = ring[(p + k + j) % L]; }
                const int base = nvS + static_cast<int>(newP.size());
                int made = 0;
                for (int i = 1; i < k; ++i)
                    for (int j = 1; j < l; ++j) {
                        const double sx = double(i) / k, ty = double(j) / l;
                        Vec3 pos = Lf[j] * (1 - sx) + R[j] * sx + B[i] * (1 - ty) + T[i] * ty
                                 - (B[0] * ((1 - sx) * (1 - ty)) + B[k] * (sx * (1 - ty)) + T[0] * ((1 - sx) * ty) + T[k] * (sx * ty));
                        const auto h = ref_.closest(pos);
                        if (h.tri >= 0) pos = h.p;
                        grid[i][j] = base + made++;
                        newP.push_back(pos);
                        newFeat.push_back(0);
                    }
                for (int i = 0; i < k; ++i)
                    for (int j = 0; j < l; ++j) newQ.push_back({grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]});
                for (int qi : inc[v]) deadQ[qi] = 1;
                deadV[v] = 1;
                for (int u : ring) touched[u] = 1;
                touched[v] = 1;
                ++replaced;
                if (getenv("FORGE_DBG")) std::fprintf(stderr, "star %d spokes -> %d x %d patch (cost %.0f -> %.0f)\n", n, k, l, oldCost, bestVal);
            }
            if (replaced) {
                std::vector<int> remapV(nvS + newP.size(), -1);
                PolyMesh cl;
                std::vector<uint8_t> cf;
                for (int v = 0; v < nvS; ++v) if (!deadV[v]) { remapV[v] = static_cast<int>(cl.P.size()); cl.P.push_back(sub.P[v]); cf.push_back(subFeat[v]); }
                for (size_t i = 0; i < newP.size(); ++i) { remapV[nvS + i] = static_cast<int>(cl.P.size()); cl.P.push_back(newP[i]); cf.push_back(0); }
                auto emit = [&](const std::array<int, 4>& q) { cl.sizes.push_back(4); for (int k2 = 0; k2 < 4; ++k2) cl.idx.push_back(remapV[q[k2]]); };
                for (size_t q = 0; q < F4.size(); ++q) if (!deadQ[q]) emit(F4[q]);
                for (const auto& q : newQ) emit(q);
                // smooth the new interior vertices (the rest stays)
                std::vector<std::vector<int>> nbN(cl.P.size());
                for (size_t q = 0; q < cl.sizes.size(); ++q)
                    for (int k2 = 0; k2 < 4; ++k2) { const int a = cl.idx[4*q+k2], b = cl.idx[4*q+(k2+1)%4]; nbN[a].push_back(b); nbN[b].push_back(a); }
                const int firstNew = static_cast<int>(cl.P.size() - newP.size());
                for (int it = 0; it < 200; ++it)
                    for (int v2 = firstNew; v2 < static_cast<int>(cl.P.size()); ++v2) {
                        Vec3 avg; for (int w : nbN[v2]) avg += cl.P[w];
                        avg = avg / static_cast<double>(nbN[v2].size());
                        Vec3 pn = cl.P[v2] + (avg - cl.P[v2]) * 0.8;
                        const auto h = ref_.closest(pn);
                        cl.P[v2] = h.tri >= 0 ? h.p : pn;
                    }
                sub = std::move(cl);
                subFeat = std::move(cf);
                if (getenv("FORGE_DBG")) std::fprintf(stderr, "replaced %d stars by patches\n", replaced);
            }
        }
        if (!std::getenv("FORGE_NOQQ")) {
            std::vector<std::array<int, 4>> F4(sub.idx.size() / 4);
            for (size_t q = 0; q < F4.size(); ++q) F4[q] = {sub.idx[4*q], sub.idx[4*q+1], sub.idx[4*q+2], sub.idx[4*q+3]};
            std::vector<uint8_t> frozen(sub.P.size(), 0);
            for (size_t v = 0; v < sub.P.size(); ++v) frozen[v] = subFeat[v] >= 1;
            QuadQualityStats qqs;
            improveQuads(sub.P, F4, frozen, ref_, 40, 0.6, qqs);
            if (getenv("FORGE_DBG")) std::fprintf(stderr, "quad quality: worst %.3f -> %.3f, bad(<0.6) %d -> %d, moved %d in %d sweeps\n", qqs.worstBefore, qqs.worstAfter, qqs.badBefore, qqs.badAfter, qqs.moved, qqs.sweeps);
        }
        out = std::move(sub);
    }
    st.graphSeconds = secs(t2, clock::now());
    if (faces.empty()) { err = "extraction produced no faces"; return false; }
    return true;
}

}  // namespace forge
