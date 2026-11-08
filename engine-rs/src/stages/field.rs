//! Smooth 4-direction (cross) field on a triangle mesh.
//!
//! Every face carries z = exp(i*4*theta), theta being the angle of one of its four axes in the face frame.
//! Neighbouring faces are compared after transporting z across the shared edge, which removes the four-fold
//! ambiguity (no matching needed). energy = sum over edges |z_g - r_fg z_f|^2.
//! Feature edges and open borders fix z on the faces next to them. Components without any constraint get the
//! smoothest field (inverse iteration with conjugate gradients); constrained ones get a plain linear solve.
//!
//! Work areas: `build_frames` (per-face frames) · `curvature_alignment` (crease pull) · `add_flow_guides`
//! (user guides) · `solve_cross_field` (the solver).
use crate::math::{clamp, Cplx, Vec3, PI};
use crate::mesh::bvh::SegmentIndex;
use crate::mesh::cmesh::{face_of, nxt, prv, CMesh};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

fn env_f64(name: &str) -> Option<f64> { std::env::var(name).ok().and_then(|s| s.trim().parse().ok()) }

pub struct FaceFrames {
    pub e1: Vec<Vec3>,
    pub e2: Vec<Vec3>,
    pub n: Vec<Vec3>,
    /// Face across the edge opposite corner k (-1 border).
    pub nb: Vec<[i32; 3]>,
    /// r_{f->g}: transports z_f into g's frame.
    pub rot: Vec<[Cplx; 3]>,
    /// That edge is a feature.
    pub feat: Vec<[bool; 3]>,
    /// Angle of the edge in this face's frame.
    pub alpha: Vec<[f64; 3]>,
    /// Edge weight: |edge| / distance of the face centres (1 on a regular mesh).
    pub w: Vec<[f64; 3]>,
}

pub fn build_frames(m: &CMesh) -> FaceFrames {
    let n_f = m.num_face_slots() as usize;
    let mut fr = FaceFrames {
        e1: vec![Vec3::ZERO; n_f],
        e2: vec![Vec3::ZERO; n_f],
        n: vec![Vec3::ZERO; n_f],
        nb: vec![[-1; 3]; n_f],
        rot: vec![[Cplx::ONE; 3]; n_f],
        feat: vec![[false; 3]; n_f],
        alpha: vec![[0.0; 3]; n_f],
        w: vec![[1.0; 3]; n_f],
    };
    for f in 0..n_f {
        let a = m.p[m.v[3 * f] as usize];
        let b = m.p[m.v[3 * f + 1] as usize];
        let c = m.p[m.v[3 * f + 2] as usize];
        fr.e1[f] = (b - a).normalized();
        fr.n[f] = (b - a).cross(c - a).normalized();
        fr.e2[f] = fr.n[f].cross(fr.e1[f]);
        for k in 0..3 {
            let c0 = (3 * f + k) as i32;
            let t = (m.p[m.v[prv(c0) as usize] as usize] - m.p[m.v[nxt(c0) as usize] as usize]).normalized();
            fr.alpha[f][k] = t.dot(fr.e2[f]).atan2(t.dot(fr.e1[f]));
            fr.feat[f][k] = m.e[c0 as usize] != 0;
            let d = m.o[c0 as usize];
            fr.nb[f][k] = if d < 0 { -1 } else { face_of(d) };
        }
    }
    let centre = |f: usize| (m.p[m.v[3 * f] as usize] + m.p[m.v[3 * f + 1] as usize] + m.p[m.v[3 * f + 2] as usize]) / 3.0;
    let no_fw = std::env::var_os("FORGE_NOFW").is_some();
    for f in 0..n_f {
        for k in 0..3 {
            let d = m.o[3 * f + k];
            if d < 0 { continue; }
            let g = face_of(d) as usize;
            let c0 = (3 * f + k) as i32;
            let el = m.p[m.v[prv(c0) as usize] as usize].distance(m.p[m.v[nxt(c0) as usize] as usize]);
            let cd = centre(f).distance(centre(g));
            fr.w[f][k] = if cd > 0.0 && !no_fw { clamp(el / (cd * 1.7320508), 0.2, 5.0) } else { 1.0 };
            let ag = fr.alpha[g][(d % 3) as usize];
            fr.rot[f][k] = Cplx::polar(1.0, 4.0 * (ag - fr.alpha[f][k]));
        }
    }
    fr
}

