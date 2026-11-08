use super::{clamp, vec3::Vec3};

/// Closest point on triangle `abc` to `p` (Ericson, Real-Time Collision Detection).
/// Returns the point and the barycentric weights `(u, v)`: b -> u, c -> v, a -> 1-u-v.
pub fn closest_point_on_triangle(p: Vec3, a: Vec3, b: Vec3, c: Vec3) -> (Vec3, f64, f64) {
    let ab = b - a;
    let ac = c - a;
    let ap = p - a;
    let d1 = ab.dot(ap);
    let d2 = ac.dot(ap);
    if d1 <= 0.0 && d2 <= 0.0 { return (a, 0.0, 0.0); }
    let bp = p - b;
    let d3 = ab.dot(bp);
    let d4 = ac.dot(bp);
    if d3 >= 0.0 && d4 <= d3 { return (b, 1.0, 0.0); }
    let vc = d1 * d4 - d3 * d2;
    if vc <= 0.0 && d1 >= 0.0 && d3 <= 0.0 {
        let t = d1 / (d1 - d3);
        return (a + ab * t, t, 0.0);
    }
    let cp = p - c;
    let d5 = ab.dot(cp);
    let d6 = ac.dot(cp);
    if d6 >= 0.0 && d5 <= d6 { return (c, 0.0, 1.0); }
    let vb = d5 * d2 - d1 * d6;
    if vb <= 0.0 && d2 >= 0.0 && d6 <= 0.0 {
        let t = d2 / (d2 - d6);
        return (a + ac * t, 0.0, t);
    }
    let va = d3 * d6 - d5 * d4;
    if va <= 0.0 && (d4 - d3) >= 0.0 && (d5 - d6) >= 0.0 {
        let t = (d4 - d3) / ((d4 - d3) + (d5 - d6));
        return (b + (c - b) * t, 1.0 - t, t);
    }
    let denom = 1.0 / (va + vb + vc);
    let u = vb * denom;
    let v = vc * denom;
    (a + ab * u + ac * v, u, v)
}

/// Closest point on segment `ab` to `p`; `t` in [0, 1] along the segment.
pub fn closest_point_on_segment(p: Vec3, a: Vec3, b: Vec3) -> (Vec3, f64) {
    let ab = b - a;
    let l2 = ab.dot(ab);
    let t = if l2 > 0.0 { clamp((p - a).dot(ab) / l2, 0.0, 1.0) } else { 0.0 };
    (a + ab * t, t)
}

pub fn triangle_normal_unnormalized(a: Vec3, b: Vec3, c: Vec3) -> Vec3 { (b - a).cross(c - a) }
