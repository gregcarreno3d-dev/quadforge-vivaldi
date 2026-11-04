// Forge engine - pole optimisation on a pure quad mesh.
//
// One move only: the "chord rotation". Two quads that share an edge (a,b) form a hexagon;
// the shared edge can be replaced by either of the two other chords that split the
// hexagon into two quads. The move lowers the valence of a and b by one and raises the
// valence of the two new chord ends. Simulated annealing over these moves lets poles
// travel and cancel (a 3 next to a 5 disappears after a few moves) while a geometric
// term keeps the quads square.
#pragma once
#include <array>
#include <cmath>
#include <random>
#include <unordered_map>
#include <vector>

#include "vec.hpp"

namespace forge {

struct QuadOptStats {
    int moves = 0, poleCostBefore = 0, poleCostAfter = 0;
};

inline void optimizePoles(const std::vector<Vec3>& P, std::vector<std::array<int, 4>>& F, double movesPerFace,
                          double geomWeight, unsigned seed, QuadOptStats& st) {
    const int nV = static_cast<int>(P.size());
    const int nF = static_cast<int>(F.size());
    if (nF < 2) return;
    auto key = [](int a, int b) { return (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b); };
    std::unordered_map<uint64_t, int> he;
    he.reserve(static_cast<size_t>(nF) * 8);
    for (int f = 0; f < nF; ++f)
        for (int k = 0; k < 4; ++k) he[key(F[f][k], F[f][(k + 1) % 4])] = f;
    std::vector<std::vector<int>> nbr(nV);
    std::vector<uint8_t> bnd(nV, 0);
    auto addN = [&](int a, int b) { if (std::find(nbr[a].begin(), nbr[a].end(), b) == nbr[a].end()) nbr[a].push_back(b); };
    for (int f = 0; f < nF; ++f)
        for (int k = 0; k < 4; ++k) {
            const int a = F[f][k], b = F[f][(k + 1) % 4];
            addN(a, b); addN(b, a);
            if (!he.count(key(b, a))) { bnd[a] = 1; bnd[b] = 1; }
        }
    auto cost = [&](int v, int val) { return bnd[v] ? 0 : std::abs(val - 4); };
    for (int v = 0; v < nV; ++v) st.poleCostBefore += cost(v, static_cast<int>(nbr[v].size()));

    auto cornerTerm = [&](const Vec3& p0, const Vec3& pm, const Vec3& p1, const Vec3& n) {
        const Vec3 u = p0 - pm, w = p1 - pm;
        const double lu = length(u), lw = length(w);
        if (lu < 1e-12 || lw < 1e-12) return 10.0;
        const double c = dot(u, w) / (lu * lw);
        double t = c * c;
        const double s = dot(cross(w, u), n);   // orientation of the corner, positive when convex
        if (s <= 0) t += 4.0 + 4.0 * (-s / (lu * lw));
        return t;
    };
    auto geom = [&](const std::array<int, 4>& q, const Vec3& n) {
        double e = 0;
        for (int k = 0; k < 4; ++k) e += cornerTerm(P[q[(k + 3) % 4]], P[q[k]], P[q[(k + 1) % 4]], n);
        return e;
    };
    auto faceNormal = [&](const std::array<int, 4>& q) {
        return cross(P[q[2]] - P[q[0]], P[q[3]] - P[q[1]]);
    };

    std::mt19937 rng(seed);
    std::uniform_real_distribution<double> U(0.0, 1.0);
    const long total = static_cast<long>(movesPerFace * nF);
    for (long it = 0; it < total; ++it) {
        const double T = 0.6 * std::pow(0.03 / 0.6, static_cast<double>(it) / std::max<long>(1, total));
        const int f = static_cast<int>(rng() % nF);
        const int k = static_cast<int>(rng() & 3);
        const int a = F[f][k], b = F[f][(k + 1) % 4];
        if (bnd[a] || bnd[b]) continue;
        const auto gi = he.find(key(b, a));
        if (gi == he.end()) continue;
        const int g = gi->second;
        const int p = F[f][(k + 2) % 4], q = F[f][(k + 3) % 4];
        int j = 0;
        while (j < 4 && !(F[g][j] == b && F[g][(j + 1) % 4] == a)) ++j;
        if (j == 4) continue;
        const int r = F[g][(j + 2) % 4], s = F[g][(j + 3) % 4];
        const int variant = static_cast<int>(rng() & 1);
        const int c1 = variant ? s : r, c2 = variant ? q : p;
        // distinct vertices
        const int six[6] = {a, b, p, q, r, s};
        bool distinct = true;
        for (int x = 0; x < 6 && distinct; ++x) for (int y = x + 1; y < 6; ++y) if (six[x] == six[y]) { distinct = false; break; }
        if (!distinct) continue;
        if (bnd[c1] || bnd[c2]) continue;
        if (std::find(nbr[c1].begin(), nbr[c1].end(), c2) != nbr[c1].end()) continue;
        const int va = static_cast<int>(nbr[a].size()) - 1, vb = static_cast<int>(nbr[b].size()) - 1;
        const int v1 = static_cast<int>(nbr[c1].size()) + 1, v2 = static_cast<int>(nbr[c2].size()) + 1;
        if (va < 3 || vb < 3 || v1 > 7 || v2 > 7) continue;
        const double dPole = (cost(a, va) + cost(b, vb) + cost(c1, v1) + cost(c2, v2)) -
                             (cost(a, va + 1) + cost(b, vb + 1) + cost(c1, v1 - 1) + cost(c2, v2 - 1));
        std::array<int, 4> n1, n2;
        if (variant == 0) { n1 = {r, s, b, p}; n2 = {p, q, a, r}; }
        else              { n1 = {s, b, p, q}; n2 = {q, a, r, s}; }
        Vec3 nref = faceNormal(F[f]) + faceNormal(F[g]);
        const double ln = length(nref);
        if (ln < 1e-20) continue;
        nref = nref / ln;
        const double dGeom = geom(n1, nref) + geom(n2, nref) - geom(F[f], nref) - geom(F[g], nref);
        const double dE = dPole + geomWeight * dGeom;
        if (dE > 0 && U(rng) > std::exp(-dE / T)) continue;
        // apply
        for (int x = 0; x < 4; ++x) { he.erase(key(F[f][x], F[f][(x + 1) % 4])); he.erase(key(F[g][x], F[g][(x + 1) % 4])); }
        F[f] = n1; F[g] = n2;
        for (int x = 0; x < 4; ++x) { he[key(F[f][x], F[f][(x + 1) % 4])] = f; he[key(F[g][x], F[g][(x + 1) % 4])] = g; }
        nbr[a].erase(std::find(nbr[a].begin(), nbr[a].end(), b));
        nbr[b].erase(std::find(nbr[b].begin(), nbr[b].end(), a));
        nbr[c1].push_back(c2);
        nbr[c2].push_back(c1);
        ++st.moves;
    }
    for (int v = 0; v < nV; ++v) st.poleCostAfter += cost(v, static_cast<int>(nbr[v].size()));
}

}  // namespace forge
