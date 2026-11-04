// Forge engine - quality metrics of a polygon mesh (reported as JSON for tests and the add-on).
#pragma once
#include <algorithm>
#include <cstdio>
#include <string>
#include <vector>

#include "bvh.hpp"
#include "meshio.hpp"
#include "repair.hpp"

namespace forge {

inline std::string metricsJson(const PolyMesh& m, const SurfaceIndex* ref) {
    const int nV = static_cast<int>(m.P.size());
    int tris = 0, quads = 0, other = 0;
    std::vector<uint64_t> edges;
    std::vector<double> lengths;
    double angMin = 180, angSum = 0;
    long angCount = 0;
    long badAngles = 0;  // tris below 20 deg or above 130 deg; quads below 45 or above 135
    size_t at = 0;
    for (int s : m.sizes) {
        const int* v = &m.idx[at];
        at += static_cast<size_t>(s);
        if (s == 3) ++tris; else if (s == 4) ++quads; else ++other;
        for (int k = 0; k < s; ++k) {
            const int a = v[k], b = v[(k + 1) % s], c = v[(k + s - 1) % s];
            edges.push_back(detail::edgeKey(a, b));
            const Vec3 e1 = normalized(m.P[b] - m.P[a]), e2 = normalized(m.P[c] - m.P[a]);
            const double ang = std::acos(clampd(dot(e1, e2), -1.0, 1.0)) * 180.0 / kPi;
            angMin = std::min(angMin, ang);
            angSum += ang;
            ++angCount;
            if (s == 3 && (ang < 20 || ang > 130)) ++badAngles;
            if (s == 4 && (ang < 45 || ang > 135)) ++badAngles;
        }
    }
    std::sort(edges.begin(), edges.end());
    int boundary = 0, nonManifold = 0, uniqueEdges = 0;
    std::vector<int> valence(nV, 0);
    double lenMin = kInf, lenMax = 0, lenSum = 0;
    for (size_t i = 0; i < edges.size();) {
        size_t j = i;
        while (j < edges.size() && edges[j] == edges[i]) ++j;
        const size_t n = j - i;
        ++uniqueEdges;
        if (n == 1) ++boundary; else if (n > 2) ++nonManifold;
        const int a = static_cast<int>(edges[i] >> 32), b = static_cast<int>(edges[i] & 0xffffffffu);
        ++valence[a]; ++valence[b];
        const double l = distance(m.P[a], m.P[b]);
        lenMin = std::min(lenMin, l); lenMax = std::max(lenMax, l); lenSum += l;
        i = j;
    }
    int hist[10] = {0};
    for (int v = 0; v < nV; ++v) ++hist[std::min(valence[v], 9)];
    const int regular = tris > 0 && quads == 0 ? hist[6] : hist[4];
    double dMean = 0, dMax = 0;
    if (ref && !ref->empty()) {
        double sum = 0;
        for (int v = 0; v < nV; ++v) {
            const double d = std::sqrt(ref->closest(m.P[v]).d2);
            sum += d; dMax = std::max(dMax, d);
        }
        dMean = nV ? sum / nV : 0;
    }
    char buf[1400];
    std::snprintf(buf, sizeof buf,
        "{\"verts\":%d,\"faces\":%zu,\"tris\":%d,\"quads\":%d,\"other\":%d,"
        "\"edges\":%d,\"boundary_edges\":%d,\"nonmanifold_edges\":%d,"
        "\"edge_len_min\":%.6g,\"edge_len_mean\":%.6g,\"edge_len_max\":%.6g,"
        "\"angle_min\":%.3f,\"angle_mean\":%.3f,\"bad_angle_corners\":%ld,"
        "\"valence\":[%d,%d,%d,%d,%d,%d,%d,%d,%d,%d],\"regular_vertices\":%d,"
        "\"dist_mean\":%.6g,\"dist_max\":%.6g}",
        nV, m.sizes.size(), tris, quads, other, uniqueEdges, boundary, nonManifold,
        uniqueEdges ? lenMin : 0.0, uniqueEdges ? lenSum / uniqueEdges : 0.0, lenMax,
        angMin, angCount ? angSum / angCount : 0.0, badAngles,
        hist[0], hist[1], hist[2], hist[3], hist[4], hist[5], hist[6], hist[7], hist[8], hist[9], regular,
        dMean, dMax);
    return buf;
}

}  // namespace forge
