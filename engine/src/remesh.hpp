// Forge engine - isotropic triangle remeshing (Botsch & Kobbelt style) with
//   * feature lines and open borders kept as lines,
//   * per-vertex density (edge length multiplier),
//   * exact face-count targeting,
//   * snapping onto the original surface after every pass.
#pragma once
#include <chrono>
#include <functional>
#include <string>
#include <vector>

#include "bvh.hpp"
#include "cmesh.hpp"
#include "parallel.hpp"
#include "repair.hpp"

namespace forge {

struct RemeshParams {
    int targetFaces = 10000;
    int iterations = 8;          // main passes at the initial edge length
    int correctionRounds = 10;   // extra passes that steer the face count to the target
    int polishPasses = 3;        // final smoothing passes
    int threads = 1;
    bool project = true;
    double cornerAngleDeg = 35.0;
    double countTolerance = 0.02;
    double smoothing = 0.8;
    bool check = false;          // validate the mesh after every step (slow, for tests)
    std::function<void(int, const std::string&)> progress;
};

struct RemeshStats {
    int inputFaces = 0, outputFaces = 0, outputVerts = 0;
    double edgeLength = 0;
    int rounds = 0;
    double seconds = 0;
    std::string error;
};

class Remesher {
public:
    Remesher(const TriSoup& soup, const RemeshParams& params) : soup_(soup), prm_(params) {}

    bool run(PolyMesh& out, RemeshStats& st);

    // Reusable pieces (the quad stage builds on the same mesh and reference).
    CMesh mesh;
    SurfaceIndex ref;
    SegmentIndex featureLines;
    double baseLength = 0;

    void snapToReference(double smoothing, bool tangentialSmooth);

private:
    const TriSoup& soup_;
    RemeshParams prm_;
    std::vector<Vec3> normals_;
    long splits_ = 0, collapses_ = 0, flips_ = 0;

