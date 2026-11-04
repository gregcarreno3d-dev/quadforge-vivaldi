// Forge engine - smooth 4-direction (cross) field on a triangle mesh.
//
// Every face carries z = exp(i*4*theta), theta being the angle of one of its four
// axes in the face frame. Neighbouring faces are compared after transporting z across
// the shared edge, which makes the four-fold ambiguity disappear (no matching needed).
//   energy  = sum over edges |z_g - r_fg z_f|^2
// Feature edges and open borders fix z on the faces next to them. Components without
// any constraint get the smoothest field (smallest eigenvector, by inverse iteration
// with conjugate gradients); components with constraints get a plain linear solve.
#pragma once
#include <algorithm>
#include <array>
#include <complex>
#include <functional>
#include <queue>
#include <vector>

#include "bvh.hpp"
#include "cmesh.hpp"
#include "vec.hpp"

namespace forge {

using cplx = std::complex<double>;

struct FaceFrames {
    std::vector<Vec3> e1, e2, n;
    std::vector<std::array<int, 3>> nb;       // face across the edge opposite corner k (-1 border)
    std::vector<std::array<cplx, 3>> rot;     // r_{f->g}: transports z_f into g's frame
    std::vector<std::array<uint8_t, 3>> feat; // that edge is a feature
    std::vector<std::array<double, 3>> alpha; // angle of the edge in this face's frame
    std::vector<std::array<double, 3>> w;     // edge weight: |edge| / distance of the face centres (1 on a regular mesh)
};

inline FaceFrames buildFrames(const CMesh& m) {
    const int nF = m.numFaceSlots();
    FaceFrames fr;
    fr.e1.resize(nF); fr.e2.resize(nF); fr.n.resize(nF);
    fr.nb.assign(nF, {-1, -1, -1});
    fr.rot.assign(nF, {cplx(1, 0), cplx(1, 0), cplx(1, 0)});
    fr.feat.assign(nF, {0, 0, 0});
    fr.alpha.assign(nF, {0.0, 0.0, 0.0});
    fr.w.assign(nF, {1.0, 1.0, 1.0});
    for (int f = 0; f < nF; ++f) {
        const Vec3 &a = m.P[m.V[3 * f]], &b = m.P[m.V[3 * f + 1]], &c = m.P[m.V[3 * f + 2]];
        fr.e1[f] = normalized(b - a);
        fr.n[f] = normalized(cross(b - a, c - a));
        fr.e2[f] = cross(fr.n[f], fr.e1[f]);
        for (int k = 0; k < 3; ++k) {
            const int c0 = 3 * f + k;
            const Vec3 t = normalized(m.P[m.V[CMesh::prv(c0)]] - m.P[m.V[CMesh::nxt(c0)]]);
            fr.alpha[f][k] = std::atan2(dot(t, fr.e2[f]), dot(t, fr.e1[f]));
            fr.feat[f][k] = m.E[c0];
            const int d = m.O[c0];
            fr.nb[f][k] = d < 0 ? -1 : CMesh::faceOf(d);
        }
    }
    auto centre = [&](int f) { return (m.P[m.V[3 * f]] + m.P[m.V[3 * f + 1]] + m.P[m.V[3 * f + 2]]) / 3.0; };
    for (int f = 0; f < nF; ++f)
        for (int k = 0; k < 3; ++k) {
            const int d = m.O[3 * f + k];
            if (d < 0) continue;
            const int g = CMesh::faceOf(d);
            {
                const int c0 = 3 * f + k;
                const double el = distance(m.P[m.V[CMesh::prv(c0)]], m.P[m.V[CMesh::nxt(c0)]]);
                const double cd = distance(centre(f), centre(g));
                fr.w[f][k] = (cd > 0 && !std::getenv("FORGE_NOFW")) ? std::clamp(el / (cd * 1.7320508), 0.2, 5.0) : 1.0;
            }
            const double ag = fr.alpha[g][d % 3];
            fr.rot[f][k] = std::polar(1.0, 4.0 * (ag - fr.alpha[f][k]));
        }
    return fr;
}

inline Vec3 axisFromZ(const FaceFrames& fr, int f, cplx z) {
    const double theta = std::arg(z) / 4.0;
    return fr.e1[f] * std::cos(theta) + fr.e2[f] * std::sin(theta);
}

// Soft alignment of the cross field to creases and grooves (eyelids, lips, nostril and ear rims).
// The bending of the surface across every edge (dihedral angle) is summed into a tensor per face; its dominant
// axis is the direction along which the surface does NOT bend, i.e. the line of the crease. Where that is clear
// (strongly one-directional bending), the field is pulled towards it, so the quad rows follow the line and close
// into loops around the eye or the mouth. Flat or round (dome-like) places get no pull.
struct SoftField {
    std::vector<double> w;     // pull strength per face (0 = free)
    std::vector<cplx> target;  // wanted z per face
    int pulled = 0;
};

inline SoftField curvatureAlignment(const CMesh& m, const FaceFrames& fr, double strength, double gateAngle, int smoothPasses = 2) {
    const int nF = m.numFaceSlots();
    SoftField sf;
    sf.w.assign(nF, 0.0);
    sf.target.assign(nF, cplx(1, 0));
    if (strength <= 0 || gateAngle <= 0) return sf;
    struct T6 { double xx = 0, xy = 0, xz = 0, yy = 0, yz = 0, zz = 0, L = 0; };
    std::vector<T6> T(nF);
    for (int f = 0; f < nF; ++f) {
        if (!m.falive[f]) continue;
        for (int k = 0; k < 3; ++k) {
            const int g = fr.nb[f][k];
            if (g < 0 || g < f) continue;
            const int c0 = 3 * f + k;
            const Vec3 ev = m.P[m.V[CMesh::prv(c0)]] - m.P[m.V[CMesh::nxt(c0)]];
            const double len = length(ev);
            if (len <= 0) continue;
            const Vec3 e = ev / len;
            const double beta = std::atan2(length(cross(fr.n[f], fr.n[g])), dot(fr.n[f], fr.n[g]));
            const double wgt = beta * len;
            for (int idx : {f, g}) {
                T6& t = T[idx];
                t.xx += wgt * e.x * e.x; t.xy += wgt * e.x * e.y; t.xz += wgt * e.x * e.z;
                t.yy += wgt * e.y * e.y; t.yz += wgt * e.y * e.z; t.zz += wgt * e.z * e.z;
                t.L += len;
            }
        }
    }
    for (int pass = 0; pass < smoothPasses; ++pass) {
        std::vector<T6> U(nF);
        for (int f = 0; f < nF; ++f) {
            if (!m.falive[f]) continue;
            T6 a = T[f];
            for (int k = 0; k < 3; ++k) {
                const int g = fr.nb[f][k];
                if (g < 0) continue;
                const T6& b = T[g];
                a.xx += b.xx; a.xy += b.xy; a.xz += b.xz; a.yy += b.yy; a.yz += b.yz; a.zz += b.zz; a.L += b.L;
            }
            U[f] = a;
        }
        T.swap(U);
    }
    auto smooth01 = [](double x, double a, double b) { const double t = std::clamp((x - a) / (b - a), 0.0, 1.0); return t * t * (3 - 2 * t); };
    for (int f = 0; f < nF; ++f) {
        if (!m.falive[f] || T[f].L <= 0) continue;
        const T6& t = T[f];
        const Vec3 a = fr.e1[f], b = fr.e2[f];
        auto mul = [&](const Vec3& v) { return Vec3{t.xx * v.x + t.xy * v.y + t.xz * v.z, t.xy * v.x + t.yy * v.y + t.yz * v.z, t.xz * v.x + t.yz * v.y + t.zz * v.z}; };
        const double Aaa = dot(a, mul(a)), Abb = dot(b, mul(b)), Aab = dot(a, mul(b));
        const double tr = Aaa + Abb;
        if (tr <= 1e-12) continue;
        const double diff = std::sqrt((Aaa - Abb) * (Aaa - Abb) * 0.25 + Aab * Aab);   // (mu1 - mu2) / 2
        const double aniso = 2 * diff / tr;                    // 0 round, 1 pure crease
        const double mag = tr / t.L;                           // mean bending angle per unit edge length, scaled below
        const double phi = 0.5 * std::atan2(2 * Aab, Aaa - Abb);   // dominant axis (the line of the crease) in the face frame
        const double wgt = smooth01(aniso, 0.45, 0.85) * smooth01(mag, 0.35 * gateAngle, 0.9 * gateAngle);
        if (wgt <= 0.01) continue;
        sf.w[f] = strength * wgt;
        sf.target[f] = std::polar(1.0, 4.0 * phi);
        ++sf.pulled;
    }
    // Rings: around a crease the rows should run along the lines of equal distance to it, so they close into loops
    // around an eye or a mouth (and turn the corner at the ends of a slit). Distance from the crease faces over the
    // mesh edges; the wanted direction is perpendicular to the distance gradient; the pull fades out with distance.
    const double ringK = std::getenv("FORGE_RING") ? std::atof(std::getenv("FORGE_RING")) : 0.0;
    const double ringR = std::getenv("FORGE_RINGR") ? std::atof(std::getenv("FORGE_RINGR")) : 3.0;
    if (ringK > 0 && sf.pulled > 0) {
        const int nV = m.numVertSlots();
        std::vector<std::vector<std::pair<int, double>>> adj(nV);
        double edgeSum = 0; long edgeCnt = 0;
        for (int f = 0; f < nF; ++f) {
            if (!m.falive[f]) continue;
            for (int k = 0; k < 3; ++k) {
                const int a = m.V[3 * f + k], b = m.V[3 * f + (k + 1) % 3];
                const double len = distance(m.P[a], m.P[b]);
                adj[a].push_back({b, len}); adj[b].push_back({a, len});
                edgeSum += len; ++edgeCnt;
            }
        }
        const double meanEdge = edgeCnt ? edgeSum / edgeCnt : 0.0;
        const double R = ringR * meanEdge;
        std::vector<double> dist(nV, 1e300);
        using QE = std::pair<double, int>;
        std::priority_queue<QE, std::vector<QE>, std::greater<QE>> pq;
        for (int f = 0; f < nF; ++f)
            if (m.falive[f] && sf.w[f] >= 0.5 * strength)
                for (int k = 0; k < 3; ++k) { const int v = m.V[3 * f + k]; if (dist[v] > 0) { dist[v] = 0; pq.push({0.0, v}); } }
        while (!pq.empty()) {
            const auto [d, v] = pq.top(); pq.pop();
            if (d > dist[v] || d > R) continue;
            for (const auto& [u, len] : adj[v])
                if (d + len < dist[u]) { dist[u] = d + len; pq.push({dist[u], u}); }
        }
        std::vector<double> ringW(nF, 0.0);
        std::vector<cplx> ringT(nF, cplx(1, 0));
        for (int f = 0; f < nF; ++f) {
            if (!m.falive[f] || sf.w[f] > 0) continue;
            const int v0 = m.V[3 * f], v1 = m.V[3 * f + 1], v2 = m.V[3 * f + 2];
            const double d0 = dist[v0], d1 = dist[v1], d2 = dist[v2];
            if (d0 > R || d1 > R || d2 > R) continue;
            const Vec3 &p0 = m.P[v0], &p1 = m.P[v1], &p2 = m.P[v2];
            const Vec3 n = fr.n[f];
            const double A2 = length(cross(p1 - p0, p2 - p0));
            if (A2 <= 1e-18) continue;
            const Vec3 g = (cross(n, p2 - p1) * d0 + cross(n, p0 - p2) * d1 + cross(n, p1 - p0) * d2) / A2;
            const double gl = length(g);
            if (gl < 1e-9) continue;
            const Vec3 t = cross(n, g / gl);
            const double phi = std::atan2(dot(t, fr.e2[f]), dot(t, fr.e1[f]));
            const double dm = (d0 + d1 + d2) / 3.0;
            const double fade = std::max(0.0, 1.0 - dm / R);
            ringW[f] = strength * ringK * fade * fade;
            ringT[f] = std::polar(1.0, 4.0 * phi);
        }
        for (int f = 0; f < nF; ++f) if (ringW[f] > 0.01) { sf.w[f] = ringW[f]; sf.target[f] = ringT[f]; ++sf.pulled; }
    }
    return sf;
}

// Flow guides: the user draws lines on the model; the rows of quads should run along them. Every face near a guide
// segment gets a pull towards the direction of that segment, fading out with the distance. It is a soft hint
// (the smoothness of the field still wins where guides contradict the surface), not a hard constraint.
inline int addFlowGuides(const CMesh& m, const FaceFrames& fr, const SegmentIndex& guides, double strength,
                         double radiusEdges, SoftField& sf) {
    if (guides.empty() || strength <= 0) return 0;
    const int nF = m.numFaceSlots();
    double edgeSum = 0; long edgeCnt = 0;
    for (int f = 0; f < nF; ++f) {
        if (!m.falive[f]) continue;
        for (int k = 0; k < 3; ++k) {
            edgeSum += distance(m.P[m.V[3 * f + k]], m.P[m.V[3 * f + (k + 1) % 3]]);
            ++edgeCnt;
        }
    }
    const double R = radiusEdges * (edgeCnt ? edgeSum / edgeCnt : 0.0);
    if (R <= 0) return 0;
    int hit = 0;
    for (int f = 0; f < nF; ++f) {
        if (!m.falive[f]) continue;
        const Vec3 c = (m.P[m.V[3 * f]] + m.P[m.V[3 * f + 1]] + m.P[m.V[3 * f + 2]]) / 3.0;
        const auto h = guides.closest(c);
        if (h.seg < 0) continue;
        const double d = std::sqrt(h.d2);
        if (d >= R) continue;
        const auto& sg = guides.segment(h.seg);
        Vec3 dir = sg[1] - sg[0];
        if (length(dir) < 1e-12) continue;
        dir = normalized(dir);
        const Vec3 t = dir - fr.n[f] * dot(dir, fr.n[f]);
        if (length(t) < 0.3) continue;      // the guide stands on end to the surface here
        const double phi = std::atan2(dot(t, fr.e2[f]), dot(t, fr.e1[f]));
        const double fade = 1.0 - d / R;
        const double w = strength * fade * fade;
        if (w > sf.w[f]) {
            if (sf.w[f] <= 0) ++sf.pulled;
            sf.w[f] = w;
            sf.target[f] = std::polar(1.0, 4.0 * phi);
            ++hit;
        }
    }
    return hit;
}

namespace detail {

// y = (L restricted to free faces) x, where fixed faces are treated as zero (their
// contribution is moved to the right-hand side by the caller).
struct FieldSystem {
    const FaceFrames& fr;
    const std::vector<uint8_t>& fixed;
    const std::vector<double>* soft = nullptr;      // extra pull towards a target per face
    explicit FieldSystem(const FaceFrames& f, const std::vector<uint8_t>& fx) : fr(f), fixed(fx) {}
    double deg(int f) const { double d = soft ? (*soft)[f] : 0.0; for (int k = 0; k < 3; ++k) if (fr.nb[f][k] >= 0) d += fr.w[f][k]; return d; }
    void apply(const std::vector<cplx>& x, std::vector<cplx>& y, double eps, const std::vector<int>& free) const {
        for (int f : free) {
            cplx s = (deg(f) + eps) * x[f];
            for (int k = 0; k < 3; ++k) {
                const int g = fr.nb[f][k];
                if (g < 0 || fixed[g]) continue;
                s -= fr.w[f][k] * std::conj(fr.rot[f][k]) * x[g];  // r_{g->f} = conj(r_{f->g})
            }
            y[f] = s;
        }
    }
};

inline int conjugateGradient(const FieldSystem& A, const std::vector<int>& free, const std::vector<cplx>& b,
                             std::vector<cplx>& x, double eps, int maxIter, double tol) {
    const size_t n = x.size();
    std::vector<cplx> r(n), p(n), Ap(n), zv(n);
    A.apply(x, Ap, eps, free);
    double bnorm = 0, rz = 0;
    for (int f : free) { r[f] = b[f] - Ap[f]; bnorm += std::norm(b[f]); }
    if (bnorm < 1e-300) return 0;
    auto precond = [&](int f) { return 1.0 / (A.deg(f) + eps); };
    for (int f : free) { zv[f] = r[f] * precond(f); p[f] = zv[f]; rz += std::real(std::conj(r[f]) * zv[f]); }
    int it = 0;
    for (; it < maxIter; ++it) {
        A.apply(p, Ap, eps, free);
        double pAp = 0;
        for (int f : free) pAp += std::real(std::conj(p[f]) * Ap[f]);
        if (std::abs(pAp) < 1e-300) break;
        const double alpha = rz / pAp;
        double rnorm = 0;
        for (int f : free) { x[f] += alpha * p[f]; r[f] -= alpha * Ap[f]; rnorm += std::norm(r[f]); }
        if (rnorm <= tol * tol * bnorm) { ++it; break; }
        double rzNew = 0;
        for (int f : free) { zv[f] = r[f] * precond(f); rzNew += std::real(std::conj(r[f]) * zv[f]); }
        const double beta = rzNew / rz;
        rz = rzNew;
        for (int f : free) p[f] = zv[f] + beta * p[f];
    }
    return it;
}

}  // namespace detail

// Returns one unit complex number per live face (garbage for dead slots).
inline std::vector<cplx> solveCrossField(const CMesh& m, const FaceFrames& fr, bool useFeatures, double curvStrength = 0, double curvGate = 0,
                                         const SegmentIndex* guides = nullptr, double guideStrength = 0, double guideRadius = 4.0) {
    const int nF = m.numFaceSlots();
    std::vector<cplx> z(nF, cplx(1, 0));
    std::vector<uint8_t> fixed(nF, 0);
    // constraints from feature edges and borders
    if (useFeatures) {
        for (int f = 0; f < nF; ++f) {
            if (!m.falive[f]) continue;
            bool have = false;
            cplx target;
            bool conflict = false;
            for (int k = 0; k < 3; ++k) {
                if (!fr.feat[f][k]) continue;
                const cplx t = std::polar(1.0, 4.0 * fr.alpha[f][k]);
                if (!have) { target = t; have = true; }
                else if (std::real(std::conj(target) * t) < 0.5) conflict = true;  // corner: edges not orthogonal
            }
            if (have && !conflict) { fixed[f] = 1; z[f] = target; }
        }
    }
    // connected components over face adjacency
    std::vector<int> comp(nF, -1);
    int nc = 0;
    std::vector<int> stack;
    for (int s = 0; s < nF; ++s) {
        if (!m.falive[s] || comp[s] >= 0) continue;
        comp[s] = nc;
        stack.assign(1, s);
        while (!stack.empty()) {
            const int f = stack.back(); stack.pop_back();
            for (int k = 0; k < 3; ++k) {
                const int g = fr.nb[f][k];
                if (g >= 0 && comp[g] < 0) { comp[g] = nc; stack.push_back(g); }
            }
        }
        ++nc;
    }
    std::vector<std::vector<int>> members(nc);
    std::vector<uint8_t> hasFixed(nc, 0);
    for (int f = 0; f < nF; ++f) if (m.falive[f]) { members[comp[f]].push_back(f); if (fixed[f]) hasFixed[comp[f]] = 1; }

    SoftField soft;
    soft.w.assign(nF, 0.0);
    soft.target.assign(nF, cplx(1, 0));
    if (curvStrength > 0 && curvGate > 0) soft = curvatureAlignment(m, fr, curvStrength, curvGate);
    if (guides && guideStrength > 0) addFlowGuides(m, fr, *guides, guideStrength, guideRadius, soft);
    for (int f = 0; f < nF; ++f) if (m.falive[f] && fixed[f]) soft.w[f] = 0;
    std::vector<uint8_t> hasSoft(nc, 0);
    for (int f = 0; f < nF; ++f) if (m.falive[f] && f < (int)soft.w.size() && soft.w[f] > 0) hasSoft[comp[f]] = 1;
    detail::FieldSystem sys(fr, fixed);
    if (soft.pulled > 0) sys.soft = &soft.w;
    std::vector<cplx> b(nF);
    for (int c = 0; c < nc; ++c) {
        std::vector<int> freeF;
        for (int f : members[c]) if (!fixed[f]) freeF.push_back(f);
        if (freeF.empty()) continue;
        if (hasFixed[c] || hasSoft[c]) {
            // L_FF x = - L_FC z_C   (plus a tiny regulariser; plus the soft pull towards the crease directions)
            for (int f : freeF) {
                cplx s = 0;
                if (f < (int)soft.w.size() && soft.w[f] > 0) s += soft.w[f] * soft.target[f];
                for (int k = 0; k < 3; ++k) {
                    const int g = fr.nb[f][k];
                    if (g >= 0 && fixed[g]) s += fr.w[f][k] * std::conj(fr.rot[f][k]) * z[g];
                }
                b[f] = s;
            }
            // initial guess: average of the fixed neighbours' influence (propagated by CG)
            for (int f : freeF) z[f] = b[f] == cplx(0, 0) ? cplx(1, 0) : b[f] / std::abs(b[f]);
            detail::conjugateGradient(sys, freeF, b, z, 1e-9, 2000, 1e-8);
        } else {
            // smoothest field: inverse iteration on (L + eps I)
            const double eps = 1e-3;
            uint64_t seed = 12345 + static_cast<uint64_t>(c);
            for (int f : freeF) {
                seed = seed * 6364136223846793005ULL + 1442695040888963407ULL;
                z[f] = std::polar(1.0, static_cast<double>(seed >> 40) / static_cast<double>(1 << 24) * 2 * kPi);
            }
            std::vector<cplx> rhs(nF);
            for (int outer = 0; outer < (std::getenv("FORGE_FOUT") ? std::atoi(std::getenv("FORGE_FOUT")) : 12); ++outer) {
                for (int f : freeF) rhs[f] = z[f];
                detail::conjugateGradient(sys, freeF, rhs, z, eps, std::getenv("FORGE_FCG") ? std::atoi(std::getenv("FORGE_FCG")) : 400, 1e-6);
                double nrm = 0;
                for (int f : freeF) nrm += std::norm(z[f]);
                nrm = std::sqrt(nrm / freeF.size());
                if (nrm < 1e-300) break;
                for (int f : freeF) z[f] /= nrm;
            }
        }
        for (int f : freeF) { const double a = std::abs(z[f]); z[f] = a > 1e-12 ? z[f] / a : cplx(1, 0); }
    }
    return z;
}

}  // namespace forge