pub fn axis_from_z(fr: &FaceFrames, f: usize, z: Cplx) -> Vec3 {
    let theta = z.arg() / 4.0;
    fr.e1[f] * theta.cos() + fr.e2[f] * theta.sin()
}

/// Soft pull of the field towards given directions.
pub struct SoftField {
    pub w: Vec<f64>,
    pub target: Vec<Cplx>,
    pub pulled: i32,
}

impl SoftField {
    pub fn new(n_f: usize) -> Self { SoftField { w: vec![0.0; n_f], target: vec![Cplx::ONE; n_f], pulled: 0 } }
}

#[derive(Clone, Copy, Default)]
struct T6 { xx: f64, xy: f64, xz: f64, yy: f64, yz: f64, zz: f64, l: f64 }

fn smooth01(x: f64, a: f64, b: f64) -> f64 {
    let t = clamp((x - a) / (b - a), 0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

#[derive(PartialEq)]
struct QE(f64, i32);
impl Eq for QE {}
impl PartialOrd for QE { fn partial_cmp(&self, o: &Self) -> Option<Ordering> { Some(self.cmp(o)) } }
impl Ord for QE {
    // reversed: BinaryHeap pops the smallest distance first
    fn cmp(&self, o: &Self) -> Ordering { o.0.partial_cmp(&self.0).unwrap_or(Ordering::Equal).then(o.1.cmp(&self.1)) }
}

/// Soft alignment to creases and grooves (eyelids, lips, nostril and ear rims). The dihedral angle across every
/// edge is summed into a tensor per face; its dominant axis is the line of the crease. Where that is clear the
/// field is pulled towards it. Flat or round places get no pull.
pub fn curvature_alignment(m: &CMesh, fr: &FaceFrames, strength: f64, gate_angle: f64, smooth_passes: usize) -> SoftField {
    let n_f = m.num_face_slots() as usize;
    let mut sf = SoftField::new(n_f);
    if strength <= 0.0 || gate_angle <= 0.0 { return sf; }
    let mut t = vec![T6::default(); n_f];
    for f in 0..n_f {
        if !m.falive[f] { continue; }
        for k in 0..3 {
            let g = fr.nb[f][k];
            if g < 0 || (g as usize) < f { continue; }
            let c0 = (3 * f + k) as i32;
            let ev = m.p[m.v[prv(c0) as usize] as usize] - m.p[m.v[nxt(c0) as usize] as usize];
            let len = ev.length();
            if len <= 0.0 { continue; }
            let e = ev / len;
            let gi = g as usize;
            let beta = fr.n[f].cross(fr.n[gi]).length().atan2(fr.n[f].dot(fr.n[gi]));
            let wgt = beta * len;
            for idx in [f, gi] {
                let tt = &mut t[idx];
                tt.xx += wgt * e.x * e.x; tt.xy += wgt * e.x * e.y; tt.xz += wgt * e.x * e.z;
                tt.yy += wgt * e.y * e.y; tt.yz += wgt * e.y * e.z; tt.zz += wgt * e.z * e.z;
                tt.l += len;
            }
        }
    }
    for _ in 0..smooth_passes {
        let mut u = vec![T6::default(); n_f];
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let mut a = t[f];
            for k in 0..3 {
                let g = fr.nb[f][k];
                if g < 0 { continue; }
                let b = t[g as usize];
                a.xx += b.xx; a.xy += b.xy; a.xz += b.xz; a.yy += b.yy; a.yz += b.yz; a.zz += b.zz; a.l += b.l;
            }
            u[f] = a;
        }
        t = u;
    }
    for f in 0..n_f {
        if !m.falive[f] || t[f].l <= 0.0 { continue; }
        let tt = t[f];
        let (a, b) = (fr.e1[f], fr.e2[f]);
        let mul = |v: Vec3| Vec3::new(tt.xx * v.x + tt.xy * v.y + tt.xz * v.z, tt.xy * v.x + tt.yy * v.y + tt.yz * v.z, tt.xz * v.x + tt.yz * v.y + tt.zz * v.z);
        let aaa = a.dot(mul(a));
        let abb = b.dot(mul(b));
        let aab = a.dot(mul(b));
        let tr = aaa + abb;
        if tr <= 1e-12 { continue; }
        let diff = ((aaa - abb) * (aaa - abb) * 0.25 + aab * aab).sqrt();
        let aniso = 2.0 * diff / tr; // 0 round, 1 pure crease
        let mag = tr / tt.l;
        let phi = 0.5 * (2.0 * aab).atan2(aaa - abb);
        let wgt = smooth01(aniso, 0.45, 0.85) * smooth01(mag, 0.35 * gate_angle, 0.9 * gate_angle);
        if wgt <= 0.01 { continue; }
        sf.w[f] = strength * wgt;
        sf.target[f] = Cplx::polar(1.0, 4.0 * phi);
        sf.pulled += 1;
    }
    // Rings: rows run along lines of equal distance to the crease, closing into loops around an eye or mouth.
    let ring_k = env_f64("FORGE_RING").unwrap_or(0.0);
    let ring_r = env_f64("FORGE_RINGR").unwrap_or(3.0);
    if ring_k > 0.0 && sf.pulled > 0 {
        let n_v = m.num_vert_slots() as usize;
        let mut adj: Vec<Vec<(i32, f64)>> = vec![Vec::new(); n_v];
        let (mut edge_sum, mut edge_cnt) = (0.0, 0i64);
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            for k in 0..3 {
                let a = m.v[3 * f + k];
                let b = m.v[3 * f + (k + 1) % 3];
                let len = m.p[a as usize].distance(m.p[b as usize]);
                adj[a as usize].push((b, len));
                adj[b as usize].push((a, len));
                edge_sum += len;
                edge_cnt += 1;
            }
        }
        let mean_edge = if edge_cnt > 0 { edge_sum / edge_cnt as f64 } else { 0.0 };
        let r = ring_r * mean_edge;
        let mut dist = vec![1e300f64; n_v];
        let mut pq = BinaryHeap::new();
        for f in 0..n_f {
            if m.falive[f] && sf.w[f] >= 0.5 * strength {
                for k in 0..3 {
                    let v = m.v[3 * f + k] as usize;
                    if dist[v] > 0.0 { dist[v] = 0.0; pq.push(QE(0.0, v as i32)); }
                }
            }
        }
        while let Some(QE(d, v)) = pq.pop() {
            if d > dist[v as usize] || d > r { continue; }
            for &(u, len) in &adj[v as usize] {
                if d + len < dist[u as usize] { dist[u as usize] = d + len; pq.push(QE(dist[u as usize], u)); }
            }
        }
        let mut ring_w = vec![0.0f64; n_f];
        let mut ring_t = vec![Cplx::ONE; n_f];
        for f in 0..n_f {
            if !m.falive[f] || sf.w[f] > 0.0 { continue; }
            let (v0, v1, v2) = (m.v[3 * f] as usize, m.v[3 * f + 1] as usize, m.v[3 * f + 2] as usize);
            let (d0, d1, d2) = (dist[v0], dist[v1], dist[v2]);
            if d0 > r || d1 > r || d2 > r { continue; }
            let (p0, p1, p2) = (m.p[v0], m.p[v1], m.p[v2]);
            let n = fr.n[f];
            let a2 = (p1 - p0).cross(p2 - p0).length();
            if a2 <= 1e-18 { continue; }
            let g = (n.cross(p2 - p1) * d0 + n.cross(p0 - p2) * d1 + n.cross(p1 - p0) * d2) / a2;
            let gl = g.length();
            if gl < 1e-9 { continue; }
            let tv = n.cross(g / gl);
            let phi = tv.dot(fr.e2[f]).atan2(tv.dot(fr.e1[f]));
            let dm = (d0 + d1 + d2) / 3.0;
            let fade = (1.0 - dm / r).max(0.0);
            ring_w[f] = strength * ring_k * fade * fade;
            ring_t[f] = Cplx::polar(1.0, 4.0 * phi);
        }
        for f in 0..n_f {
            if ring_w[f] > 0.01 { sf.w[f] = ring_w[f]; sf.target[f] = ring_t[f]; sf.pulled += 1; }
        }
    }
    sf
}

