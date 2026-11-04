// Forge engine - dynamic triangle mesh stored as a "corner table".
//
// Corner c = 3*face + k. V[c] is its vertex, O[c] the opposite corner in the
// neighbouring face (or -1 on an open border). The edge opposite corner c runs
// between V[next(c)] and V[prev(c)]. E[c] flags that edge as a feature line
// (kept sharp); open borders are always features.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "bvh.hpp"
#include "repair.hpp"
#include "vec.hpp"

namespace forge {

enum VType : uint8_t { V_FREE = 0, V_CREASE = 1, V_FIXED = 2 };

class CMesh {
public:
    std::vector<Vec3> P;
    std::vector<int> vcorner;      // some corner of each vertex (-1 for dead vertices)
    std::vector<uint8_t> vtype;
    std::vector<uint8_t> valive;
    std::vector<float> vdens;      // edge-length multiplier per vertex
    std::vector<int> V, O;
    std::vector<uint8_t> E;
    std::vector<uint8_t> falive;
    int aliveFaces = 0;
    int aliveVerts = 0;

    static int nxt(int c) { return (c % 3 == 2) ? c - 2 : c + 1; }
    static int prv(int c) { return (c % 3 == 0) ? c + 2 : c - 1; }
    static int faceOf(int c) { return c / 3; }
    int numFaceSlots() const { return static_cast<int>(falive.size()); }
    int numVertSlots() const { return static_cast<int>(P.size()); }

    // ----------------------------------------------------------- construction
    // Returns an error message (empty on success).
    std::string build(const TriSoup& soup, double cornerAngleDeg) {
        const int nF = static_cast<int>(soup.F.size());
        const int nV = static_cast<int>(soup.P.size());
        P = soup.P;
        vdens.assign(nV, 1.0f);
        if (!soup.density.empty()) vdens = soup.density;
        V.resize(3 * nF); O.assign(3 * nF, -1); E.assign(3 * nF, 0); falive.assign(nF, 1);
        for (int f = 0; f < nF; ++f)
            for (int k = 0; k < 3; ++k) V[3 * f + k] = soup.F[f][k];
        // opposite corners through sorted directed edges
        std::vector<std::pair<uint64_t, int>> dir(3 * nF);
        for (int c = 0; c < 3 * nF; ++c) {
            const uint64_t a = static_cast<uint32_t>(V[nxt(c)]), b = static_cast<uint32_t>(V[prv(c)]);
            dir[c] = {(a << 32) | b, c};
        }
        std::sort(dir.begin(), dir.end());
        for (size_t i = 1; i < dir.size(); ++i)
            if (dir[i].first == dir[i - 1].first) return "two faces share the same directed edge";
        for (int c = 0; c < 3 * nF; ++c) {
            const uint64_t a = static_cast<uint32_t>(V[prv(c)]), b = static_cast<uint32_t>(V[nxt(c)]);
            const std::pair<uint64_t, int> key{(a << 32) | b, 0};
            auto it = std::lower_bound(dir.begin(), dir.end(), key,
                                       [](const auto& x, const auto& y) { return x.first < y.first; });
            if (it != dir.end() && it->first == key.first) O[c] = it->second;
        }
        // feature flags
        std::vector<uint64_t> fkeys;
        fkeys.reserve(soup.features.size());
        for (const auto& e : soup.features) fkeys.push_back(detail::edgeKey(e[0], e[1]));
        std::sort(fkeys.begin(), fkeys.end());
        for (int c = 0; c < 3 * nF; ++c) {
            if (O[c] < 0) { E[c] = 1; continue; }
            if (std::binary_search(fkeys.begin(), fkeys.end(), detail::edgeKey(V[nxt(c)], V[prv(c)]))) E[c] = 1;
        }
        vcorner.assign(nV, -1);
        for (int c = 0; c < 3 * nF; ++c) vcorner[V[c]] = c;
        valive.assign(nV, 0);
        for (int v = 0; v < nV; ++v) valive[v] = vcorner[v] >= 0;
        aliveFaces = nF;
        aliveVerts = 0;
        for (int v = 0; v < nV; ++v) aliveVerts += valive[v];
        classifyVertices(cornerAngleDeg);
        return {};
    }

    // ----------------------------------------------------------- topology queries
    int swingLeft(int c) const {  // rotate around V[c] across the edge to V[prev(c)]
        const int o = O[nxt(c)];
        return o < 0 ? -1 : nxt(o);
    }
    int swingRight(int c) const {  // rotate across the edge to V[next(c)]
        const int o = O[prv(c)];
        return o < 0 ? -1 : prv(o);
    }

