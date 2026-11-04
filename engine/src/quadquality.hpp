// Forge engine - local quality optimisation of a quad mesh.
//
// Every vertex that touches a poorly shaped quad is moved (along the surface) to the position that
// maximises the worst scaled Jacobian of its quads. Candidates are the neighbour average and small
// steps around it; each is projected back onto the reference surface. Vertices on marked edges stay.
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <vector>

#include "bvh.hpp"
#include "vec.hpp"

namespace forge {

struct QuadQualityStats {
    int sweeps = 0, moved = 0;
    double worstBefore = 1, worstAfter = 1;
    int badBefore = 0, badAfter = 0;      // quads with scaled Jacobian below 0.5
};

namespace qq {

inline double quadQuality(const Vec3& p0, const Vec3& p1, const Vec3& p2, const Vec3& p3) {
    const Vec3 n = normalized(cross(p2 - p0, p3 - p1));
    const Vec3* p[4] = {&p0, &p1, &p2, &p3};
    double q = 1.0;
    for (int k = 0; k < 4; ++k) {
        const Vec3 a = *p[(k + 1) & 3] - *p[k], b = *p[(k + 3) & 3] - *p[k];
        const double la = length(a), lb = length(b);
        if (la < 1e-300 || lb < 1e-300) return -1.0;
        q = std::min(q, dot(cross(a, b), n) / (la * lb));
    }
    return q;
}

}  // namespace qq

// P: positions, Q: quads (counter-clockwise), frozen[v] != 0 keeps a vertex in place.
inline void improveQuads(std::vector<Vec3>& P, const std::vector<std::array<int, 4>>& Q, const std::vector<uint8_t>& frozen,
                         const SurfaceIndex& ref, int sweeps, double threshold, QuadQualityStats& st) {
    const int nV = static_cast<int>(P.size());
    std::vector<int> start(nV + 1, 0), inc;
    for (const auto& q : Q) for (int k = 0; k < 4; ++k) ++start[q[k] + 1];
    for (int v = 0; v < nV; ++v) start[v + 1] += start[v];
    inc.resize(start[nV]);
    {
        std::vector<int> fill(start.begin(), start.end() - 1);
        for (size_t i = 0; i < Q.size(); ++i) for (int k = 0; k < 4; ++k) inc[fill[Q[i][k]]++] = static_cast<int>(i);
    }
    auto quality = [&](int qi) { const auto& q = Q[qi]; return qq::quadQuality(P[q[0]], P[q[1]], P[q[2]], P[q[3]]); };
    std::vector<double> qual(Q.size());
    auto refresh = [&]() { for (size_t i = 0; i < Q.size(); ++i) qual[i] = quality(static_cast<int>(i)); };
    refresh();
    st.worstBefore = 1; st.badBefore = 0;
    for (double x : qual) { st.worstBefore = std::min(st.worstBefore, x); if (x < threshold) ++st.badBefore; }
    // neighbours of every vertex through the quads
    auto neighbours = [&](int v, std::vector<int>& out) {
        out.clear();
        for (int e = start[v]; e < start[v + 1]; ++e) {
            const auto& q = Q[inc[e]];
            int k = 0;
            while (q[k] != v) ++k;
            for (int o : {q[(k + 1) & 3], q[(k + 3) & 3]})
                if (std::find(out.begin(), out.end(), o) == out.end()) out.push_back(o);
        }
    };
    std::vector<int> nb;
    for (int sweep = 0; sweep < sweeps; ++sweep) {
        int movedNow = 0;
        for (int v = 0; v < nV; ++v) {
            if (frozen[v] || start[v] == start[v + 1]) continue;
            double cur = 2.0;
            for (int e = start[v]; e < start[v + 1]; ++e) cur = std::min(cur, qual[inc[e]]);
            if (cur >= threshold) continue;
            neighbours(v, nb);
            Vec3 avg;
            double meanLen = 0;
            for (int w : nb) { avg += P[w]; meanLen += distance(P[v], P[w]); }
            if (nb.empty()) continue;
            avg = avg / static_cast<double>(nb.size());
            meanLen /= nb.size();
            const auto h0 = ref.closest(P[v]);
            Vec3 n = h0.tri >= 0 ? ref.faceNormal(h0.tri) : Vec3(0, 0, 1);
            Vec3 t1 = cross(n, std::fabs(n.x) < 0.9 ? Vec3(1, 0, 0) : Vec3(0, 1, 0));
            t1 = normalized(t1);
            const Vec3 t2 = cross(n, t1);
            const Vec3 old = P[v];
            double best = cur;
            Vec3 bestPos = old;
            auto tryPos = [&](Vec3 c) {
                const auto h = ref.closest(c);
                if (h.tri >= 0) c = h.p;
                {   // never squeeze an edge: keep every edge of the vertex at least a third of the usual length
                    double me = 1e300;
                    for (int w : nb) me = std::min(me, distance(c, P[w]));
                    if (me < 0.33 * meanLen) return;
                }
                P[v] = c;
                double m = 2.0;
                for (int e = start[v]; e < start[v + 1]; ++e) m = std::min(m, quality(inc[e]));
                if (m > best + 1e-4) { best = m; bestPos = c; }
            };
            tryPos(avg);
            const double steps[3] = {0.5, 0.25, 0.1};
            for (double s : steps)
                for (int d = 0; d < 8; ++d) {
                    const double a = d * 0.78539816339;
                    tryPos(avg + (t1 * std::cos(a) + t2 * std::sin(a)) * (s * meanLen));
                }
            P[v] = bestPos;
            if (best > cur + 1e-4) {
                ++movedNow;
                for (int e = start[v]; e < start[v + 1]; ++e) qual[inc[e]] = quality(inc[e]);
            }
        }
        st.sweeps = sweep + 1;
        st.moved += movedNow;
        if (movedNow == 0) break;
    }
    refresh();
    st.worstAfter = 1; st.badAfter = 0;
    for (double x : qual) { st.worstAfter = std::min(st.worstAfter, x); if (x < threshold) ++st.badAfter; }
}

}  // namespace forge