/// Flow guides: lines drawn by the user; rows of quads should run along them. Faces near a guide segment get a
/// pull towards its direction, fading with distance. A soft hint, not a hard constraint.
pub fn add_flow_guides(m: &CMesh, fr: &FaceFrames, guides: &SegmentIndex, strength: f64, radius_edges: f64, sf: &mut SoftField) -> i32 {
    if guides.is_empty() || strength <= 0.0 { return 0; }
    let n_f = m.num_face_slots() as usize;
    let (mut edge_sum, mut edge_cnt) = (0.0, 0i64);
    for f in 0..n_f {
        if !m.falive[f] { continue; }
        for k in 0..3 {
            edge_sum += m.p[m.v[3 * f + k] as usize].distance(m.p[m.v[3 * f + (k + 1) % 3] as usize]);
            edge_cnt += 1;
        }
    }
    let r = radius_edges * if edge_cnt > 0 { edge_sum / edge_cnt as f64 } else { 0.0 };
    if r <= 0.0 { return 0; }
    let mut hit = 0;
    for f in 0..n_f {
        if !m.falive[f] { continue; }
        let c = (m.p[m.v[3 * f] as usize] + m.p[m.v[3 * f + 1] as usize] + m.p[m.v[3 * f + 2] as usize]) / 3.0;
        let h = guides.closest(c);
        if h.seg < 0 { continue; }
        let d = h.d2.sqrt();
        if d >= r { continue; }
        let sg = guides.segment(h.seg);
        let mut dir = sg[1] - sg[0];
        if dir.length() < 1e-12 { continue; }
        dir = dir.normalized();
        let t = dir - fr.n[f] * dir.dot(fr.n[f]);
        if t.length() < 0.3 { continue; } // the guide stands on end to the surface here
        let phi = t.dot(fr.e2[f]).atan2(t.dot(fr.e1[f]));
        let fade = 1.0 - d / r;
        let w = strength * fade * fade;
        if w > sf.w[f] {
            if sf.w[f] <= 0.0 { sf.pulled += 1; }
            sf.w[f] = w;
            sf.target[f] = Cplx::polar(1.0, 4.0 * phi);
            hit += 1;
        }
    }
    hit
}