    // All corners at vertex v, in rotation order (starting at the border when there is one).
    // Returns true when the fan is closed (interior vertex).
    bool orbit(int v, std::vector<int>& out) const {
        out.clear();
        const int start = vcorner[v];
        int first = start;
        bool closed = false;
        for (int guard = 0; guard < 1000000; ++guard) {
            const int n = swingLeft(first);
            if (n < 0) break;
            if (n == start) { closed = true; first = start; break; }
            first = n;
        }
        int c = first;
        for (int guard = 0; guard < 1000000; ++guard) {
            out.push_back(c);
            c = swingRight(c);
            if (c < 0 || c == first) break;
        }
        return closed;
    }

    struct Nb { int v; bool feature; };
    // Neighbouring vertices of v with the feature flag of the connecting edge.
    bool neighbors(int v, std::vector<Nb>& out, std::vector<int>& scratch) const {
        const bool closed = orbit(v, scratch);
        out.clear();
        for (int c : scratch) out.push_back({V[nxt(c)], E[prv(c)] != 0});
        if (!closed && !scratch.empty()) {
            // the fan starts at a border edge towards V[prev(first)]; no other corner lists that neighbour
            const int first = scratch.front();
            out.push_back({V[prv(first)], E[nxt(first)] != 0});
        }
        return closed;
    }

    int valence(int v, std::vector<int>& scratch) const {
        const bool closed = orbit(v, scratch);
        return static_cast<int>(scratch.size()) + (closed ? 0 : 1);
    }

    bool isBoundaryVertex(int v, std::vector<int>& scratch) const { return !orbit(v, scratch); }

    Vec3 faceNormalUnnormalized(int f) const {
        return cross(P[V[3 * f + 1]] - P[V[3 * f]], P[V[3 * f + 2]] - P[V[3 * f]]);
    }

    // ----------------------------------------------------------- vertex classification
    void classifyVertices(double cornerAngleDeg) {
        const int nV = numVertSlots();
        vtype.assign(nV, V_FREE);
        std::vector<int> featCount(nV, 0);
        for (int c = 0; c < numFaceSlots() * 3; ++c) {
            if (!falive[faceOf(c)] || !E[c]) continue;
            if (O[c] >= 0 && O[c] < c) continue;  // count each edge once
            ++featCount[V[nxt(c)]];
            ++featCount[V[prv(c)]];
        }
        const double cosLimit = std::cos(cornerAngleDeg * kPi / 180.0);
        std::vector<int> scratch;
        std::vector<Nb> nbs;
        for (int v = 0; v < nV; ++v) {
            if (!valive[v]) continue;
            if (featCount[v] == 0) continue;
            if (featCount[v] != 2) { vtype[v] = V_FIXED; continue; }
            neighbors(v, nbs, scratch);
            Vec3 d[2];
            int n = 0;
            for (const Nb& nb : nbs)
                if (nb.feature && n < 2) d[n++] = normalized(P[nb.v] - P[v]);
            if (n == 2 && dot(d[0], d[1]) > -cosLimit) vtype[v] = V_FIXED;  // sharp turn of the line
            else vtype[v] = V_CREASE;
        }
    }

    // ----------------------------------------------------------- local operations
    int addVertex(const Vec3& p, float dens, uint8_t type) {
        P.push_back(p); vcorner.push_back(-1); vtype.push_back(type);
        valive.push_back(1); vdens.push_back(dens);
        ++aliveVerts;
        return static_cast<int>(P.size()) - 1;
    }
    int addFace() {
        const int f = numFaceSlots();
        V.resize(V.size() + 3, -1);
        O.resize(O.size() + 3, -1);
        E.resize(E.size() + 3, 0);
        falive.push_back(1);
        ++aliveFaces;
        return f;
    }
    void killFace(int f) {
        falive[f] = 0;
        --aliveFaces;
        for (int k = 0; k < 3; ++k) { V[3 * f + k] = -1; O[3 * f + k] = -1; E[3 * f + k] = 0; }
    }

    // Flip the edge opposite corner c. The caller checks that it is legal.
    void flipEdge(int c) {
        const int d = O[c];
        const int n1 = nxt(c), p1 = prv(c), n2 = nxt(d), p2 = prv(d);
        const int A = V[c], B = V[n1], C = V[p1], D = V[d];
        const int oBD = O[n2], oCA = O[n1];
        const uint8_t eBD = E[n2], eCA = E[n1];
        V[p1] = D;
        V[p2] = A;
        O[n1] = n2; O[n2] = n1; E[n1] = 0; E[n2] = 0;
        O[c] = oBD; if (oBD >= 0) O[oBD] = c; E[c] = eBD;
        O[d] = oCA; if (oCA >= 0) O[oCA] = d; E[d] = eCA;
        vcorner[A] = c; vcorner[B] = n1; vcorner[C] = n2; vcorner[D] = d;
    }

