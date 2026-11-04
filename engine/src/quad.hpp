// Forge engine - quad meshes from the isotropic triangle mesh.
//
//   1. smooth cross field (field.hpp), aligned to feature lines and borders
//   2. pair triangles into quads: greedy by cost, then augmenting paths so almost
//      every triangle finds a partner (the cost prefers right angles, flat pairs and a
//      removed diagonal that sits at 45 degrees to the field)
//   3. split every polygon into quads (quad -> 4, leftover triangle -> 3): pure quads
//   4. relax: every quad proposes its nearest square (turned a little towards the field);
//      vertices average the proposals and snap back onto the original surface
#pragma once
#include <algorithm>
#include <chrono>
#include <functional>
#include <unordered_map>
#include <vector>

#include "bvh.hpp"
#include "cmesh.hpp"
#include "field.hpp"
#include "parallel.hpp"

namespace forge {

struct QuadParams {
    int threads = 1;
    bool project = true;
    int relaxIterations = 30;
    double relaxStep = 0.8;
    double fieldAlign = 0.5;      // 0 = ignore the field while relaxing, 1 = snap fully to it
    double fieldWeight = 1.0;     // weight of the field term when pairing
    bool useField = true;
    double poleWeight = 10.0;     // price of one irregular vertex in the pairing search (0 = off)
    int poleSweeps = 150;         // annealing moves per triangle
    int cyclePairs = 5;           // longest alternating cycle tried, in pairs
    std::function<void(int, const std::string&)> progress;
};

struct QuadStats {
    int triangles = 0, pairs = 0, leftover = 0;
    int irregularBefore = 0, irregularAfter = 0;    // vertices whose valence is not the ideal one
    int quads = 0;
    double fieldSeconds = 0, matchSeconds = 0, relaxSeconds = 0;
};

class Quadifier {
public:
    Quadifier(const CMesh& tri, const SurfaceIndex& ref, const SegmentIndex& feat, double edgeLength, const QuadParams& p)
        : m_(tri), ref_(ref), feat_(feat), L_(edgeLength), prm_(p) {}

    bool run(PolyMesh& out, QuadStats& st, std::string& err);

private:
    const CMesh& m_;
    const SurfaceIndex& ref_;
    const SegmentIndex& feat_;
    double L_;
    QuadParams prm_;

    // field data per triangle
    FaceFrames frames_;
    std::vector<cplx> z_;

    // result under construction
    std::vector<Vec3> P_;
    std::vector<uint8_t> type_;
    std::vector<std::array<int, 4>> Q_;
    std::vector<int> quadSource_;   // triangle whose field direction guides each quad

