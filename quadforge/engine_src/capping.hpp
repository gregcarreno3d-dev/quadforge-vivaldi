// Forge engine - close open borders with caps so that the closed-surface machinery can run.
//
// Every boundary loop gets a fan of triangles around its centroid. Loops long enough to matter are
// marked as removable: their border edges become feature lines (the grid aligns to them) and the
// cap triangles are labelled so that the quads on the cap can be dropped afterwards. Short loops
// are simply left closed.
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <unordered_map>
#include <vector>

#include "repair.hpp"

namespace forge {

struct CapInfo {
    int loops = 0, removable = 0;
    size_t originalFaces = 0;
    std::vector<uint8_t> removableFace;     // per face of the capped soup: 1 = cap to drop
};


// Isolated flat slivers: a triangle whose three edges are all open and whose vertices lie on a line has no
// area and belongs to no surface (a leftover of a bad weld). It would break the grid, so it is removed.
inline int stitchSlits(TriSoup& soup) {
    std::unordered_map<uint64_t, int> directed;
    auto key = [](int a, int b) { return (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b); };
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) directed[key(t[k], t[(k + 1) % 3])]++;
    std::vector<std::array<int, 3>> keep;
    keep.reserve(soup.F.size());
    int removed = 0;
    for (const auto& t : soup.F) {
        bool allOpen = true;
        for (int k = 0; k < 3; ++k) if (directed.count(key(t[(k + 1) % 3], t[k]))) allOpen = false;
        if (allOpen) {
            const double a = distance(soup.P[t[0]], soup.P[t[1]]), b = distance(soup.P[t[1]], soup.P[t[2]]), c = distance(soup.P[t[2]], soup.P[t[0]]);
            const double longest = std::max(a, std::max(b, c));
            if (a + b + c - longest <= 1.02 * longest) { ++removed; continue; }
        }
        keep.push_back(t);
    }
    if (removed) soup.F = std::move(keep);
    return removed;
}

// Returns false (soup untouched) when the border is not made of simple loops.
inline bool capBorders(TriSoup& soup, double minLoopLength, CapInfo& info) {
    info = CapInfo();
    info.originalFaces = soup.F.size();
    std::unordered_map<uint64_t, int> directed;
    auto key = [](int a, int b) { return (static_cast<uint64_t>(static_cast<uint32_t>(a)) << 32) | static_cast<uint32_t>(b); };
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) directed[key(t[k], t[(k + 1) % 3])]++;
    std::unordered_map<int, int> nextOf;       // boundary edge a -> b
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) {
            const int a = t[k], b = t[(k + 1) % 3];
            if (directed.count(key(b, a))) continue;
            if (nextOf.count(a)) return false;     // a vertex with two outgoing border edges
            nextOf[a] = b;
        }
    if (nextOf.empty()) return true;
    std::vector<std::vector<int>> loops;
    std::unordered_map<int, uint8_t> seen;
    for (const auto& kv : nextOf) {
        if (seen[kv.first]) continue;
        std::vector<int> loop;
        int v = kv.first;
        while (!seen[v]) {
            seen[v] = 1;
            loop.push_back(v);
            auto it = nextOf.find(v);
            if (it == nextOf.end()) return false;
            v = it->second;
        }
        if (v != kv.first || loop.size() < 3) return false;
        loops.push_back(std::move(loop));
    }
    TriSoup out = soup;
    info.removableFace.assign(soup.F.size(), 0);
    for (const auto& loop : loops) {
        double len = 0;
        Vec3 c;
        float dens = 0;
        for (size_t i = 0; i < loop.size(); ++i) {
            const int a = loop[i], b = loop[(i + 1) % loop.size()];
            len += distance(soup.P[a], soup.P[b]);
            c += soup.P[a];
            if (!soup.density.empty()) dens += soup.density[a];
        }
        c = c / static_cast<double>(loop.size());
        const int cv = static_cast<int>(out.P.size());
        out.P.push_back(c);
        if (!soup.density.empty()) out.density.push_back(dens / static_cast<float>(loop.size()));
        const bool removable = len >= minLoopLength;
        for (size_t i = 0; i < loop.size(); ++i) {
            const int a = loop[i], b = loop[(i + 1) % loop.size()];
            out.F.push_back({b, a, cv});
            info.removableFace.push_back(removable ? 1 : 0);
            if (removable) out.features.push_back({a, b});
        }
        ++info.loops;
        if (removable) ++info.removable;
    }
    soup = std::move(out);
    return true;
}

}  // namespace forge