    // Split the edge opposite corner c at position m. Returns the new vertex.
    int splitEdge(int c, const Vec3& m, float dens) {
        const int d = O[c];
        const int n1 = nxt(c), p1 = prv(c);
        const int A = V[c], B = V[n1], C = V[p1];
        const bool feat = E[c] != 0;
        const int oCA = O[n1];
        const uint8_t eCA = E[n1];
        const int M = addVertex(m, dens, feat ? V_CREASE : V_FREE);
        const int f3 = addFace();
        const int r0 = 3 * f3, r1 = r0 + 1, r2 = r0 + 2;
        V[r0] = A; V[r1] = M; V[r2] = C;
        V[p1] = M;
        O[n1] = r2; O[r2] = n1; E[n1] = 0; E[r2] = 0;
        O[r1] = oCA; if (oCA >= 0) O[oCA] = r1; E[r1] = eCA;
        if (d >= 0) {
            const int n2 = nxt(d), p2 = prv(d);
            const int D = V[d];
            const int oBD = O[n2];
            const uint8_t eBD = E[n2];
            const int f4 = addFace();
            const int q0 = 3 * f4, q1 = q0 + 1, q2 = q0 + 2;
            V[q0] = D; V[q1] = M; V[q2] = B;
            V[p2] = M;
            O[c] = q0; O[q0] = c; E[c] = feat; E[q0] = feat;
            O[r0] = d; O[d] = r0; E[r0] = feat; E[d] = feat;
            O[n2] = q2; O[q2] = n2; E[n2] = 0; E[q2] = 0;
            O[q1] = oBD; if (oBD >= 0) O[oBD] = q1; E[q1] = eBD;
            vcorner[A] = c; vcorner[B] = n1; vcorner[C] = n2; vcorner[D] = d; vcorner[M] = p1;
        } else {
            O[c] = -1; E[c] = 1;
            O[r0] = -1; E[r0] = 1;
            vcorner[A] = c; vcorner[B] = n1; vcorner[C] = r2; vcorner[M] = p1;
        }
        return M;
    }

    // Collapse the edge opposite corner c: vertex `rem` is merged into `keep` (placed at newPos).
    void collapseEdge(int c, int keep, int rem, const Vec3& newPos) {
        std::vector<int> remCorners, keepCorners;
        orbit(rem, remCorners);
        orbit(keep, keepCorners);
        const int d = O[c];
        const int n1 = nxt(c), p1 = prv(c);
        const int A = V[c];
        const int oAB = O[p1], oCA = O[n1];
        const uint8_t eAB = E[p1], eCA = E[n1];
        int D = -1, oBD = -1, oDC = -1;
        uint8_t eBD = 0, eDC = 0;
        if (d >= 0) {
            const int n2 = nxt(d), p2 = prv(d);
            D = V[d];
            oBD = O[n2]; oDC = O[p2];
            eBD = E[n2]; eDC = E[p2];
        }
        glue(oAB, eAB, oCA, eCA);
        killFace(faceOf(c));
        if (d >= 0) {
            glue(oBD, eBD, oDC, eDC);
            killFace(faceOf(d));
        }
        for (int k : remCorners) if (falive[faceOf(k)]) V[k] = keep;
        P[keep] = newPos;
        valive[rem] = 0; vcorner[rem] = -1; --aliveVerts;
        // keep a live corner for every vertex that touched the removed faces
        vcorner[keep] = -1;
        for (int k : keepCorners) if (falive[faceOf(k)]) { vcorner[keep] = k; break; }
        if (vcorner[keep] < 0)
            for (int k : remCorners) if (falive[faceOf(k)]) { vcorner[keep] = k; break; }
        if (vcorner[A] >= 0 && !falive[faceOf(vcorner[A])]) {
            if (oAB >= 0) vcorner[A] = prv(oAB);
            else if (oCA >= 0) vcorner[A] = nxt(oCA);
        }
        if (D >= 0 && !falive[faceOf(vcorner[D])]) {
            if (oBD >= 0) vcorner[D] = nxt(oBD);
            else if (oDC >= 0) vcorner[D] = prv(oDC);
        }
    }

