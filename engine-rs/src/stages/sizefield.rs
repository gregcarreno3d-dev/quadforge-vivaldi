//! Adaptive element size from the shape: thickness (shrinking ball), curvature and narrow gaps.
//!
//! Every function returns a per-vertex edge-length multiplier `s` in `[min_scale, 1]` (1 = the global size),
//! graded so that the size never changes faster than `grade` per unit of distance.
use crate::math::{clamp, Aabb, Vec3};
use crate::mesh::bvh::SurfaceIndex;
use crate::stages::repair::TriSoup;
use crate::util::parallel::par_fill;

#[derive(Clone, Debug)]
pub struct SizeFieldParams {
    /// Global quad edge length estimate.
    pub rho: f64,
    /// Quad edge <= ratio * local ball radius.
    pub ratio: f64,
    /// Never finer than this fraction of the global size.
    pub min_scale: f64,
    /// Size growth per unit distance.
    pub grade: f64,
    /// Only refine where curvature * ball radius exceeds this (tubes, not flat walls).
    pub min_curv: f64,
    pub threads: usize,
}

impl Default for SizeFieldParams {
    fn default() -> Self { SizeFieldParams { rho: 0.0, ratio: 0.8, min_scale: 0.3, grade: 0.5, min_curv: 0.5, threads: 1 } }
}

#[derive(Clone, Debug)]
pub struct SizeFieldStats {
    /// Vertices whose size was reduced.
    pub refined: i32,
    pub min_seen: f64,
}

impl Default for SizeFieldStats {
    fn default() -> Self { SizeFieldStats { refined: 0, min_seen: 1.0 } }
}

fn vertex_normals(soup: &TriSoup) -> Vec<Vec3> {
    let mut n = vec![Vec3::ZERO; soup.p.len()];
    for t in &soup.f {
        let (a, b, c) = (soup.p[t[0] as usize], soup.p[t[1] as usize], soup.p[t[2] as usize]);
        let fn_ = (b - a).cross(c - a);
        for k in 0..3 { n[t[k] as usize] += fn_; }
    }
    for v in n.iter_mut() { *v = v.normalized(); }
    n
}

fn undirected_edges(soup: &TriSoup) -> Vec<[i32; 2]> {
    let mut edges = Vec::with_capacity(soup.f.len() * 3);
    for t in &soup.f { for k in 0..3 { edges.push([t[k], t[(k + 1) % 3]]); } }
    edges
}

fn finish(soup: &TriSoup, s: &mut [f32], sp: &SizeFieldParams, stats: &mut SizeFieldStats) {
    grade_size_field(soup, s, sp);
    for &x in s.iter() {
        if x < 0.999 { stats.refined += 1; }
        stats.min_seen = stats.min_seen.min(x as f64);
    }
}

/// Gradation: `s_i <= s_j + grade * |e| / rho`.
pub fn grade_size_field(soup: &TriSoup, s: &mut [f32], sp: &SizeFieldParams) {
    let edges = undirected_edges(soup);
    for _ in 0..60 {
        let mut changed = false;
        for e in &edges {
            let (a, b) = (e[0] as usize, e[1] as usize);
            let l = soup.p[a].distance(soup.p[b]) * sp.grade / sp.rho;
            if s[a] as f64 > s[b] as f64 + l + 1e-6 { s[a] = (s[b] as f64 + l) as f32; changed = true; }
            if s[b] as f64 > s[a] as f64 + l + 1e-6 { s[b] = (s[a] as f64 + l) as f32; changed = true; }
        }
        if !changed { break; }
    }
}

/// Radius of the largest ball touching the surface at `p` (normal `n`) that stays on one side. `inside` selects the
/// shrinking ball inside the shape (thickness) or the one outside it (gap width).
fn ball_radius(idx: &SurfaceIndex, p: Vec3, n: Vec3, rmax: f64, inside: bool) -> f64 {
    if n.length2() < 0.5 { return rmax; }
    let mut r = rmax;
    for _ in 0..14 {
        let q = if inside { p - n * r } else { p + n * r };
        let h = idx.closest_any(q);
        if h.tri < 0 { break; }
        if idx.face_normal(h.tri).dot(n) > 0.5 { break; } // the local neighbourhood, not the other side
        let d = if inside { p - h.p } else { h.p - p };
        let d2 = d.length2();
        if d2 < 1e-12 * rmax * rmax { break; }      // the ball touches only at p
        let nd = n.dot(d);
        if nd <= 1e-9 * d2.sqrt() { break; }        // the closest point lies in front of the tangent plane
        let rn = d2 / (2.0 * nd);
        if rn >= r * 0.999 { break; }
        r = rn;
    }
    r
}

