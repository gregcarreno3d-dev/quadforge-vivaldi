// Forge engine - adaptive element size from the local thickness of the shape.
//
// For every vertex the "shrinking ball" method finds the radius r of the largest ball that touches
// the surface at the vertex and stays inside the shape (half the local thickness of a tube or plate).
// A tube of radius a needs about eight quads around it, so the quad edge is limited to cT * r.
// The result is a per-vertex edge-length multiplier s in [minScale, 1] (1 = the global size) that is
// graded so that the size never changes faster than `grade` per unit of distance.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <vector>

#include "bvh.hpp"
#include "parallel.hpp"
#include "repair.hpp"

namespace forge {

struct SizeFieldParams {
    double rho = 0;          // global quad edge length estimate
    double ratio = 0.8;      // quad edge <= ratio * local ball radius
    double minScale = 0.3;   // never finer than this fraction of the global size
    double grade = 0.5;      // size growth per unit distance
    double minCurv = 0.5;    // only refine where curvature * ball radius exceeds this (tubes, not flat walls)
    int threads = 1;
};

struct SizeFieldStats {
    int refined = 0;         // vertices whose size was reduced
    double minSeen = 1.0;
};

inline void gradeSizeField(const TriSoup& soup, std::vector<float>& s, const SizeFieldParams& sp) {
    // gradation: s_i <= s_j + grade * |e| / rho
    std::vector<std::array<int, 2>> edges;
    edges.reserve(soup.F.size() * 3);
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) edges.push_back({t[k], t[(k + 1) % 3]});
    for (int sweep = 0; sweep < 60; ++sweep) {
        bool changed = false;
        for (const auto& e : edges) {
            const double l = distance(soup.P[e[0]], soup.P[e[1]]) * sp.grade / sp.rho;
            if (s[e[0]] > s[e[1]] + l + 1e-6) { s[e[0]] = static_cast<float>(s[e[1]] + l); changed = true; }
            if (s[e[1]] > s[e[0]] + l + 1e-6) { s[e[1]] = static_cast<float>(s[e[0]] + l); changed = true; }
        }
        if (!changed) break;
    }
}

