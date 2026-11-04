// Forge engine - small vector math. Header only, no dependencies.
#pragma once
#include <algorithm>
#include <cmath>
#include <limits>

namespace forge {

constexpr double kInf = std::numeric_limits<double>::infinity();
constexpr double kPi = 3.14159265358979323846;

struct Vec3 {
    double x = 0, y = 0, z = 0;
    constexpr Vec3() = default;
    constexpr Vec3(double x_, double y_, double z_) : x(x_), y(y_), z(z_) {}
    constexpr Vec3 operator+(const Vec3& o) const { return {x + o.x, y + o.y, z + o.z}; }
    constexpr Vec3 operator-(const Vec3& o) const { return {x - o.x, y - o.y, z - o.z}; }
    constexpr Vec3 operator*(double s) const { return {x * s, y * s, z * s}; }
    constexpr Vec3 operator/(double s) const { return {x / s, y / s, z / s}; }
    constexpr Vec3 operator-() const { return {-x, -y, -z}; }
    Vec3& operator+=(const Vec3& o) { x += o.x; y += o.y; z += o.z; return *this; }
    Vec3& operator-=(const Vec3& o) { x -= o.x; y -= o.y; z -= o.z; return *this; }
    Vec3& operator*=(double s) { x *= s; y *= s; z *= s; return *this; }
    double& operator[](int i) { return i == 0 ? x : (i == 1 ? y : z); }
    double operator[](int i) const { return i == 0 ? x : (i == 1 ? y : z); }
};

inline double dot(const Vec3& a, const Vec3& b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(const Vec3& a, const Vec3& b) {
    return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
}
inline double length2(const Vec3& a) { return dot(a, a); }
inline double length(const Vec3& a) { return std::sqrt(dot(a, a)); }
inline double distance(const Vec3& a, const Vec3& b) { return length(a - b); }
inline double distance2(const Vec3& a, const Vec3& b) { return length2(a - b); }
inline Vec3 normalized(const Vec3& a) {
    double l = length(a);
    return l > 1e-300 ? a / l : Vec3(0, 0, 0);
}
inline Vec3 lerp(const Vec3& a, const Vec3& b, double t) { return a + (b - a) * t; }
inline double clampd(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }

struct AABB {
    Vec3 lo{kInf, kInf, kInf};
    Vec3 hi{-kInf, -kInf, -kInf};
    void expand(const Vec3& p) {
        lo.x = std::min(lo.x, p.x); lo.y = std::min(lo.y, p.y); lo.z = std::min(lo.z, p.z);
        hi.x = std::max(hi.x, p.x); hi.y = std::max(hi.y, p.y); hi.z = std::max(hi.z, p.z);
    }
    void expand(const AABB& b) { expand(b.lo); expand(b.hi); }
    Vec3 center() const { return (lo + hi) * 0.5; }
    Vec3 extent() const { return hi - lo; }
    double distance2To(const Vec3& p) const {
        double dx = std::max({lo.x - p.x, 0.0, p.x - hi.x});
        double dy = std::max({lo.y - p.y, 0.0, p.y - hi.y});
        double dz = std::max({lo.z - p.z, 0.0, p.z - hi.z});
        return dx * dx + dy * dy + dz * dz;
    }
};

// Closest point on triangle abc to p (Ericson, Real-Time Collision Detection).
// Barycentric weights of the result: b -> u, c -> v, a -> 1-u-v.
inline Vec3 closestPointOnTriangle(const Vec3& p, const Vec3& a, const Vec3& b, const Vec3& c,
                                   double& u, double& v) {
    const Vec3 ab = b - a, ac = c - a, ap = p - a;
    const double d1 = dot(ab, ap), d2 = dot(ac, ap);
    if (d1 <= 0 && d2 <= 0) { u = 0; v = 0; return a; }
    const Vec3 bp = p - b;
    const double d3 = dot(ab, bp), d4 = dot(ac, bp);
    if (d3 >= 0 && d4 <= d3) { u = 1; v = 0; return b; }
    const double vc = d1 * d4 - d3 * d2;
    if (vc <= 0 && d1 >= 0 && d3 <= 0) {
        const double t = d1 / (d1 - d3);
        u = t; v = 0;
        return a + ab * t;
    }
    const Vec3 cp = p - c;
    const double d5 = dot(ab, cp), d6 = dot(ac, cp);
    if (d6 >= 0 && d5 <= d6) { u = 0; v = 1; return c; }
    const double vb = d5 * d2 - d1 * d6;
    if (vb <= 0 && d2 >= 0 && d6 <= 0) {
        const double t = d2 / (d2 - d6);
        u = 0; v = t;
        return a + ac * t;
    }
    const double va = d3 * d6 - d5 * d4;
    if (va <= 0 && (d4 - d3) >= 0 && (d5 - d6) >= 0) {
        const double t = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        u = 1 - t; v = t;
        return b + (c - b) * t;
    }
    const double denom = 1.0 / (va + vb + vc);
    u = vb * denom; v = vc * denom;
    return a + ab * u + ac * v;
}

// Closest point on segment ab to p; t in [0,1] along the segment.
inline Vec3 closestPointOnSegment(const Vec3& p, const Vec3& a, const Vec3& b, double& t) {
    const Vec3 ab = b - a;
    const double l2 = dot(ab, ab);
    t = l2 > 0 ? clampd(dot(p - a, ab) / l2, 0.0, 1.0) : 0.0;
    return a + ab * t;
}

inline Vec3 triangleNormalUnnormalized(const Vec3& a, const Vec3& b, const Vec3& c) {
    return cross(b - a, c - a);
}

}  // namespace forge