    double targetLen(int u, int v, double L) const {
        return L * 0.5 * (static_cast<double>(mesh.vdens[u]) + static_cast<double>(mesh.vdens[v]));
    }
    void computeNormals();
    struct EdgeRef { double key; int a, b; };
    int findEdge(int a, int b) const;
    int splitPass(double L);
    int collapsePass(double L);
    int flipPass(double L);
    bool tryCollapse(int c, int keep, int rem, double L);
    void step(double L, int smoothRepeats);
    void report(int pct, const std::string& msg) const { if (prm_.progress) prm_.progress(pct, msg); }
    bool validate(const char* where, RemeshStats& st) {
        if (!prm_.check) return true;
        const std::string e = mesh.check();
        if (e.empty()) return true;
        st.error = std::string("mesh check failed after ") + where + ": " + e;
        return false;
    }
};

// ------------------------------------------------------------------------ passes

inline void Remesher::computeNormals() {
    normals_.assign(mesh.numVertSlots(), Vec3());
    for (int f = 0; f < mesh.numFaceSlots(); ++f) {
        if (!mesh.falive[f]) continue;
        const Vec3 n = mesh.faceNormalUnnormalized(f);  // area weighted
        for (int k = 0; k < 3; ++k) normals_[mesh.V[3 * f + k]] += n;
    }
    for (auto& n : normals_) n = normalized(n);
}

// Corner x such that the edge opposite x joins vertices a and b, or -1.
inline int Remesher::findEdge(int a, int b) const {
    if (a < 0 || b < 0 || !mesh.valive[a] || !mesh.valive[b]) return -1;
    thread_local std::vector<int> sc;
    mesh.orbit(a, sc);
    for (int c : sc) {
        if (mesh.V[CMesh::nxt(c)] == b) return CMesh::prv(c);
        if (mesh.V[CMesh::prv(c)] == b) return CMesh::nxt(c);
    }
    return -1;
}

// Edges are collected as vertex pairs first and looked up again when processed: corner numbers
// change under splits, collapses and flips, so walking corner indices would skip edges.
inline int Remesher::splitPass(double L) {
    std::vector<EdgeRef> cand;
    const int n0 = mesh.numFaceSlots() * 3;
    for (int c = 0; c < n0; ++c) {
        if (!mesh.falive[CMesh::faceOf(c)]) continue;
        const int d = mesh.O[c];
        if (d >= 0 && d < c) continue;
        const int a = mesh.V[CMesh::nxt(c)], b = mesh.V[CMesh::prv(c)];
        const double len = distance(mesh.P[a], mesh.P[b]);
        if (len > 4.0 / 3.0 * targetLen(a, b, L)) cand.push_back({-len, a, b});  // longest first
    }
    std::sort(cand.begin(), cand.end(), [](const EdgeRef& x, const EdgeRef& y) { return x.key < y.key; });
    int count = 0;
    for (const EdgeRef& e : cand) {
        const int x = findEdge(e.a, e.b);
        if (x < 0) continue;
        const float dens = 0.5f * (mesh.vdens[e.a] + mesh.vdens[e.b]);
        mesh.splitEdge(x, (mesh.P[e.a] + mesh.P[e.b]) * 0.5, dens);
        ++count;
    }
    splits_ += count;
    return count;
}

inline bool Remesher::tryCollapse(int c, int keep, int rem, double L) {
    CMesh& m = mesh;
    const uint8_t tk = m.vtype[keep], tr = m.vtype[rem];
    if (tr == V_FIXED) return false;
    if (tr == V_CREASE) {
        if (!m.E[c] || tk == V_FREE) return false;  // creases only merge along their own line
    }
    const int d = m.O[c];
    const int A = m.V[c];
    const int D = d >= 0 ? m.V[d] : -1;
    const int fc = CMesh::faceOf(c), fd = d >= 0 ? CMesh::faceOf(d) : -1;

    // link condition
    thread_local std::vector<CMesh::Nb> nk, nr;
    thread_local std::vector<int> scratch;
    const bool keepClosed = m.neighbors(keep, nk, scratch);
    const bool remClosed = m.neighbors(rem, nr, scratch);
    int common = 0;
    for (const auto& x : nk)
        for (const auto& y : nr)
            if (x.v == y.v) ++common;
    if (common != (d >= 0 ? 2 : 1)) return false;
    (void)keepClosed;
    (void)remClosed;

    // valence of the two apex vertices must stay valid
    {
        const bool closedA = m.orbit(A, scratch);
        const int vA = static_cast<int>(scratch.size()) + (closedA ? 0 : 1);
        if (vA - 1 < (closedA ? 3 : 2)) return false;
        if (D >= 0) {
            const bool closedD = m.orbit(D, scratch);
            const int vD = static_cast<int>(scratch.size()) + (closedD ? 0 : 1);
            if (vD - 1 < (closedD ? 3 : 2)) return false;
        }
    }

    Vec3 newPos = m.P[keep];
    if (tk == V_FREE && tr == V_FREE) newPos = (m.P[keep] + m.P[rem]) * 0.5;

    // geometry: no flipped or degenerate triangles, no overlong edges
    auto checkFan = [&](int v) -> bool {
        m.orbit(v, scratch);
        for (int k : scratch) {
            const int f = CMesh::faceOf(k);
            if (f == fc || f == fd) continue;
            const int x = m.V[CMesh::nxt(k)], y = m.V[CMesh::prv(k)];
            const Vec3 nOld = cross(m.P[x] - m.P[v], m.P[y] - m.P[v]);
            const Vec3 nNew = cross(m.P[x] - newPos, m.P[y] - newPos);
            const double lo = length(nOld), ln = length(nNew);
            if (ln <= 1e-14 * (lo + 1e-300) || ln < 1e-18) return false;
            if (dot(nOld, nNew) < 0.3 * lo * ln) return false;
            if (v == rem || newPos.x != m.P[keep].x || newPos.y != m.P[keep].y || newPos.z != m.P[keep].z) {
                const double maxA = 4.0 / 3.0 * targetLen(keep, x, L);
                const double maxB = 4.0 / 3.0 * targetLen(keep, y, L);
                if (distance(newPos, m.P[x]) > maxA || distance(newPos, m.P[y]) > maxB) return false;
            }
        }
        return true;
    };
    // copy: checkFan reuses the scratch buffer
    if (!checkFan(rem)) return false;
    if (newPos.x != m.P[keep].x || newPos.y != m.P[keep].y || newPos.z != m.P[keep].z)
        if (!checkFan(keep)) return false;

    m.collapseEdge(c, keep, rem, newPos);
    return true;
}

inline int Remesher::collapsePass(double L) {
    std::vector<EdgeRef> cand;
    const int n0 = mesh.numFaceSlots() * 3;
    for (int c = 0; c < n0; ++c) {
        if (!mesh.falive[CMesh::faceOf(c)]) continue;
        const int d = mesh.O[c];
        if (d >= 0 && d < c) continue;
        const int u = mesh.V[CMesh::nxt(c)], v = mesh.V[CMesh::prv(c)];
        const double len = distance(mesh.P[u], mesh.P[v]);
        if (len < 0.8 * targetLen(u, v, L)) cand.push_back({len, u, v});  // shortest first
    }
    std::sort(cand.begin(), cand.end(), [](const EdgeRef& x, const EdgeRef& y) { return x.key < y.key; });
    int count = 0;
    for (const EdgeRef& e : cand) {
        const int x = findEdge(e.a, e.b);
        if (x < 0) continue;
        if (distance(mesh.P[e.a], mesh.P[e.b]) >= 0.8 * targetLen(e.a, e.b, L)) continue;
        if (tryCollapse(x, e.a, e.b, L) || tryCollapse(x, e.b, e.a, L)) ++count;
    }
    collapses_ += count;
    return count;
}

inline int Remesher::flipPass(double L) {
    CMesh& m = mesh;
    int count = 0;
    std::vector<int> sc;
    std::vector<CMesh::Nb> nb;
    std::vector<EdgeRef> cand;
    const int n0 = m.numFaceSlots() * 3;
    for (int c = 0; c < n0; ++c) {
        if (!m.falive[CMesh::faceOf(c)]) continue;
        const int d0 = m.O[c];
        if (d0 < 0 || d0 < c || m.E[c]) continue;
        cand.push_back({0.0, m.V[CMesh::nxt(c)], m.V[CMesh::prv(c)]});
    }
    for (const EdgeRef& e : cand) {
        const int c = findEdge(e.a, e.b);
        if (c < 0) continue;
        const int d = m.O[c];
        if (d < 0 || m.E[c]) continue;
        const int A = m.V[c], B = m.V[CMesh::nxt(c)], C = m.V[CMesh::prv(c)], D = m.V[d];
        if (A == D) continue;
        const int ids[4] = {A, D, B, C};
        int val[4];
        int target[4];
        bool closed[4];
        for (int i = 0; i < 4; ++i) {
            closed[i] = m.orbit(ids[i], sc);
            val[i] = static_cast<int>(sc.size()) + (closed[i] ? 0 : 1);
            target[i] = closed[i] ? 6 : 4;
        }
        if (val[2] - 1 < (closed[2] ? 3 : 2) || val[3] - 1 < (closed[3] ? 3 : 2)) continue;
        auto sq = [](int x) { return x * x; };
        const int before = sq(val[0] - target[0]) + sq(val[1] - target[1]) + sq(val[2] - target[2]) + sq(val[3] - target[3]);
        const int after = sq(val[0] + 1 - target[0]) + sq(val[1] + 1 - target[1]) + sq(val[2] - 1 - target[2]) + sq(val[3] - 1 - target[3]);
        if (after >= before) continue;
        // A and D must not be connected already
        m.neighbors(A, nb, sc);
        bool adjacent = false;
        for (const auto& x : nb) if (x.v == D) { adjacent = true; break; }
        if (adjacent) continue;
        // geometry
        const Vec3 &pa = m.P[A], &pb = m.P[B], &pc = m.P[C], &pd = m.P[D];
        const Vec3 n1 = cross(pb - pa, pc - pa), n2 = cross(pc - pd, pb - pd);   // old (A,B,C) and (D,C,B)
        const Vec3 m1 = cross(pb - pa, pd - pa), m2 = cross(pc - pd, pa - pd);   // new (A,B,D) and (D,C,A)
        const double l1 = length(m1), l2 = length(m2);
        if (l1 < 1e-18 || l2 < 1e-18) continue;
        if (dot(m1, m2) < 0.2 * l1 * l2) continue;
        const Vec3 nold = n1 + n2;
        if (dot(m1, nold) <= 0 || dot(m2, nold) <= 0) continue;
        if (distance(pa, pd) > 4.0 / 3.0 * targetLen(A, D, L)) continue;
        m.flipEdge(c);
        ++count;
    }
    flips_ += count;
    return count;
}

// Move vertices to even out the triangles and put them back on the reference surface.
inline void Remesher::snapToReference(double smoothing, bool tangentialSmooth) {
    CMesh& m = mesh;
    computeNormals();
    const int nV = m.numVertSlots();
    std::vector<Vec3> next = m.P;
    std::vector<float> nextDens = m.vdens;
    const bool haveDens = !soup_.density.empty();
    const double limit2 = 4.0 * baseLength * baseLength * 4.0;  // never move more than 4 edge lengths
    parallelFor(nV, prm_.threads, [&](int b, int e, int) {
        std::vector<CMesh::Nb> nb;
        std::vector<int> sc;
        for (int v = b; v < e; ++v) {
            if (!m.valive[v] || m.vtype[v] == V_FIXED) continue;
            Vec3 p = m.P[v];
            if (tangentialSmooth) {
                m.neighbors(v, nb, sc);
                if (m.vtype[v] == V_FREE) {
                    if (nb.empty()) continue;
                    Vec3 q;
                    for (const auto& x : nb) q += m.P[x.v];
                    q = q / static_cast<double>(nb.size());
                    Vec3 dlt = q - p;
                    dlt = dlt - normals_[v] * dot(normals_[v], dlt);
                    p = p + dlt * smoothing;
                } else {  // crease: slide along the line
                    Vec3 q;
                    int n = 0;
                    for (const auto& x : nb) if (x.feature) { q += m.P[x.v]; ++n; }
                    if (n == 2) p = p + (q / 2.0 - p) * smoothing;
                }
            }
            if (prm_.project) {
                if (m.vtype[v] == V_FREE) {
                    const auto hit = ref.closest(p, &normals_[v], 0.1);
                    if (hit.tri >= 0 && distance2(hit.p, m.P[v]) < limit2) {
                        p = hit.p;
                        if (haveDens) {
                            const auto& t = soup_.F[hit.tri];
                            nextDens[v] = static_cast<float>((1 - hit.u - hit.v) * soup_.density[t[0]] +
                                                             hit.u * soup_.density[t[1]] + hit.v * soup_.density[t[2]]);
                        }
                    }
                } else if (!featureLines.empty()) {
                    const auto hit = featureLines.closest(p);
                    if (hit.seg >= 0 && distance2(hit.p, m.P[v]) < limit2) p = hit.p;
                }
            }
            next[v] = p;
        }
    });
    m.P.swap(next);
    if (haveDens) m.vdens.swap(nextDens);
}

inline void Remesher::step(double L, int smoothRepeats) {
    int ns = 0;
    for (int i = 0; i < 6; ++i) { const int n = splitPass(L); ns += n; if (n == 0) break; }
    const int nf0 = mesh.aliveFaces;
    const int nc = collapsePass(L);
    const int nfl = flipPass(L);
    if (prm_.check) std::fprintf(stderr, "step: splits %d -> faces %d, collapses %d, flips %d, faces now %d\n", ns, nf0, nc, nfl, mesh.aliveFaces);
    for (int i = 0; i < smoothRepeats; ++i) snapToReference(prm_.smoothing, true);
    mesh.compact();
}

// ------------------------------------------------------------------------ driver

inline bool Remesher::run(PolyMesh& out, RemeshStats& st) {
    const auto t0 = std::chrono::steady_clock::now();
    st.inputFaces = static_cast<int>(soup_.F.size());
    if (soup_.F.empty()) { st.error = "the mesh has no valid faces"; return false; }
    const std::string err = mesh.build(soup_, prm_.cornerAngleDeg);
    if (!err.empty()) { st.error = err; return false; }
    if (!validate("build", st)) return false;

    ref.build(soup_.P, soup_.F);
    {
        std::vector<std::array<Vec3, 2>> segs;
        for (int c = 0; c < mesh.numFaceSlots() * 3; ++c) {
            if (!mesh.E[c]) continue;
            if (mesh.O[c] >= 0 && mesh.O[c] < c) continue;
            segs.push_back({mesh.P[mesh.V[CMesh::nxt(c)]], mesh.P[mesh.V[CMesh::prv(c)]]});
        }
        featureLines.build(std::move(segs));
    }

    // initial edge length from the (density weighted) surface area
    double weightedArea = 0;
    for (const auto& t : soup_.F) {
        const double a = 0.5 * length(cross(soup_.P[t[1]] - soup_.P[t[0]], soup_.P[t[2]] - soup_.P[t[0]]));
        double dn = 1.0;
        if (!soup_.density.empty())
            dn = (soup_.density[t[0]] + soup_.density[t[1]] + soup_.density[t[2]]) / 3.0;
        weightedArea += a / std::max(1e-6, dn * dn);
    }
    const double N = std::max(4, prm_.targetFaces);
    double L = std::sqrt(4.0 * weightedArea / (std::sqrt(3.0) * N));
    baseLength = L;
    if (!(L > 0) || !std::isfinite(L)) { st.error = "the mesh has zero surface area"; return false; }

    const int total = prm_.iterations + prm_.correctionRounds + prm_.polishPasses;
    int done = 0;
    for (int it = 0; it < prm_.iterations; ++it) {
        step(L, 2);
        if (!validate("main pass", st)) return false;
        report(10 + 60 * (++done) / total, "Remeshing faces=" + std::to_string(mesh.aliveFaces) + " L=" + std::to_string(L));
    }
    // steer the face count towards the target
    int rounds = 0;
    for (; rounds < prm_.correctionRounds; ++rounds) {
        const double ratio = static_cast<double>(mesh.aliveFaces) / N;
        if (std::abs(ratio - 1.0) <= prm_.countTolerance && rounds > 0) break;
        if (std::abs(ratio - 1.0) > prm_.countTolerance) {
            L *= std::pow(ratio, 0.5);
            baseLength = L;
        }
        step(L, 2);
        if (!validate("correction pass", st)) return false;
        report(10 + 60 * (++done) / total, "Matching the target");
    }
    st.rounds = rounds;
    for (int i = 0; i < prm_.polishPasses; ++i) {
        flipPass(L);
        snapToReference(prm_.smoothing, true);
        if (!validate("polish", st)) return false;
        report(10 + 60 * (++done) / total, "Polishing");
    }
    mesh.compact();
    snapToReference(0.0, false);  // final exact snap, no tangential motion
    if (!validate("final", st)) return false;

    mesh.exportTriangles(out);
    st.outputFaces = mesh.aliveFaces;
    st.outputVerts = mesh.aliveVerts;
    st.edgeLength = L;
    st.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    return true;
}

}  // namespace forge