/// Thickness-based size ("shrinking ball"): a tube of radius a needs about eight quads around it, so the quad
/// edge is limited to ratio * r.
pub fn thickness_size_field(soup: &TriSoup, sp: &SizeFieldParams, stats: &mut SizeFieldStats) -> Vec<f32> {
    let n_v = soup.p.len();
    let mut s = vec![1.0f32; n_v];
    if n_v == 0 || soup.f.is_empty() || !(sp.rho > 0.0) { return s; }
    let nrm = vertex_normals(soup);
    let mut bbox = Aabb::default();
    for p in &soup.p { bbox.expand_point(*p); }
    // A ball wider than rho / ratio asks for no refinement at all, so the search starts at that radius instead of
    // the size of the model: the query point stays close to the surface, where the BVH prunes well.
    let rmax = (0.5 * (bbox.hi - bbox.lo).length()).min(sp.rho / sp.ratio * 1.05);
    let idx = SurfaceIndex::new(&soup.p, &soup.f);
    let mut radius = vec![rmax; n_v];
    // The size field is smooth, so on dense inputs the ball is measured on a sample of the vertices (about
    // SAMPLES of them) and the others take the value of the nearest sampled vertex along the surface.
    const SAMPLES: usize = 40000;
    let stride = (n_v / SAMPLES).max(1);
    let sampled: Vec<usize> = if stride > 1 {
        (0..n_v).filter(|&v| (v as u32).wrapping_mul(2654435761u32) % (stride as u32) == 0).collect()
    } else { Vec::new() };
    let measure = |v: usize| ball_radius(&idx, soup.p[v], nrm[v], rmax, true);
    if stride > 1 {
        let mut vals = vec![0.0f64; sampled.len()];
        par_fill(&mut vals, sp.threads, |w| measure(sampled[w]));
        for (w, &v) in sampled.iter().enumerate() { radius[v] = vals[w]; }
        // multi-source breadth-first fill from the sampled vertices
        let mut start = vec![0usize; n_v + 1];
        for t in &soup.f { for k in 0..3 { start[t[k] as usize + 1] += 2; } }
        for v in 0..n_v { start[v + 1] += start[v]; }
        let mut adj = vec![0i32; start[n_v]];
        let mut fill_at = start[..n_v].to_vec();
        for t in &soup.f {
            for k in 0..3 {
                let v = t[k] as usize;
                adj[fill_at[v]] = t[(k + 1) % 3]; fill_at[v] += 1;
                adj[fill_at[v]] = t[(k + 2) % 3]; fill_at[v] += 1;
            }
        }
        let mut done = vec![false; n_v];
        let mut frontier = sampled.clone();
        for &v in &sampled { done[v] = true; }
        let mut next = Vec::new();
        while !frontier.is_empty() {
            next.clear();
            for &v in &frontier {
                for k in start[v]..start[v + 1] {
                    let u = adj[k] as usize;
                    if !done[u] { done[u] = true; radius[u] = radius[v]; next.push(u); }
                }
            }
            std::mem::swap(&mut frontier, &mut next);
        }
    } else {
        par_fill(&mut radius, sp.threads, measure);
    }
    // curvature: a thin flat wall needs no resolution along itself, a thin tube does
    let mut kap = vec![0.0f64; n_v];
    let mut kap_avg = vec![0.0f64; n_v];
    let mut kcount = vec![0i32; n_v];
    for t in &soup.f {
        for k in 0..3 {
            let (a, b) = (t[k] as usize, t[(k + 1) % 3] as usize);
            let l = soup.p[a].distance(soup.p[b]);
            if l <= 0.0 { continue; }
            let kk = clamp(nrm[a].dot(nrm[b]), -1.0, 1.0).acos() / l;
            kap[a] = kap[a].max(kk);
            kap[b] = kap[b].max(kk);
        }
    }
    for t in &soup.f {
        for k in 0..3 {
            let v = t[k] as usize;
            kap_avg[v] += kap[t[(k + 1) % 3] as usize] + kap[v];
            kcount[v] += 2;
        }
    }
    for v in 0..n_v { if kcount[v] > 0 { kap[v] = kap_avg[v] / kcount[v] as f64; } }
    for v in 0..n_v {
        if kap[v] * radius[v] < sp.min_curv { s[v] = 1.0; continue; }
        let hmax = sp.ratio * radius[v];
        s[v] = clamp(hmax / sp.rho, sp.min_scale, 1.0) as f32;
    }
    finish(soup, &mut s, sp, stats);
    s
}