    double pairCost(int c) const;
    void match(std::vector<int>& mate, QuadStats& st);
    void refineMatching(std::vector<int>& mate, QuadStats& st);
    void subdivide(const std::vector<int>& mate);
    void relax();
    void report(int pct, const std::string& s) const { if (prm_.progress) prm_.progress(pct, s); }
};

// ------------------------------------------------------------------ pairing

inline double angleBetween(const Vec3& a, const Vec3& b) {
    const double la = length(a), lb = length(b);
    if (la < 1e-300 || lb < 1e-300) return 0;
    return std::acos(clampd(dot(a, b) / (la * lb), -1.0, 1.0));
}

// cost of removing the edge opposite corner c (merging its two triangles into one quad)
inline double Quadifier::pairCost(int c) const {
    const int d = m_.O[c];
    const int f = CMesh::faceOf(c), g = CMesh::faceOf(d);
    const int n1 = CMesh::nxt(c), p1 = CMesh::prv(c);
    const Vec3 &A = m_.P[m_.V[c]], &B = m_.P[m_.V[n1]], &C = m_.P[m_.V[p1]], &D = m_.P[m_.V[d]];
    // quad A, B, D, C (counter clockwise)
    const Vec3 q[4] = {A, B, D, C};
    double angleCost = 0;
    constexpr double half_pi = kPi / 2;
    double maxAngle = 0;
    for (int k = 0; k < 4; ++k) {
        const Vec3 u = q[(k + 1) % 4] - q[k], v = q[(k + 3) % 4] - q[k];
        const double ang = angleBetween(u, v);
        maxAngle = std::max(maxAngle, ang);
        const double e = (ang - half_pi) / (kPi / 4);
        angleCost += e * e;
    }
    if (maxAngle > 170.0 * kPi / 180.0) angleCost += 1000.0;      // (almost) flat or concave corner
    // flatness
    const double flat = 1.0 - clampd(dot(frames_.n[f], frames_.n[g]), -1.0, 1.0);
    double cost = angleCost + 40.0 * flat;
    // side length balance: the two sides next to the removed edge versus the others
    const double s0 = distance(A, B), s1 = distance(B, D), s2 = distance(D, C), s3 = distance(C, A);
    const double mean = 0.25 * (s0 + s1 + s2 + s3);
    for (double s : {s0, s1, s2, s3}) { const double e = (s - mean) / std::max(mean, 1e-300); cost += 2.0 * e * e; }
    if (prm_.useField) {
        // the removed edge should sit at 45 degrees to the field axes
        const Vec3 diag = normalized(C - B);
        const Vec3 dirF = axisFromZ(frames_, f, z_[f]);
        const Vec3 dirG = axisFromZ(frames_, g, z_[g]);
        // use the field of both faces, transported into the plane of f by the best of the two
        const double a1 = std::atan2(dot(diag, cross(frames_.n[f], dirF)), dot(diag, dirF));
        const double a2 = std::atan2(dot(diag, cross(frames_.n[g], dirG)), dot(diag, dirG));
        auto off45 = [](double a) {  // distance of a (mod 90 deg) from 45 deg, in [0, 1]
            double r = std::fmod(std::fabs(a), kPi / 2);
            return std::fabs(r - kPi / 4) / (kPi / 4);   // 0 at 45 deg, 1 on an axis
        };
        const double e = 0.5 * (off45(a1) + off45(a2));
        cost += prm_.fieldWeight * 6.0 * e * e;
    }
    return cost;
}

inline void Quadifier::match(std::vector<int>& mate, QuadStats& st) {
    const int nF = m_.numFaceSlots();
    mate.assign(nF, -1);
    struct Cand { double cost; int c; };
    std::vector<Cand> cand;
    for (int c = 0; c < nF * 3; ++c) {
        if (!m_.falive[CMesh::faceOf(c)]) continue;
        const int d = m_.O[c];
        if (d < 0 || d < c || m_.E[c]) continue;  // never remove feature lines
        cand.push_back({pairCost(c), c});
    }
    std::sort(cand.begin(), cand.end(), [](const Cand& a, const Cand& b) { return a.cost < b.cost; });
    for (const Cand& cd : cand) {
        const int f = CMesh::faceOf(cd.c), g = CMesh::faceOf(m_.O[cd.c]);
        if (mate[f] < 0 && mate[g] < 0 && cd.cost < 900.0) { mate[f] = g; mate[g] = f; }
    }
    // augmenting paths (alternating BFS) for triangles still alone
    std::vector<int> seen(nF, 0), outerFrom(nF, -1), queue;
    int stamp = 0;
    auto allowedNb = [&](int f, int k) -> int {
        const int c = 3 * f + k;
        const int d = m_.O[c];
        if (d < 0 || m_.E[c]) return -1;
        return CMesh::faceOf(d);
    };
    for (int root = 0; root < nF; ++root) {
        if (!m_.falive[root] || mate[root] >= 0) continue;
        ++stamp;
        queue.assign(1, root);
        seen[root] = stamp;
        bool done = false;
        for (size_t qi = 0; qi < queue.size() && !done && qi < 4000; ++qi) {
            const int x = queue[qi];
            for (int k = 0; k < 3 && !done; ++k) {
                const int y = allowedNb(x, k);
                if (y < 0 || seen[y] == stamp) continue;
                if (mate[y] < 0) {
                    int cur = x, partner = y;
                    for (;;) {
                        const int old = mate[cur];
                        mate[cur] = partner; mate[partner] = cur;
                        if (cur == root) break;
                        partner = old; cur = outerFrom[old];
                    }
                    done = true;
                } else {
                    seen[y] = stamp;
                    const int w = mate[y];
                    if (seen[w] != stamp) { seen[w] = stamp; outerFrom[y] = x; queue.push_back(w); }
                }
            }
        }
    }
    st.triangles = m_.aliveFaces;
    for (int f = 0; f < nF; ++f) if (m_.falive[f] && mate[f] > f) ++st.pairs;
    st.leftover = st.triangles - 2 * st.pairs;
}


// ------------------------------------------------------------------ valence-aware pairing
//
// After pairing and subdividing, an original vertex v ends with valence  k(v) - d(v)  (interior) where k is
// its number of triangles and d the number of removed edges (diagonals) touching it. All other new vertices
// are regular. So the poles are decided entirely by WHICH edges the pairing removes. We search for a pairing
// whose d(v) is close to k(v) - 4 (interior) or k(v) - 2 (border) with simulated annealing over alternating
// cycles (toggle the pairs along a closed path of triangles: a perfect pairing stays perfect).
inline void Quadifier::refineMatching(std::vector<int>& mate, QuadStats& st) {
    const int nF = m_.numFaceSlots(), nV = m_.numVertSlots();
    if (prm_.poleWeight <= 0 || m_.aliveFaces < 8) return;
    std::vector<double> costC(3 * static_cast<size_t>(nF), 1e30);
    for (int c = 0; c < nF * 3; ++c) {
        if (!m_.falive[CMesh::faceOf(c)]) continue;
        const int d = m_.O[c];
        if (d < 0 || m_.E[c]) continue;
        costC[c] = d > c ? pairCost(c) : 0.0;
    }
    for (int c = 0; c < nF * 3; ++c) if (m_.O[c] >= 0 && m_.O[c] < c && costC[c] < 1e29) costC[c] = costC[m_.O[c]];
    // vertex data
    std::vector<int> k(nV, 0);
    std::vector<uint8_t> bnd(nV, 0);
    for (int c = 0; c < nF * 3; ++c) {
        if (!m_.falive[CMesh::faceOf(c)]) continue;
        ++k[m_.V[c]];
        if (m_.O[c] < 0) { bnd[m_.V[CMesh::nxt(c)]] = 1; bnd[m_.V[CMesh::prv(c)]] = 1; }
    }
    std::vector<int> d(nV, 0);
    std::vector<double> w(nV, 1.0);
    for (int v = 0; v < nV; ++v) if (m_.vtype[v] == V_FIXED && !bnd[v]) w[v] = 0.0;
    auto ideal = [&](int v) { return bnd[v] ? 2 : 4; };
    auto sharedCorner = [&](int f, int g) {
        for (int j = 0; j < 3; ++j) { const int o = m_.O[3 * f + j]; if (o >= 0 && CMesh::faceOf(o) == g) return 3 * f + j; }
        return -1;
    };
    auto addEdge = [&](int c, int sign) { d[m_.V[CMesh::nxt(c)]] += sign; d[m_.V[CMesh::prv(c)]] += sign; };
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f] || mate[f] <= f) continue;
        const int c = sharedCorner(f, mate[f]);
        if (c >= 0) addEdge(c, +1);
    }
    auto err = [&](int v) { return k[v] - d[v] - ideal(v); };
    auto irregular = [&]() { int n = 0; for (int v = 0; v < nV; ++v) if (w[v] > 0 && k[v] > 0 && err(v) != 0) ++n; return n; };
    st.irregularBefore = irregular();

    uint64_t rng = 0x9E3779B97F4A7C15ULL;
    auto rnd = [&]() { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return rng; };
    auto rnd01 = [&]() { return static_cast<double>(rnd() >> 11) / 9007199254740992.0; };

    std::vector<int> matched;
    for (int f = 0; f < nF; ++f) if (m_.falive[f] && mate[f] > f) matched.push_back(f);
    if (matched.empty()) return;
    const long long moves = static_cast<long long>(prm_.poleSweeps) * static_cast<long long>(matched.size());
    const double T0 = 1.0, T1 = 0.02;

    int path[16];
    std::vector<int> touched;                 // vertices touched by the move under evaluation
    std::vector<int> dd(nV, 0);
    int bestPath[16], bestLen = 0;
    double bestDelta = 1e30;

    // delta of toggling the alternating cycle path[0..len)  (old pairs (0,1),(2,3).. new pairs (1,2),(3,4)..(len-1,0))
    auto evaluate = [&](int len) -> double {
        double dc = 0;
        touched.clear();
        auto bump = [&](int c, int sign) {
            for (int v : {m_.V[CMesh::nxt(c)], m_.V[CMesh::prv(c)]}) {
                if (dd[v] == 0) touched.push_back(v);
                dd[v] += sign;
                if (dd[v] == 0) { /* stays in list, harmless */ }
            }
        };
        for (int i = 0; i < len; i += 2) {
            const int c = sharedCorner(path[i], path[i + 1]);
            if (c < 0) { for (int v : touched) dd[v] = 0; return 1e30; }
            dc -= costC[c]; bump(c, -1);
        }
        for (int i = 1; i <= len; i += 2) {
            const int a = path[i], b = path[(i + 1) % len];
            const int c = sharedCorner(a, b);
            if (c < 0 || costC[c] > 1e29) { for (int v : touched) dd[v] = 0; return 1e30; }
            dc += costC[c]; bump(c, +1);
        }
        double dv = 0;
        for (int v : touched) {
            if (dd[v] != 0 && w[v] > 0) {
                const int e0 = err(v), e1 = e0 - dd[v];
                dv += w[v] * (static_cast<double>(e1) * e1 - static_cast<double>(e0) * e0);
            }
        }
        for (int v : touched) dd[v] = 0;
        return dc + prm_.poleWeight * dv;
    };

    auto neighbourFace = [&](int f, int j) -> int {
        const int c = 3 * f + j;
        if (m_.O[c] < 0 || m_.E[c]) return -1;
        return CMesh::faceOf(m_.O[c]);
    };
    auto inPath = [&](int f, int len) { for (int i = 0; i < len; ++i) if (path[i] == f) return true; return false; };

    // depth-first search of alternating cycles through path[0]=a, path[1]=mate[a]
    std::function<void(int)> dfs = [&](int len) {
        const int a = path[0], y = path[len - 1];
        if (len >= 4) {                       // does y touch a through a free (unmatched) dual edge?
            for (int j = 0; j < 3; ++j) if (neighbourFace(y, j) == a) {
                const double dl = evaluate(len);
                if (dl < bestDelta) { bestDelta = dl; bestLen = len; std::copy(path, path + len, bestPath); }
                break;
            }
        }
        if (len / 2 >= prm_.cyclePairs) return;
        for (int j = 0; j < 3; ++j) {
            const int c = neighbourFace(y, j);
            if (c < 0 || mate[c] < 0 || inPath(c, len) || c == mate[y]) continue;
            const int e = mate[c];
            if (e < 0 || inPath(e, len)) continue;
            path[len] = c; path[len + 1] = e;
            dfs(len + 2);
        }
    };

    for (long long it = 0; it < moves; ++it) {
        const double T = T0 * std::pow(T1 / T0, static_cast<double>(it) / static_cast<double>(moves));
        int a = matched[rnd() % matched.size()];
        if (rnd() & 1) a = mate[a];
        if (mate[a] < 0) continue;
        path[0] = a; path[1] = mate[a];
        bestDelta = 1e30; bestLen = 0;
        dfs(2);
        if (bestLen == 0) continue;
        if (bestDelta < 0 || rnd01() < std::exp(-bestDelta / T)) {
            // apply: remove old pairs, add new ones
            for (int i = 0; i < bestLen; i += 2) { addEdge(sharedCorner(bestPath[i], bestPath[i + 1]), -1); }
            for (int i = 1; i <= bestLen; i += 2) {
                const int p = bestPath[i], q = bestPath[(i + 1) % bestLen];
                mate[p] = q; mate[q] = p;
                addEdge(sharedCorner(p, q), +1);
            }
        }
    }
    st.irregularAfter = irregular();
}