// ------------------------------------------------------------------------------------------------ linear solver

/// y = (L restricted to free faces) x; fixed faces count as zero (the caller moves them to the right-hand side).
struct FieldSystem<'a> {
    fr: &'a FaceFrames,
    fixed: &'a [bool],
    soft: Option<&'a [f64]>,
}

impl<'a> FieldSystem<'a> {
    fn deg(&self, f: usize) -> f64 {
        let mut d = self.soft.map_or(0.0, |s| s[f]);
        for k in 0..3 { if self.fr.nb[f][k] >= 0 { d += self.fr.w[f][k]; } }
        d
    }
    fn apply(&self, x: &[Cplx], y: &mut [Cplx], eps: f64, free: &[usize]) {
        for &f in free {
            let mut s = x[f] * (self.deg(f) + eps);
            for k in 0..3 {
                let g = self.fr.nb[f][k];
                if g < 0 || self.fixed[g as usize] { continue; }
                s -= self.fr.rot[f][k].conj() * x[g as usize] * self.fr.w[f][k]; // r_{g->f} = conj(r_{f->g})
            }
            y[f] = s;
        }
    }
}

fn conjugate_gradient(a: &FieldSystem, free: &[usize], b: &[Cplx], x: &mut [Cplx], eps: f64, max_iter: usize, tol: f64) -> usize {
    let n = x.len();
    let mut r = vec![Cplx::ZERO; n];
    let mut p = vec![Cplx::ZERO; n];
    let mut ap = vec![Cplx::ZERO; n];
    let mut zv = vec![Cplx::ZERO; n];
    a.apply(x, &mut ap, eps, free);
    let (mut bnorm, mut rz) = (0.0, 0.0);
    for &f in free { r[f] = b[f] - ap[f]; bnorm += b[f].norm(); }
    if bnorm < 1e-300 { return 0; }
    let precond = |f: usize| 1.0 / (a.deg(f) + eps);
    for &f in free {
        zv[f] = r[f] * precond(f);
        p[f] = zv[f];
        rz += (r[f].conj() * zv[f]).re;
    }
    let mut it = 0;
    while it < max_iter {
        a.apply(&p, &mut ap, eps, free);
        let mut pap = 0.0;
        for &f in free { pap += (p[f].conj() * ap[f]).re; }
        if pap.abs() < 1e-300 { break; }
        let alpha = rz / pap;
        let mut rnorm = 0.0;
        for &f in free {
            x[f] += p[f] * alpha;
            r[f] -= ap[f] * alpha;
            rnorm += r[f].norm();
        }
        if rnorm <= tol * tol * bnorm { it += 1; break; }
        let mut rz_new = 0.0;
        for &f in free {
            zv[f] = r[f] * precond(f);
            rz_new += (r[f].conj() * zv[f]).re;
        }
        let beta = rz_new / rz;
        rz = rz_new;
        for &f in free { p[f] = zv[f] + p[f] * beta; }
        it += 1;
    }
    it
}