inline std::vector<float> thicknessSizeField(const TriSoup& soup, const SizeFieldParams& sp, SizeFieldStats& stats) {
    const int nV = static_cast<int>(soup.P.size());
    std::vector<float> s(nV, 1.0f);
    if (nV == 0 || soup.F.empty() || !(sp.rho > 0)) return s;
    // vertex normals (area weighted)
    std::vector<Vec3> N(nV);
    AABB box;
    for (const auto& p : soup.P) box.expand(p);
    for (const auto& t : soup.F) {
        const Vec3 n = cross(soup.P[t[1]] - soup.P[t[0]], soup.P[t[2]] - soup.P[t[0]]);
        for (int k = 0; k < 3; ++k) N[t[k]] += n;
    }
    for (auto& n : N) n = normalized(n);
    const double rmax = 0.5 * length(box.hi - box.lo);
    SurfaceIndex idx;
    idx.build(soup.P, soup.F);
    std::vector<double> radius(nV, rmax);
    // The size field is smooth, so on dense inputs the ball is measured on a sample of the vertices (about
    // kSamples of them) and the others take the value of the nearest sampled vertex along the surface.
    constexpr int kSamples = 40000;
    const int stride = std::max(1, nV / kSamples);
    std::vector<int> sampled;
    if (stride > 1) {
        sampled.reserve(nV / stride + 1);
        for (int v = 0; v < nV; ++v)
            if (static_cast<uint32_t>(v) * 2654435761u % static_cast<uint32_t>(stride) == 0) sampled.push_back(v);
    }
    const int nWork = stride > 1 ? static_cast<int>(sampled.size()) : nV;
    parallelFor(nWork, sp.threads, [&](int b, int e, int) {
        for (int w = b; w < e; ++w) {
            const int v = stride > 1 ? sampled[w] : w;
            const Vec3 p = soup.P[v], n = N[v];
            if (length2(n) < 0.5) continue;
            double r = rmax;
            for (int it = 0; it < 14; ++it) {
                const Vec3 q = p - n * r;
                const auto h = idx.closest(q);
                if (h.tri < 0) break;
                if (dot(idx.faceNormal(h.tri), n) > 0.5) break;   // the local neighbourhood, not the other side
                const Vec3 d = p - h.p;
                const double d2 = length2(d);
                if (d2 < 1e-12 * rmax * rmax) break;       // the ball touches only at p
                const double nd = dot(n, d);
                if (nd <= 1e-9 * std::sqrt(d2)) break;     // the closest point lies in front of the tangent plane
                const double rn = d2 / (2.0 * nd);
                if (rn >= r * 0.999) break;
                r = rn;
            }
            radius[v] = r;
        }
    });
    if (stride > 1) {
        // multi-source breadth-first fill from the sampled vertices
        std::vector<int> start(nV + 1, 0);
        for (const auto& t : soup.F)
            for (int k = 0; k < 3; ++k) start[t[k] + 1] += 2;
        for (int v = 0; v < nV; ++v) start[v + 1] += start[v];
        std::vector<int> adj(start[nV]), fillAt(start.begin(), start.end() - 1);
        for (const auto& t : soup.F)
            for (int k = 0; k < 3; ++k) {
                adj[fillAt[t[k]]++] = t[(k + 1) % 3];
                adj[fillAt[t[k]]++] = t[(k + 2) % 3];
            }
        std::vector<char> done(nV, 0);
        std::vector<int> frontier = sampled, next;
        for (int v : sampled) done[v] = 1;
        while (!frontier.empty()) {
            next.clear();
            for (int v : frontier)
                for (int k = start[v]; k < start[v + 1]; ++k) {
                    const int u = adj[k];
                    if (!done[u]) { done[u] = 1; radius[u] = radius[v]; next.push_back(u); }
                }
            frontier.swap(next);
        }
    }
    // curvature: a thin flat wall needs no resolution along itself, a thin tube does
    std::vector<double> kap(nV, 0.0), kapAvg(nV, 0.0);
    std::vector<int> kcount(nV, 0);
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) {
            const int a = t[k], b = t[(k + 1) % 3];
            const double l = distance(soup.P[a], soup.P[b]);
            if (l <= 0) continue;
            const double c = std::clamp(dot(N[a], N[b]), -1.0, 1.0);
            const double kk = std::acos(c) / l;
            kap[a] = std::max(kap[a], kk);
            kap[b] = std::max(kap[b], kk);
        }
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) { kapAvg[t[k]] += kap[t[(k + 1) % 3]] + kap[t[k]]; kcount[t[k]] += 2; }
    for (int v = 0; v < nV; ++v) if (kcount[v]) kap[v] = kapAvg[v] / kcount[v];
    for (int v = 0; v < nV; ++v) {
        if (kap[v] * radius[v] < sp.minCurv) { s[v] = 1.0f; continue; }
        const double hmax = sp.ratio * radius[v];
        s[v] = static_cast<float>(std::clamp(hmax / sp.rho, sp.minScale, 1.0));
    }
    gradeSizeField(soup, s, sp);
    for (int v = 0; v < nV; ++v) {
        if (s[v] < 0.999f) ++stats.refined;
        stats.minSeen = std::min<double>(stats.minSeen, s[v]);
    }
    return s;
}