// ------------------------------------------------------------------ subdivision

inline void Quadifier::subdivide(const std::vector<int>& mate) {
    struct Poly { int n; int v[4]; uint8_t feat[4]; int src; };
    std::vector<Poly> polys;
    const int nF = m_.numFaceSlots();
    for (int f = 0; f < nF; ++f) {
        if (!m_.falive[f]) continue;
        const int g = mate[f];
        if (g >= 0 && g < f) continue;  // handled with its partner
        if (g < 0) {
            const int c = 3 * f, n1 = CMesh::nxt(c), p1 = CMesh::prv(c);
            polys.push_back({3, {m_.V[c], m_.V[n1], m_.V[p1], -1}, {m_.E[p1], m_.E[c], m_.E[n1], 0}, f});
        } else {
            // find the corner c of f whose opposite edge is shared with g
            int c = -1;
            for (int k = 0; k < 3; ++k) if (m_.O[3 * f + k] >= 0 && CMesh::faceOf(m_.O[3 * f + k]) == g) { c = 3 * f + k; break; }
            const int d = m_.O[c], n1 = CMesh::nxt(c), p1 = CMesh::prv(c), n2 = CMesh::nxt(d), p2 = CMesh::prv(d);
            polys.push_back({4, {m_.V[c], m_.V[n1], m_.V[d], m_.V[p1]}, {m_.E[p1], m_.E[n2], m_.E[p2], m_.E[n1]}, f});
        }
    }
    P_ = m_.P;
    type_ = m_.vtype;
    P_.resize(m_.numVertSlots());
    std::unordered_map<uint64_t, int> mid;
    mid.reserve(polys.size() * 3);
    auto midpoint = [&](int a, int b, bool feature) {
        const uint64_t key = detail::edgeKey(a, b);
        auto it = mid.find(key);
        if (it != mid.end()) return it->second;
        const int id = static_cast<int>(P_.size());
        P_.push_back((P_[a] + P_[b]) * 0.5);
        type_.push_back(feature ? V_CREASE : V_FREE);
        mid.emplace(key, id);
        return id;
    };
    Q_.clear();
    quadSource_.clear();
    for (const Poly& p : polys) {
        int mids[4];
        Vec3 centre;
        for (int i = 0; i < p.n; ++i) { mids[i] = midpoint(p.v[i], p.v[(i + 1) % p.n], p.feat[i] != 0); centre += P_[p.v[i]]; }
        const int cid = static_cast<int>(P_.size());
        P_.push_back(centre / p.n);
        type_.push_back(V_FREE);
        for (int i = 0; i < p.n; ++i) {
            Q_.push_back({p.v[i], mids[i], cid, mids[(i + p.n - 1) % p.n]});
            quadSource_.push_back(p.src);
        }
    }
}