/// One unit complex number per live face (garbage for dead slots).
pub struct FieldInputs<'a> {
    pub use_features: bool,
    pub curv_strength: f64,
    pub curv_gate: f64,
    pub guides: Option<&'a SegmentIndex>,
    pub guide_strength: f64,
    pub guide_radius: f64,
}

impl<'a> Default for FieldInputs<'a> {
    fn default() -> Self {
        FieldInputs { use_features: true, curv_strength: 0.0, curv_gate: 0.0, guides: None, guide_strength: 0.0, guide_radius: 4.0 }
    }
}

pub fn solve_cross_field(m: &CMesh, fr: &FaceFrames, inp: &FieldInputs) -> Vec<Cplx> {
    let n_f = m.num_face_slots() as usize;
    let mut z = vec![Cplx::ONE; n_f];
    let mut fixed = vec![false; n_f];
    // constraints from feature edges and borders
    if inp.use_features {
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let mut target: Option<Cplx> = None;
            let mut conflict = false;
            for k in 0..3 {
                if !fr.feat[f][k] { continue; }
                let t = Cplx::polar(1.0, 4.0 * fr.alpha[f][k]);
                match target {
                    None => target = Some(t),
                    Some(tg) => if (tg.conj() * t).re < 0.5 { conflict = true; }, // corner: edges not orthogonal
                }
            }
            if let (Some(t), false) = (target, conflict) { fixed[f] = true; z[f] = t; }
        }
    }
    // connected components over face adjacency
    let mut comp = vec![-1i32; n_f];
    let mut nc = 0usize;
    let mut stack: Vec<usize> = Vec::new();
    for s in 0..n_f {
        if !m.falive[s] || comp[s] >= 0 { continue; }
        comp[s] = nc as i32;
        stack.clear();
        stack.push(s);
        while let Some(f) = stack.pop() {
            for k in 0..3 {
                let g = fr.nb[f][k];
                if g >= 0 && comp[g as usize] < 0 { comp[g as usize] = nc as i32; stack.push(g as usize); }
            }
        }
        nc += 1;
    }
    let mut members: Vec<Vec<usize>> = vec![Vec::new(); nc];
    let mut has_fixed = vec![false; nc];
    for f in 0..n_f {
        if m.falive[f] { members[comp[f] as usize].push(f); if fixed[f] { has_fixed[comp[f] as usize] = true; } }
    }

    let mut soft = SoftField::new(n_f);
    if inp.curv_strength > 0.0 && inp.curv_gate > 0.0 { soft = curvature_alignment(m, fr, inp.curv_strength, inp.curv_gate, 2); }
    if let Some(g) = inp.guides {
        if inp.guide_strength > 0.0 { add_flow_guides(m, fr, g, inp.guide_strength, inp.guide_radius, &mut soft); }
    }
    for f in 0..n_f { if m.falive[f] && fixed[f] { soft.w[f] = 0.0; } }
    let mut has_soft = vec![false; nc];
    for f in 0..n_f { if m.falive[f] && soft.w[f] > 0.0 { has_soft[comp[f] as usize] = true; } }
    let sys = FieldSystem { fr, fixed: &fixed, soft: if soft.pulled > 0 { Some(&soft.w) } else { None } };
    let fout = env_f64("FORGE_FOUT").map_or(12, |v| v as usize);
    let fcg = env_f64("FORGE_FCG").map_or(400, |v| v as usize);
    let mut b = vec![Cplx::ZERO; n_f];
    for c in 0..nc {
        let free: Vec<usize> = members[c].iter().copied().filter(|&f| !fixed[f]).collect();
        if free.is_empty() { continue; }
        if has_fixed[c] || has_soft[c] {
            // L_FF x = - L_FC z_C (+ tiny regulariser; + the soft pull towards crease directions)
            for &f in &free {
                let mut s = Cplx::ZERO;
                if soft.w[f] > 0.0 { s += soft.target[f] * soft.w[f]; }
                for k in 0..3 {
                    let g = fr.nb[f][k];
                    if g >= 0 && fixed[g as usize] { s += fr.rot[f][k].conj() * z[g as usize] * fr.w[f][k]; }
                }
                b[f] = s;
            }
            for &f in &free {
                z[f] = if b[f] == Cplx::ZERO { Cplx::ONE } else { b[f] / b[f].abs() };
            }
            conjugate_gradient(&sys, &free, &b, &mut z, 1e-9, 2000, 1e-8);
        } else {
            // smoothest field: inverse iteration on (L + eps I)
            let eps = 1e-3;
            let mut seed: u64 = 12345 + c as u64;
            for &f in &free {
                seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                z[f] = Cplx::polar(1.0, (seed >> 40) as f64 / (1u64 << 24) as f64 * 2.0 * PI);
            }
            let mut rhs = vec![Cplx::ZERO; n_f];
            for _ in 0..fout {
                for &f in &free { rhs[f] = z[f]; }
                conjugate_gradient(&sys, &free, &rhs, &mut z, eps, fcg, 1e-6);
                let mut nrm = 0.0;
                for &f in &free { nrm += z[f].norm(); }
                nrm = (nrm / free.len() as f64).sqrt();
                if nrm < 1e-300 { break; }
                for &f in &free { z[f] = z[f] / nrm; }
            }
        }
        for &f in &free {
            let a = z[f].abs();
            z[f] = if a > 1e-12 { z[f] / a } else { Cplx::ONE };
        }
    }
    z
}
