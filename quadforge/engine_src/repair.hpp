// Forge engine - turn arbitrary polygon soup into a valid manifold triangle mesh.
//
// What it does (and reports):
//   * triangulates polygons (quads along the shorter diagonal),
//   * drops faces that repeat a vertex and exact duplicate faces,
//   * orients every connected piece consistently,
//   * unlinks non-manifold edges (they become open borders, which are preserved),
//   * duplicates "pinched" vertices so every vertex has a single fan of faces,
//   * removes unused vertices.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <numeric>
#include <unordered_set>
#include <vector>

#include "meshio.hpp"
#include "vec.hpp"

namespace forge {

struct TriSoup {
    std::vector<Vec3> P;
    std::vector<std::array<int, 3>> F;
    std::vector<std::array<int, 2>> features;  // on repaired vertex ids
    std::vector<float> density;                // empty when not given
};

struct RepairReport {
    int inputPolygons = 0;
    int repeatedVertexFaces = 0;
    int duplicateFaces = 0;
    int nonManifoldEdges = 0;
    int conflictingEdges = 0;
    int flippedFaces = 0;
    int pinchedVertices = 0;
    int collisionFaces = 0;
    int unusedVertices = 0;
};

namespace detail {

struct DisjointSet {
    std::vector<int> parent;
    explicit DisjointSet(size_t n) : parent(n) { std::iota(parent.begin(), parent.end(), 0); }
    int find(int x) {
        while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x;
    }
    void unite(int a, int b) {
        a = find(a); b = find(b);
        if (a != b) parent[std::max(a, b)] = std::min(a, b);
    }
};

inline uint64_t edgeKey(int a, int b) {
    if (a > b) std::swap(a, b);
    return (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b);
}

struct EdgeRec {
    uint64_t key;
    int face;
    int corner;  // edge is opposite this corner
    int dir;     // 0 if the face walks the edge from the smaller to the larger vertex id
};

inline std::vector<EdgeRec> collectEdges(const std::vector<std::array<int, 3>>& F,
                                         const std::vector<uint8_t>* alive = nullptr) {
    std::vector<EdgeRec> e;
    e.reserve(F.size() * 3);
    for (size_t f = 0; f < F.size(); ++f) {
        if (alive && !(*alive)[f]) continue;
        for (int k = 0; k < 3; ++k) {
            const int a = F[f][(k + 1) % 3], b = F[f][(k + 2) % 3];
            e.push_back({edgeKey(a, b), static_cast<int>(f), k, a < b ? 0 : 1});
        }
    }
    std::sort(e.begin(), e.end(), [](const EdgeRec& x, const EdgeRec& y) {
        return x.key != y.key ? x.key < y.key : (x.face != y.face ? x.face < y.face : x.corner < y.corner);
    });
    return e;
}

}  // namespace detail

inline TriSoup repairMesh(const PolyMesh& in, RepairReport& rep) {
    using namespace detail;
    TriSoup out;
    out.P = in.P;
    const int nV0 = static_cast<int>(in.P.size());
    rep.inputPolygons = static_cast<int>(in.sizes.size());

    // 1. triangulate
    std::vector<std::array<int, 3>> F;
    F.reserve(in.sizes.size() * 2);
    {
        size_t at = 0;
        for (int s : in.sizes) {
            const int* v = &in.idx[at];
            at += static_cast<size_t>(s);
            if (s == 3) {
                F.push_back({v[0], v[1], v[2]});
            } else if (s == 4) {
                const double d02 = distance2(in.P[v[0]], in.P[v[2]]);
                const double d13 = distance2(in.P[v[1]], in.P[v[3]]);
                if (d02 <= d13) { F.push_back({v[0], v[1], v[2]}); F.push_back({v[0], v[2], v[3]}); }
                else { F.push_back({v[0], v[1], v[3]}); F.push_back({v[1], v[2], v[3]}); }
            } else {
                for (int i = 1; i + 1 < s; ++i) F.push_back({v[0], v[i], v[i + 1]});
            }
        }
    }

    // 2. repeated vertices and duplicate faces
    {
        std::vector<std::array<int, 3>> kept;
        kept.reserve(F.size());
        for (auto& t : F) {
            if (t[0] == t[1] || t[1] == t[2] || t[0] == t[2]) { ++rep.repeatedVertexFaces; continue; }
            kept.push_back(t);
        }
        std::vector<std::pair<std::array<int, 3>, int>> sorted(kept.size());
        for (size_t i = 0; i < kept.size(); ++i) {
            auto s = kept[i];
            std::sort(s.begin(), s.end());
            sorted[i] = {s, static_cast<int>(i)};
        }
        std::sort(sorted.begin(), sorted.end());
        std::vector<uint8_t> drop(kept.size(), 0);
        for (size_t i = 1; i < sorted.size(); ++i)
            if (sorted[i].first == sorted[i - 1].first) { drop[sorted[i].second] = 1; ++rep.duplicateFaces; }
        F.clear();
        for (size_t i = 0; i < kept.size(); ++i) if (!drop[i]) F.push_back(kept[i]);
    }

    // 3. orientation and edge linking
    const size_t nF = F.size();
    std::vector<EdgeRec> edges = collectEdges(F);
    struct Group { size_t begin, end; };
    std::vector<Group> groups;
    for (size_t i = 0; i < edges.size();) {
        size_t j = i;
        while (j < edges.size() && edges[j].key == edges[i].key) ++j;
        groups.push_back({i, j});
        i = j;
    }
    struct Adj { int face; int rel; };
    std::vector<std::vector<Adj>> adj(nF);
    std::vector<uint8_t> linkable(groups.size(), 0);
    for (size_t gi = 0; gi < groups.size(); ++gi) {
        const size_t n = groups[gi].end - groups[gi].begin;
        if (n == 2) {
            const EdgeRec &a = edges[groups[gi].begin], &b = edges[groups[gi].begin + 1];
            if (a.face != b.face) {
                const int rel = (a.dir == b.dir) ? 1 : 0;
                adj[a.face].push_back({b.face, rel});
                adj[b.face].push_back({a.face, rel});
                linkable[gi] = 1;
            }
        } else if (n > 2) {
            ++rep.nonManifoldEdges;
        }
    }
    std::vector<int8_t> flip(nF, -1);
    {
        std::vector<int> queue;
        for (size_t s = 0; s < nF; ++s) {
            if (flip[s] != -1) continue;
            flip[s] = 0;
            queue.assign(1, static_cast<int>(s));
            for (size_t qi = 0; qi < queue.size(); ++qi) {
                const int f = queue[qi];
                for (const Adj& a : adj[f]) {
                    if (flip[a.face] == -1) { flip[a.face] = flip[f] ^ a.rel; queue.push_back(a.face); }
                }
            }
        }
    }
    for (size_t f = 0; f < nF; ++f)
        if (flip[f] == 1) { std::swap(F[f][1], F[f][2]); ++rep.flippedFaces; }
    // after orientation, an edge is linked only if the two faces now walk it in opposite directions
    edges = collectEdges(F);
    groups.clear();
    for (size_t i = 0; i < edges.size();) {
        size_t j = i;
        while (j < edges.size() && edges[j].key == edges[i].key) ++j;
        groups.push_back({i, j});
        i = j;
    }
    detail::DisjointSet ds(nF * 3);
    for (const Group& g : groups) {
        const size_t n = g.end - g.begin;
        if (n != 2) continue;
        const EdgeRec &a = edges[g.begin], &b = edges[g.begin + 1];
        if (a.face == b.face) continue;
        if (a.dir == b.dir) { ++rep.conflictingEdges; continue; }  // non-orientable: leave open
        // vertices of the edge, seen from face a: corners (ka+1) and (ka+2)
        ds.unite(a.face * 3 + (a.corner + 1) % 3, b.face * 3 + (b.corner + 2) % 3);
        ds.unite(a.face * 3 + (a.corner + 2) % 3, b.face * 3 + (b.corner + 1) % 3);
    }

    // 4. split pinched vertices: one new vertex per group of linked corners
    std::vector<int> rootToVertex(nF * 3, -1);
    std::vector<uint8_t> vertexUsed(nV0, 0);
    std::vector<int> origin(nV0);
    std::iota(origin.begin(), origin.end(), 0);
    for (size_t f = 0; f < nF; ++f) {
        for (int k = 0; k < 3; ++k) {
            const int root = ds.find(static_cast<int>(f) * 3 + k);
            if (rootToVertex[root] != -1) continue;
            const int v = F[f][k];
            if (!vertexUsed[v]) { vertexUsed[v] = 1; rootToVertex[root] = v; }
            else {
                rootToVertex[root] = static_cast<int>(out.P.size());
                out.P.push_back(in.P[v]);
                origin.push_back(v);
                ++rep.pinchedVertices;
            }
        }
    }
    std::vector<std::array<int, 3>> G(nF);
    for (size_t f = 0; f < nF; ++f)
        for (int k = 0; k < 3; ++k) G[f][k] = rootToVertex[ds.find(static_cast<int>(f) * 3 + k)];

    // 5. faces that still share a directed edge (extremely rare) are dropped
    std::vector<uint8_t> alive(nF, 1);
    for (int round = 0; round < 4; ++round) {
        std::vector<std::pair<uint64_t, int>> dir;
        dir.reserve(nF * 3);
        for (size_t f = 0; f < nF; ++f) {
            if (!alive[f]) continue;
            for (int k = 0; k < 3; ++k) {
                const uint64_t a = static_cast<uint32_t>(G[f][(k + 1) % 3]);
                const uint64_t b = static_cast<uint32_t>(G[f][(k + 2) % 3]);
                dir.push_back({(a << 32) | b, static_cast<int>(f)});
            }
        }
        std::sort(dir.begin(), dir.end());
        bool any = false;
        for (size_t i = 1; i < dir.size(); ++i)
            if (dir[i].first == dir[i - 1].first && alive[dir[i].second]) {
                alive[dir[i].second] = 0; ++rep.collisionFaces; any = true;
            }
        if (!any) break;
    }

    // 6. features: map original vertex pairs onto the repaired faces
    std::unordered_set<uint64_t> featureSet;
    featureSet.reserve(in.features.size() * 2);
    for (const auto& e : in.features) featureSet.insert(edgeKey(e[0], e[1]));
    std::vector<uint64_t> featNew;
    for (size_t f = 0; f < nF; ++f) {
        if (!alive[f]) continue;
        for (int k = 0; k < 3; ++k) {
            const int a = G[f][(k + 1) % 3], b = G[f][(k + 2) % 3];
            if (featureSet.count(edgeKey(origin[a], origin[b]))) featNew.push_back(edgeKey(a, b));
        }
    }
    std::sort(featNew.begin(), featNew.end());
    featNew.erase(std::unique(featNew.begin(), featNew.end()), featNew.end());

    // 7. compact: keep only used vertices
    std::vector<int> remap(out.P.size(), -1);
    int nextId = 0;
    for (size_t f = 0; f < nF; ++f) {
        if (!alive[f]) continue;
        for (int k = 0; k < 3; ++k) if (remap[G[f][k]] == -1) remap[G[f][k]] = nextId++;
    }
    std::vector<Vec3> P2(nextId);
    std::vector<float> dens2;
    if (!in.density.empty()) dens2.resize(nextId);
    for (size_t v = 0; v < out.P.size(); ++v)
        if (remap[v] != -1) {
            P2[remap[v]] = out.P[v];
            if (!dens2.empty()) dens2[remap[v]] = in.density[origin[v]];
        }
    rep.unusedVertices = static_cast<int>(out.P.size()) - nextId;
    out.P.swap(P2);
    out.density.swap(dens2);
    for (size_t f = 0; f < nF; ++f)
        if (alive[f]) out.F.push_back({remap[G[f][0]], remap[G[f][1]], remap[G[f][2]]});
    for (uint64_t key : featNew) {
        const int a = static_cast<int>(key >> 32), b = static_cast<int>(key & 0xffffffffu);
        if (remap[a] != -1 && remap[b] != -1) out.features.push_back({remap[a], remap[b]});
    }
    return out;
}

}  // namespace forge