// Smaller quads where the surface bends sharply (eyelids, lips, nostrils, ear rims): a quad edge should
// not turn the surface by more than `angle` radians, so its length is limited to angle / curvature.
inline std::vector<float> curvatureSizeField(const TriSoup& soup, const SizeFieldParams& sp, double angle, SizeFieldStats& stats) {
    const int nV = static_cast<int>(soup.P.size());
    std::vector<float> s(nV, 1.0f);
    if (nV == 0 || soup.F.empty() || !(sp.rho > 0) || !(angle > 0)) return s;
    std::vector<Vec3> N(nV);
    for (const auto& t : soup.F) {
        const Vec3 n = cross(soup.P[t[1]] - soup.P[t[0]], soup.P[t[2]] - soup.P[t[0]]);
        for (int k = 0; k < 3; ++k) N[t[k]] += n;
    }
    for (auto& n : N) n = normalized(n);
    std::vector<double> kap(nV, 0.0);
    std::vector<int> cnt(nV, 0);
    double lsum = 0; size_t lcount = 0;
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) {
            const int a = t[k], b = t[(k + 1) % 3];
            const double l = distance(soup.P[a], soup.P[b]);
            if (l <= 0) continue;
            const double kk = std::acos(std::clamp(dot(N[a], N[b]), -1.0, 1.0)) / l;
            kap[a] += kk; kap[b] += kk; ++cnt[a]; ++cnt[b];
            lsum += l; ++lcount;
        }
    for (int v = 0; v < nV; ++v) if (cnt[v]) kap[v] /= cnt[v];
    // smooth over about half a quad so that mesh noise does not count as curvature
    const double l0 = lcount ? lsum / lcount : sp.rho;
    int iters = static_cast<int>(std::clamp((0.5 * sp.rho / std::max(l0, 1e-12)) * (0.5 * sp.rho / std::max(l0, 1e-12)), 1.0, 150.0));
    std::vector<std::array<int, 2>> edges;
    edges.reserve(soup.F.size() * 3);
    for (const auto& t : soup.F)
        for (int k = 0; k < 3; ++k) edges.push_back({t[k], t[(k + 1) % 3]});
    std::vector<double> acc(nV);
    std::vector<int> deg(nV);
    for (const auto& e : edges) { ++deg[e[0]]; ++deg[e[1]]; }
    for (int it = 0; it < iters; ++it) {
        std::fill(acc.begin(), acc.end(), 0.0);
        for (const auto& e : edges) { acc[e[0]] += kap[e[1]]; acc[e[1]] += kap[e[0]]; }
        for (int v = 0; v < nV; ++v) if (deg[v]) kap[v] = 0.5 * kap[v] + 0.5 * acc[v] / deg[v];
    }
    for (int v = 0; v < nV; ++v) {
        if (kap[v] <= 0) continue;
        s[v] = static_cast<float>(std::clamp(angle / kap[v] / sp.rho, sp.minScale, 1.0));
    }
    gradeSizeField(soup, s, sp);
    for (int v = 0; v < nV; ++v) {
        if (s[v] < 0.999f) ++stats.refined;
        stats.minSeen = std::min<double>(stats.minSeen, s[v]);
    }
    return s;
}

// Narrow gaps and creases (the groove under a lip, between fingers): the largest ball that fits OUTSIDE the
// surface at a vertex without crossing it. Faces there are limited to ratio * that radius.
inline std::vector<float> gapSizeField(const TriSoup& soup, const SizeFieldParams& sp, SizeFieldStats& stats) {
    const int nV = static_cast<int>(soup.P.size());
    std::vector<float> s(nV, 1.0f);
    if (nV == 0 || soup.F.empty() || !(sp.rho > 0)) return s;
    std::vector<Vec3> N(nV);
    AABB box;
    for (const auto& p : soup.P) box.expand(p);
    for (const auto& t : soup.F) {
        const Vec3 n = cross(soup.P[t[1]] - soup.P[t[0]], soup.P[t[2]] - soup.P[t[0]]);
        for (int k = 0; k < 3; ++k) N[t[k]] += n;
    }
    for (auto& n : N) n = normalized(n);
    const double rmax = 0.5 * length(box.hi - box.lo);
    SurfaceIndex idx;
    idx.build(soup.P, soup.F);
    std::vector<double> radius(nV, rmax);
    parallelFor(nV, sp.threads, [&](int b, int e, int) {
        for (int v = b; v < e; ++v) {
            const Vec3 p = soup.P[v], n = N[v];
            if (length2(n) < 0.5) continue;
            double r = rmax;
            for (int it = 0; it < 14; ++it) {
                const Vec3 q = p + n * r;
                const auto h = idx.closest(q);
                if (h.tri < 0) break;
                if (dot(idx.faceNormal(h.tri), n) > 0.5) break;   // our own neighbourhood
                const Vec3 d = h.p - p;
                const double d2 = length2(d);
                if (d2 < 1e-12 * rmax * rmax) break;
                const double nd = dot(n, d);
                if (nd <= 1e-9 * std::sqrt(d2)) break;
                const double rn = d2 / (2.0 * nd);
                if (rn >= r * 0.999) break;
                r = rn;
            }
            radius[v] = r;
        }
    });
    if (std::getenv("FORGE_DBG")) { double mn = 1e30; int below = 0; for (double r : radius) { mn = std::min(mn, r); if (sp.ratio * r < sp.rho) ++below; } std::fprintf(stderr, "gap: min radius %.3f rho %.3f below %d rmax %.1f\n", mn, sp.rho, below, rmax); }
    for (int v = 0; v < nV; ++v) {
        const double hmax = sp.ratio * radius[v];
        s[v] = static_cast<float>(std::clamp(hmax / sp.rho, sp.minScale, 1.0));
    }
    gradeSizeField(soup, s, sp);
    for (int v = 0; v < nV; ++v) {
        if (s[v] < 0.999f) ++stats.refined;
        stats.minSeen = std::min<double>(stats.minSeen, s[v]);
    }
    return s;
}

}  // namespace forge