/// Smaller quads where the surface bends sharply (eyelids, lips, nostrils, ear rims): a quad edge should not
/// turn the surface by more than `angle` radians, so its length is limited to `angle / curvature`.
pub fn curvature_size_field(soup: &TriSoup, sp: &SizeFieldParams, angle: f64, stats: &mut SizeFieldStats) -> Vec<f32> {
    let n_v = soup.p.len();
    let mut s = vec![1.0f32; n_v];
    if n_v == 0 || soup.f.is_empty() || !(sp.rho > 0.0) || !(angle > 0.0) { return s; }
    let nrm = vertex_normals(soup);
    let mut kap = vec![0.0f64; n_v];
    let mut cnt = vec![0i32; n_v];
    let (mut lsum, mut lcount) = (0.0f64, 0usize);
    for t in &soup.f {
        for k in 0..3 {
            let (a, b) = (t[k] as usize, t[(k + 1) % 3] as usize);
            let l = soup.p[a].distance(soup.p[b]);
            if l <= 0.0 { continue; }
            let kk = clamp(nrm[a].dot(nrm[b]), -1.0, 1.0).acos() / l;
            kap[a] += kk; kap[b] += kk; cnt[a] += 1; cnt[b] += 1;
            lsum += l; lcount += 1;
        }
    }
    for v in 0..n_v { if cnt[v] > 0 { kap[v] /= cnt[v] as f64; } }
    // smooth over about half a quad so that mesh noise does not count as curvature
    let l0 = if lcount > 0 { lsum / lcount as f64 } else { sp.rho };
    let q = 0.5 * sp.rho / l0.max(1e-12);
    let iters = clamp(q * q, 1.0, 150.0) as i32;
    let edges = undirected_edges(soup);
    let mut acc = vec![0.0f64; n_v];
    let mut deg = vec![0i32; n_v];
    for e in &edges { deg[e[0] as usize] += 1; deg[e[1] as usize] += 1; }
    for _ in 0..iters {
        acc.iter_mut().for_each(|x| *x = 0.0);
        for e in &edges {
            let (a, b) = (e[0] as usize, e[1] as usize);
            acc[a] += kap[b];
            acc[b] += kap[a];
        }
        for v in 0..n_v { if deg[v] > 0 { kap[v] = 0.5 * kap[v] + 0.5 * acc[v] / deg[v] as f64; } }
    }
    for v in 0..n_v {
        if kap[v] <= 0.0 { continue; }
        s[v] = clamp(angle / kap[v] / sp.rho, sp.min_scale, 1.0) as f32;
    }
    finish(soup, &mut s, sp, stats);
    s
}

/// Narrow gaps and creases (the groove under a lip, between fingers): the largest ball that fits OUTSIDE the
/// surface at a vertex without crossing it. Faces there are limited to ratio * that radius.
pub fn gap_size_field(soup: &TriSoup, sp: &SizeFieldParams, stats: &mut SizeFieldStats) -> Vec<f32> {
    let n_v = soup.p.len();
    let mut s = vec![1.0f32; n_v];
    if n_v == 0 || soup.f.is_empty() || !(sp.rho > 0.0) { return s; }
    let nrm = vertex_normals(soup);
    let mut bbox = Aabb::default();
    for p in &soup.p { bbox.expand_point(*p); }
    let rmax = 0.5 * (bbox.hi - bbox.lo).length();
    let idx = SurfaceIndex::new(&soup.p, &soup.f);
    let mut radius = vec![rmax; n_v];
    par_fill(&mut radius, sp.threads, |v| ball_radius(&idx, soup.p[v], nrm[v], rmax, false));
    if std::env::var_os("FORGE_DBG").is_some() {
        let mn = radius.iter().cloned().fold(1e30, f64::min);
        let below = radius.iter().filter(|&&r| sp.ratio * r < sp.rho).count();
        eprintln!("gap: min radius {mn:.3} rho {:.3} below {below} rmax {rmax:.1}", sp.rho);
    }
    for v in 0..n_v {
        let hmax = sp.ratio * radius[v];
        s[v] = clamp(hmax / sp.rho, sp.min_scale, 1.0) as f32;
    }
    finish(soup, &mut s, sp, stats);
    s
}
