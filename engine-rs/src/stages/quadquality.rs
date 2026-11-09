//! Local quality optimisation of a quad mesh.
//!
//! Every vertex that touches a poorly shaped quad is moved (along the surface) to the position that maximises
//! the worst scaled Jacobian of its quads. Candidates are the neighbour average and small steps around it; each
//! is projected back onto the reference surface. Frozen vertices (marked edges) stay.
use crate::math::Vec3;
use crate::mesh::bvh::SurfaceIndex;

#[derive(Clone, Debug)]
pub struct QuadQualityStats {
    pub sweeps: i32,
    pub moved: i32,
    pub worst_before: f64,
    pub worst_after: f64,
    /// Quads with scaled Jacobian below the threshold.
    pub bad_before: i32,
    pub bad_after: i32,
}

impl Default for QuadQualityStats {
    fn default() -> Self { QuadQualityStats { sweeps: 0, moved: 0, worst_before: 1.0, worst_after: 1.0, bad_before: 0, bad_after: 0 } }
}

pub fn quad_quality(p0: Vec3, p1: Vec3, p2: Vec3, p3: Vec3) -> f64 {
    let n = (p2 - p0).cross(p3 - p1).normalized();
    let p = [p0, p1, p2, p3];
    let mut q: f64 = 1.0;
    for k in 0..4 {
        let a = p[(k + 1) & 3] - p[k];
        let b = p[(k + 3) & 3] - p[k];
        let (la, lb) = (a.length(), b.length());
        if la < 1e-300 || lb < 1e-300 { return -1.0; }
        q = q.min(a.cross(b).dot(n) / (la * lb));
    }
    q
}

/// `p`: positions, `quads`: counter-clockwise, `frozen[v]` keeps a vertex in place.
pub fn improve_quads(p: &mut [Vec3], quads: &[[i32; 4]], frozen: &[bool], reference: &SurfaceIndex, sweeps: i32,
                     threshold: f64, st: &mut QuadQualityStats) {
    let n_v = p.len();
    let mut start = vec![0usize; n_v + 1];
    for q in quads { for k in 0..4 { start[q[k] as usize + 1] += 1; } }
    for v in 0..n_v { start[v + 1] += start[v]; }
    let mut inc = vec![0usize; start[n_v]];
    {
        let mut fill = start[..n_v].to_vec();
        for (i, q) in quads.iter().enumerate() { for k in 0..4 { inc[fill[q[k] as usize]] = i; fill[q[k] as usize] += 1; } }
    }
    let quality = |p: &[Vec3], qi: usize| {
        let q = quads[qi];
        quad_quality(p[q[0] as usize], p[q[1] as usize], p[q[2] as usize], p[q[3] as usize])
    };
    let mut qual: Vec<f64> = (0..quads.len()).map(|i| quality(p, i)).collect();
    st.worst_before = 1.0;
    st.bad_before = 0;
    for &x in &qual { st.worst_before = st.worst_before.min(x); if x < threshold { st.bad_before += 1; } }
    let neighbours = |v: usize, out: &mut Vec<i32>| {
        out.clear();
        for e in start[v]..start[v + 1] {
            let q = quads[inc[e]];
            let mut k = 0;
            while q[k] as usize != v { k += 1; }
            for o in [q[(k + 1) & 3], q[(k + 3) & 3]] {
                if !out.contains(&o) { out.push(o); }
            }
        }
    };
    let mut nb: Vec<i32> = Vec::new();
    for sweep in 0..sweeps {
        let mut moved_now = 0;
        for v in 0..n_v {
            if frozen[v] || start[v] == start[v + 1] { continue; }
            let mut cur = 2.0f64;
            for e in start[v]..start[v + 1] { cur = cur.min(qual[inc[e]]); }
            if cur >= threshold { continue; }
            neighbours(v, &mut nb);
            if nb.is_empty() { continue; }
            let mut avg = Vec3::ZERO;
            let mut mean_len = 0.0;
            for &w in &nb { avg += p[w as usize]; mean_len += p[v].distance(p[w as usize]); }
            avg = avg / nb.len() as f64;
            mean_len /= nb.len() as f64;
            let h0 = reference.closest_any(p[v]);
            let n = if h0.tri >= 0 { reference.face_normal(h0.tri) } else { Vec3::new(0.0, 0.0, 1.0) };
            let t1 = n.cross(if n.x.abs() < 0.9 { Vec3::new(1.0, 0.0, 0.0) } else { Vec3::new(0.0, 1.0, 0.0) }).normalized();
            let t2 = n.cross(t1);
            let old = p[v];
            let mut best = cur;
            let mut best_pos = old;
            let try_pos = |c: Vec3, p: &mut [Vec3], best: &mut f64, best_pos: &mut Vec3| {
                let h = reference.closest_any(c);
                let c = if h.tri >= 0 { h.p } else { c };
                // never squeeze an edge: keep every edge of the vertex at least a third of the usual length
                let me = nb.iter().map(|&w| c.distance(p[w as usize])).fold(1e300, f64::min);
                if me < 0.33 * mean_len { return; }
                p[v] = c;
                let mut m = 2.0f64;
                for e in start[v]..start[v + 1] { m = m.min(quality(p, inc[e])); }
                if m > *best + 1e-4 { *best = m; *best_pos = c; }
            };
            try_pos(avg, p, &mut best, &mut best_pos);
            for s in [0.5, 0.25, 0.1] {
                for d in 0..8 {
                    let a = d as f64 * 0.78539816339;
                    try_pos(avg + (t1 * a.cos() + t2 * a.sin()) * (s * mean_len), p, &mut best, &mut best_pos);
                }
            }
            p[v] = best_pos;
            if best > cur + 1e-4 {
                moved_now += 1;
                for e in start[v]..start[v + 1] { qual[inc[e]] = quality(p, inc[e]); }
            }
        }
        st.sweeps = sweep + 1;
        st.moved += moved_now;
        if moved_now == 0 { break; }
    }
    for i in 0..quads.len() { qual[i] = quality(p, i); }
    st.worst_after = 1.0;
    st.bad_after = 0;
    for &x in &qual { st.worst_after = st.worst_after.min(x); if x < threshold { st.bad_after += 1; } }
}