// ------------------------------------------------------------------ relaxation

inline void Quadifier::relax() {
    const int nV = static_cast<int>(P_.size());
    const int nQ = static_cast<int>(Q_.size());
    // incident quads (CSR)
    std::vector<int> start(nV + 1, 0);
    for (const auto& q : Q_) for (int k = 0; k < 4; ++k) ++start[q[k] + 1];
    for (int v = 0; v < nV; ++v) start[v + 1] += start[v];
    std::vector<int> incident(start[nV]);
    {
        std::vector<int> fill(start.begin(), start.end() - 1);
        for (int qi = 0; qi < nQ; ++qi) for (int k = 0; k < 4; ++k) incident[fill[Q_[qi][k]]++] = qi * 4 + k;
    }
    // feature neighbours of crease vertices (along quad edges that are features)
    std::vector<std::array<int, 2>> fnb(nV, {-1, -1});
    {
        // an edge is a feature if both endpoints are non-free and it lies on a feature line:
        // true for quad edges whose two vertices are crease/fixed AND whose midpoint is on a feature line
        auto onFeature = [&](int a, int b) {
            if (type_[a] == V_FREE || type_[b] == V_FREE || feat_.empty()) return false;
            const auto h = feat_.closest((P_[a] + P_[b]) * 0.5);
            return h.d2 < 1e-6 * L_ * L_;
        };
        for (const auto& q : Q_)
            for (int k = 0; k < 4; ++k) {
                const int a = q[k], b = q[(k + 1) % 4];
                if (!onFeature(a, b)) continue;
                for (int s = 0; s < 2; ++s) {
                    const int u = s ? b : a, w = s ? a : b;
                    if (type_[u] != V_CREASE) continue;
                    if (fnb[u][0] == w || fnb[u][1] == w) continue;
                    if (fnb[u][0] < 0) fnb[u][0] = w; else if (fnb[u][1] < 0) fnb[u][1] = w;
                }
            }
    }
    // field direction per quad (taken from the triangle it came from)
    std::vector<Vec3> qdir(nQ);
    for (int qi = 0; qi < nQ; ++qi) { const int f = quadSource_[qi]; qdir[qi] = axisFromZ(frames_, f, z_[f]); }

    std::vector<Vec3> proposal(static_cast<size_t>(nQ) * 4);
    std::vector<Vec3> nextP(nV);
    std::vector<Vec3> vnormal(nV);
    for (int it = 0; it < prm_.relaxIterations; ++it) {
        // 1. square proposals
        parallelFor(nQ, prm_.threads, [&](int b, int e, int) {
            for (int qi = b; qi < e; ++qi) {
                const auto& q = Q_[qi];
                const Vec3 p0 = P_[q[0]], p1 = P_[q[1]], p2 = P_[q[2]], p3 = P_[q[3]];
                const Vec3 c = (p0 + p1 + p2 + p3) * 0.25;
                Vec3 n = normalized(cross(p2 - p0, p3 - p1));
                if (length2(n) < 0.5) { for (int k = 0; k < 4; ++k) proposal[qi * 4 + k] = P_[q[k]]; continue; }
                Vec3 u1 = p0 - c;
                u1 = normalized(u1 - n * dot(n, u1));
                if (length2(u1) < 0.5) { for (int k = 0; k < 4; ++k) proposal[qi * 4 + k] = P_[q[k]]; continue; }
                const Vec3 u2 = cross(n, u1);
                cplx z[4];
                const Vec3 pts[4] = {p0, p1, p2, p3};
                for (int k = 0; k < 4; ++k) z[k] = cplx(dot(pts[k] - c, u1), dot(pts[k] - c, u2));
                const cplx I(0, 1);
                cplx w = (z[0] - I * z[1] - z[2] + I * z[3]) * 0.25;
                if (prm_.useField && prm_.fieldAlign > 0 && std::abs(w) > 1e-300) {
                    Vec3 d = qdir[qi];
                    d = normalized(d - n * dot(n, d));
                    if (length2(d) > 0.5) {
                        const double thetaD = std::atan2(dot(d, u2), dot(d, u1));
                        // square sides run at angle(w)+135deg; they should be a multiple of 90deg from thetaD
                        const double target = thetaD + kPi / 4;
                        double delta = std::fmod(std::arg(w) - target, kPi / 2);
                        if (delta > kPi / 4) delta -= kPi / 2;
                        if (delta < -kPi / 4) delta += kPi / 2;
                        w = std::polar(std::abs(w), std::arg(w) - prm_.fieldAlign * delta);
                    }
                }
                cplx r = w;
                for (int k = 0; k < 4; ++k) { proposal[qi * 4 + k] = c + u1 * r.real() + u2 * r.imag(); r *= I; }
            }
        });
        // 2. vertex normals (for the projection filter)
        std::fill(vnormal.begin(), vnormal.end(), Vec3());
        for (int qi = 0; qi < nQ; ++qi) {
            const auto& q = Q_[qi];
            const Vec3 n = cross(P_[q[2]] - P_[q[0]], P_[q[3]] - P_[q[1]]);
            for (int k = 0; k < 4; ++k) vnormal[q[k]] += n;
        }
        // 3. gather, move, snap
        parallelFor(nV, prm_.threads, [&](int b, int e, int) {
            for (int v = b; v < e; ++v) {
                nextP[v] = P_[v];
                if (type_[v] == V_FIXED || start[v] == start[v + 1]) continue;
                Vec3 p = P_[v];
                if (type_[v] == V_FREE) {
                    Vec3 acc;
                    int cnt = 0;
                    for (int i = start[v]; i < start[v + 1]; ++i) { acc += proposal[incident[i]]; ++cnt; }
                    p = p + (acc / cnt - p) * prm_.relaxStep;
                    if (prm_.project) {
                        const Vec3 nn = normalized(vnormal[v]);
                        const auto hit = ref_.closest(p, &nn, 0.1);
                        if (hit.tri >= 0) p = hit.p;
                    }
                } else {
                    const int a = fnb[v][0], bb = fnb[v][1];
                    if (a >= 0 && bb >= 0) {
                        p = p + ((P_[a] + P_[bb]) * 0.5 - p) * prm_.relaxStep;
                        if (prm_.project && !feat_.empty()) p = feat_.closest(p).p;
                    }
                }
                nextP[v] = p;
            }
        });
        P_.swap(nextP);
        if (prm_.progress && it % 5 == 0) report(80 + 15 * it / std::max(1, prm_.relaxIterations), "Relaxing quads");
    }
}