    // Remove dead faces/vertices and renumber. Keeps relative order.
    void compact() {
        const int nF = numFaceSlots();
        const int nV = numVertSlots();
        std::vector<int> fmap(nF, -1), vmap(nV, -1);
        int nf = 0, nv = 0;
        for (int f = 0; f < nF; ++f) if (falive[f]) fmap[f] = nf++;
        for (int v = 0; v < nV; ++v) if (valive[v]) vmap[v] = nv++;
        std::vector<int> V2(3 * nf), O2(3 * nf, -1);
        std::vector<uint8_t> E2(3 * nf);
        for (int f = 0; f < nF; ++f) {
            if (!falive[f]) continue;
            for (int k = 0; k < 3; ++k) {
                const int c = 3 * f + k, c2 = 3 * fmap[f] + k;
                V2[c2] = vmap[V[c]];
                E2[c2] = E[c];
                O2[c2] = O[c] < 0 ? -1 : 3 * fmap[faceOf(O[c])] + (O[c] % 3);
            }
        }
        std::vector<Vec3> P2(nv);
        std::vector<int> vc2(nv);
        std::vector<uint8_t> vt2(nv);
        std::vector<float> vd2(nv);
        for (int v = 0; v < nV; ++v) {
            if (!valive[v]) continue;
            const int c = vcorner[v];
            P2[vmap[v]] = P[v];
            vc2[vmap[v]] = 3 * fmap[faceOf(c)] + (c % 3);
            vt2[vmap[v]] = vtype[v];
            vd2[vmap[v]] = vdens[v];
        }
        P.swap(P2); vcorner.swap(vc2); vtype.swap(vt2); vdens.swap(vd2);
        valive.assign(nv, 1);
        V.swap(V2); O.swap(O2); E.swap(E2);
        falive.assign(nf, 1);
        aliveFaces = nf; aliveVerts = nv;
    }

    // Full consistency check; returns an empty string when the mesh is valid.
    std::string check() const {
        const int nF = numFaceSlots();
        std::vector<int> cornersPerVertex(numVertSlots(), 0);
        int liveFaces = 0;
        for (int f = 0; f < nF; ++f) {
            if (!falive[f]) continue;
            ++liveFaces;
            for (int k = 0; k < 3; ++k) {
                const int c = 3 * f + k;
                const int v = V[c];
                if (v < 0 || v >= numVertSlots() || !valive[v]) return "corner " + std::to_string(c) + " has a dead vertex";
                ++cornersPerVertex[v];
                if (V[nxt(c)] == V[prv(c)] || v == V[nxt(c)]) return "degenerate face " + std::to_string(f);
                const int d = O[c];
                if (E[c]) {
                    if (vtype[V[nxt(c)]] == V_FREE || vtype[V[prv(c)]] == V_FREE)
                        return "feature edge touches a free vertex at corner " + std::to_string(c);
                }
                if (d < 0) { if (!E[c]) return "border edge not flagged as feature"; continue; }
                if (!falive[faceOf(d)]) return "opposite corner in a dead face";
                if (O[d] != c) return "opposite is not symmetric at corner " + std::to_string(c);
                if (V[nxt(c)] != V[prv(d)] || V[prv(c)] != V[nxt(d)]) return "opposite edge mismatch at corner " + std::to_string(c);
                if (E[c] != E[d]) return "feature flag mismatch at corner " + std::to_string(c);
            }
        }
        if (liveFaces != aliveFaces) return "alive face counter is wrong";
        int liveVerts = 0;
        std::vector<int> scratch;
        for (int v = 0; v < numVertSlots(); ++v) {
            if (!valive[v]) continue;
            ++liveVerts;
            const int c = vcorner[v];
            if (c < 0 || !falive[faceOf(c)] || V[c] != v) return "bad vertex corner for vertex " + std::to_string(v);
            orbit(v, scratch);
            if (static_cast<int>(scratch.size()) != cornersPerVertex[v])
                return "vertex " + std::to_string(v) + " has more than one fan (non-manifold)";
        }
        if (liveVerts != aliveVerts) return "alive vertex counter is wrong";
        return {};
    }

    // Export the live faces as indexed triangles (vertices renumbered densely).
    void exportTriangles(PolyMesh& out) const {
        std::vector<int> vmap(numVertSlots(), -1);
        int nv = 0;
        for (int v = 0; v < numVertSlots(); ++v) if (valive[v]) vmap[v] = nv++;
        out.P.resize(nv);
        for (int v = 0; v < numVertSlots(); ++v) if (valive[v]) out.P[vmap[v]] = P[v];
        out.sizes.clear(); out.idx.clear();
        for (int f = 0; f < numFaceSlots(); ++f) {
            if (!falive[f]) continue;
            out.sizes.push_back(3);
            for (int k = 0; k < 3; ++k) out.idx.push_back(vmap[V[3 * f + k]]);
        }
        out.density.clear();
    }

private:
    void glue(int x, uint8_t ex, int y, uint8_t ey) {
        if (x >= 0 && y >= 0) {
            O[x] = y; O[y] = x;
            E[x] = E[y] = static_cast<uint8_t>(ex | ey);
        } else if (x >= 0) {
            O[x] = -1; E[x] = 1;
        } else if (y >= 0) {
            O[y] = -1; E[y] = 1;
        }
    }
};

}  // namespace forge