// ------------------------------------------------------------------ driver

inline bool Quadifier::run(PolyMesh& out, QuadStats& st, std::string& err) {
    using clock = std::chrono::steady_clock;
    auto secs = [](clock::time_point a, clock::time_point b) { return std::chrono::duration<double>(b - a).count(); };
    auto t0 = clock::now();
    frames_ = buildFrames(m_);
    z_ = solveCrossField(m_, frames_, true);
    auto t1 = clock::now();
    st.fieldSeconds = secs(t0, t1);
    report(75, "Pairing triangles");
    std::vector<int> mate;
    match(mate, st);
    refineMatching(mate, st);
    auto t2 = clock::now();
    st.matchSeconds = secs(t1, t2);
    subdivide(mate);
    relax();
    st.relaxSeconds = secs(t2, clock::now());
    st.quads = static_cast<int>(Q_.size());
    if (Q_.empty()) { err = "no quads were produced"; return false; }
    out.P = P_;
    out.sizes.assign(Q_.size(), 4);
    out.idx.clear();
    out.idx.reserve(Q_.size() * 4);
    for (const auto& q : Q_) for (int k = 0; k < 4; ++k) out.idx.push_back(q[k]);
    out.features.clear();
    out.density.clear();
    return true;
}

}  // namespace forge
