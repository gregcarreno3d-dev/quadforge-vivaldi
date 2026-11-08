//! Quad meshes from the isotropic triangle mesh (the "pair" route).
//!
//!   1. smooth cross field (`field`), aligned to feature lines and borders
//!   2. pair triangles into quads: greedy by cost, then augmenting paths so almost every triangle finds a
//!      partner; then valence-aware annealing so the pairing leaves few irregular vertices
//!   3. split every polygon into quads (quad -> 4, leftover triangle -> 3): pure quads
//!   4. relax: every quad proposes its nearest square (turned a little towards the field); vertices average the
//!      proposals and snap back onto the original surface
//!
//! Work areas: `pair_cost` (what a good quad is) · `match_triangles` · `refine_matching` (pole annealing) ·
//! `subdivide` · `relax`.
use crate::io::fmesh::PolyMesh;
use crate::math::{clamp, Cplx, Vec3, PI};
use crate::mesh::bvh::{SegmentIndex, SurfaceIndex};
use crate::mesh::cmesh::{face_of, nxt, prv, CMesh, V_CREASE, V_FIXED, V_FREE};
use crate::stages::field::{axis_from_z, build_frames, solve_cross_field, FaceFrames, FieldInputs};
use crate::stages::remesh::ProgressFn;
use crate::stages::repair::edge_key;
use crate::util::parallel::par_fill;
use std::collections::HashMap;
use std::time::Instant;

pub struct QuadParams {
    pub threads: usize,
    pub project: bool,
    pub relax_iterations: i32,
    pub relax_step: f64,
    /// 0 = ignore the field while relaxing, 1 = snap fully to it.
    pub field_align: f64,
    /// Weight of the field term when pairing.
    pub field_weight: f64,
    pub use_field: bool,
    /// Price of one irregular vertex in the pairing search (0 = off).
    pub pole_weight: f64,
    /// Annealing moves per triangle.
    pub pole_sweeps: i32,
    /// Longest alternating cycle tried, in pairs.
    pub cycle_pairs: i32,
    pub progress: Option<ProgressFn>,
}

impl Default for QuadParams {
    fn default() -> Self {
        QuadParams {
            threads: 1, project: true, relax_iterations: 30, relax_step: 0.8, field_align: 0.5, field_weight: 1.0,
            use_field: true, pole_weight: 10.0, pole_sweeps: 150, cycle_pairs: 5, progress: None,
        }
    }
}

#[derive(Default, Clone, Debug)]
pub struct QuadStats {
    pub triangles: i32,
    pub pairs: i32,
    pub leftover: i32,
    /// Vertices whose valence is not the ideal one.
    pub irregular_before: i32,
    pub irregular_after: i32,
    pub quads: i32,
    pub field_seconds: f64,
    pub match_seconds: f64,
    pub relax_seconds: f64,
}

pub struct Quadifier<'a> {
    m: &'a CMesh,
    reference: &'a SurfaceIndex,
    feat: &'a SegmentIndex,
    l: f64,
    prm: QuadParams,
    frames: FaceFrames,
    z: Vec<Cplx>,
    p: Vec<Vec3>,
    vtype: Vec<u8>,
    q: Vec<[i32; 4]>,
    /// Triangle whose field direction guides each quad.
    quad_source: Vec<i32>,
}

fn angle_between(a: Vec3, b: Vec3) -> f64 {
    let (la, lb) = (a.length(), b.length());
    if la < 1e-300 || lb < 1e-300 { return 0.0; }
    clamp(a.dot(b) / (la * lb), -1.0, 1.0).acos()
}

/// Distance of angle `a` (mod 90 deg) from 45 deg, in [0, 1]: 0 at 45 deg, 1 on an axis.
fn off45(a: f64) -> f64 {
    let r = a.abs() % (PI / 2.0);
    (r - PI / 4.0).abs() / (PI / 4.0)
}

impl<'a> Quadifier<'a> {
    pub fn new(tri: &'a CMesh, reference: &'a SurfaceIndex, feat: &'a SegmentIndex, edge_length: f64, prm: QuadParams) -> Self {
        Quadifier {
            m: tri, reference, feat, l: edge_length, prm,
            frames: FaceFrames { e1: vec![], e2: vec![], n: vec![], nb: vec![], rot: vec![], feat: vec![], alpha: vec![], w: vec![] },
            z: vec![], p: vec![], vtype: vec![], q: vec![], quad_source: vec![],
        }
    }

    fn report(&self, pct: i32, s: &str) { if let Some(f) = &self.prm.progress { f(pct, s); } }

    // ------------------------------------------------------------------ pairing

    /// Cost of removing the edge opposite corner `c` (merging its two triangles into one quad).
    fn pair_cost(&self, c: i32) -> f64 {
        let m = self.m;
        let d = m.o[c as usize];
        let (f, g) = (face_of(c) as usize, face_of(d) as usize);
        let (n1, p1) = (nxt(c), prv(c));
        let a = m.p[m.v[c as usize] as usize];
        let b = m.p[m.v[n1 as usize] as usize];
        let cc = m.p[m.v[p1 as usize] as usize];
        let dd = m.p[m.v[d as usize] as usize];
        let q = [a, b, dd, cc]; // counter clockwise
        let mut angle_cost = 0.0;
        let mut max_angle: f64 = 0.0;
        for k in 0..4 {
            let u = q[(k + 1) % 4] - q[k];
            let v = q[(k + 3) % 4] - q[k];
            let ang = angle_between(u, v);
            max_angle = max_angle.max(ang);
            let e = (ang - PI / 2.0) / (PI / 4.0);
            angle_cost += e * e;
        }
        if max_angle > 170.0 * PI / 180.0 { angle_cost += 1000.0; } // (almost) flat or concave corner
        let flat = 1.0 - clamp(self.frames.n[f].dot(self.frames.n[g]), -1.0, 1.0);
        let mut cost = angle_cost + 40.0 * flat;
        // side length balance
        let s = [a.distance(b), b.distance(dd), dd.distance(cc), cc.distance(a)];
        let mean = 0.25 * (s[0] + s[1] + s[2] + s[3]);
        for x in s { let e = (x - mean) / mean.max(1e-300); cost += 2.0 * e * e; }
        if self.prm.use_field {
            // the removed edge should sit at 45 degrees to the field axes
            let diag = (cc - b).normalized();
            let dir_f = axis_from_z(&self.frames, f, self.z[f]);
            let dir_g = axis_from_z(&self.frames, g, self.z[g]);
            let a1 = diag.dot(self.frames.n[f].cross(dir_f)).atan2(diag.dot(dir_f));
            let a2 = diag.dot(self.frames.n[g].cross(dir_g)).atan2(diag.dot(dir_g));
            let e = 0.5 * (off45(a1) + off45(a2));
            cost += self.prm.field_weight * 6.0 * e * e;
        }
        cost
    }

    fn match_triangles(&self, st: &mut QuadStats) -> Vec<i32> {
        let m = self.m;
        let n_f = m.num_face_slots() as usize;
        let mut mate = vec![-1i32; n_f];
        let mut cand: Vec<(f64, i32)> = Vec::new();
        for c in 0..(n_f * 3) as i32 {
            if !m.falive[face_of(c) as usize] { continue; }
            let d = m.o[c as usize];
            if d < 0 || d < c || m.e[c as usize] != 0 { continue; } // never remove feature lines
            cand.push((self.pair_cost(c), c));
        }
        cand.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
        for &(cost, c) in &cand {
            let f = face_of(c) as usize;
            let g = face_of(m.o[c as usize]) as usize;
            if mate[f] < 0 && mate[g] < 0 && cost < 900.0 { mate[f] = g as i32; mate[g] = f as i32; }
        }
        // augmenting paths (alternating BFS) for triangles still alone
        let mut seen = vec![0i32; n_f];
        let mut outer_from = vec![-1i32; n_f];
        let mut queue: Vec<usize> = Vec::new();
        let mut stamp = 0;
        let allowed = |f: usize, k: usize| -> i32 {
            let c = 3 * f + k;
            let d = m.o[c];
            if d < 0 || m.e[c] != 0 { -1 } else { face_of(d) }
        };
        for root in 0..n_f {
            if !m.falive[root] || mate[root] >= 0 { continue; }
            stamp += 1;
            queue.clear();
            queue.push(root);
            seen[root] = stamp;
            let mut done = false;
            let mut qi = 0;
            while qi < queue.len() && !done && qi < 4000 {
                let x = queue[qi];
                qi += 1;
                let mut k = 0;
                while k < 3 && !done {
                    let y = allowed(x, k);
                    k += 1;
                    if y < 0 || seen[y as usize] == stamp { continue; }
                    let y = y as usize;
                    if mate[y] < 0 {
                        let (mut cur, mut partner) = (x, y);
                        loop {
                            let old = mate[cur];
                            mate[cur] = partner as i32;
                            mate[partner] = cur as i32;
                            if cur == root { break; }
                            partner = old as usize;
                            cur = outer_from[old as usize] as usize;
                        }
                        done = true;
                    } else {
                        seen[y] = stamp;
                        let w = mate[y] as usize;
                        if seen[w] != stamp { seen[w] = stamp; outer_from[y] = x as i32; queue.push(w); }
                    }
                }
            }
        }
        st.triangles = m.alive_faces;
        st.pairs = (0..n_f).filter(|&f| m.falive[f] && mate[f] > f as i32).count() as i32;
        st.leftover = st.triangles - 2 * st.pairs;
        mate
    }

    /// Valence-aware pairing. After pairing and subdividing, an original vertex v ends with valence k(v)-d(v)
    /// (interior) where k is its number of triangles and d the number of removed edges touching it; all other
    /// new vertices are regular. So the poles are decided entirely by WHICH edges the pairing removes. Simulated
    /// annealing over alternating cycles (a perfect pairing stays perfect) looks for d(v) close to k(v)-4
    /// (interior) or k(v)-2 (border).
    fn refine_matching(&self, mate: &mut Vec<i32>, st: &mut QuadStats) {
        let m = self.m;
        if self.prm.pole_weight <= 0.0 || m.alive_faces < 8 { return; }
        let n_f = m.num_face_slots() as usize;
        let n_v = m.num_vert_slots() as usize;
        let mut cost_c = vec![1e30f64; 3 * n_f];
        for c in 0..(n_f * 3) as i32 {
            if !m.falive[face_of(c) as usize] { continue; }
            let d = m.o[c as usize];
            if d < 0 || m.e[c as usize] != 0 { continue; }
            cost_c[c as usize] = if d > c { self.pair_cost(c) } else { 0.0 };
        }
        for c in 0..n_f * 3 {
            let o = m.o[c];
            if o >= 0 && (o as usize) < c && cost_c[c] < 1e29 { cost_c[c] = cost_c[o as usize]; }
        }
        let mut k = vec![0i32; n_v];
        let mut bnd = vec![false; n_v];
        for c in 0..(n_f * 3) as i32 {
            if !m.falive[face_of(c) as usize] { continue; }
            k[m.v[c as usize] as usize] += 1;
            if m.o[c as usize] < 0 {
                bnd[m.v[nxt(c) as usize] as usize] = true;
                bnd[m.v[prv(c) as usize] as usize] = true;
            }
        }
        let mut w = vec![1.0f64; n_v];
        for v in 0..n_v { if m.vtype[v] == V_FIXED && !bnd[v] { w[v] = 0.0; } }
        let mut r = Refiner {
            m, cost_c, k, w, bnd, d: vec![0; n_v], dd: vec![0; n_v], touched: Vec::new(), mate: std::mem::take(mate),
            path: [0; 16], best_path: [0; 16], best_len: 0, best_delta: 1e30, pole_weight: self.prm.pole_weight,
            cycle_pairs: self.prm.cycle_pairs,
        };
        for f in 0..n_f {
            if !m.falive[f] || r.mate[f] <= f as i32 { continue; }
            let c = r.shared_corner(f as i32, r.mate[f]);
            if c >= 0 { r.add_edge(c, 1); }
        }
        st.irregular_before = r.irregular();
        let mut rng: u64 = 0x9E3779B97F4A7C15;
        let mut rnd = || { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; rng };
        let matched: Vec<i32> = (0..n_f as i32).filter(|&f| m.falive[f as usize] && r.mate[f as usize] > f).collect();
        if matched.is_empty() { *mate = r.mate; return; }
        let moves = self.prm.pole_sweeps as i64 * matched.len() as i64;
        let (t0, t1) = (1.0f64, 0.02f64);
        for it in 0..moves {
            let t = t0 * (t1 / t0).powf(it as f64 / moves as f64);
            let mut a = matched[(rnd() % matched.len() as u64) as usize];
            if rnd() & 1 != 0 { a = r.mate[a as usize]; }
            if r.mate[a as usize] < 0 { continue; }
            r.path[0] = a;
            r.path[1] = r.mate[a as usize];
            r.best_delta = 1e30;
            r.best_len = 0;
            r.dfs(2);
            if r.best_len == 0 { continue; }
            let u01 = (rnd() >> 11) as f64 / 9007199254740992.0;
            if r.best_delta < 0.0 || u01 < (-r.best_delta / t).exp() {
                let len = r.best_len;
                let bp = r.best_path;
                let mut i = 0;
                while i < len { let c = r.shared_corner(bp[i], bp[i + 1]); r.add_edge(c, -1); i += 2; }
                let mut i = 1;
                while i <= len {
                    let (p, q) = (bp[i], bp[(i + 1) % len]);
                    r.mate[p as usize] = q;
                    r.mate[q as usize] = p;
                    let c = r.shared_corner(p, q);
                    r.add_edge(c, 1);
                    i += 2;
                }
            }
        }
        st.irregular_after = r.irregular();
        *mate = r.mate;
    }

    // ------------------------------------------------------------------ subdivision

    fn subdivide(&mut self, mate: &[i32]) {
        struct Poly { n: usize, v: [i32; 4], feat: [bool; 4], src: i32 }
        let m = self.m;
        let n_f = m.num_face_slots() as usize;
        let mut polys: Vec<Poly> = Vec::new();
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let g = mate[f];
            if g >= 0 && (g as usize) < f { continue; } // handled with its partner
            if g < 0 {
                let c = (3 * f) as i32;
                let (n1, p1) = (nxt(c), prv(c));
                let e = |x: i32| m.e[x as usize] != 0;
                polys.push(Poly { n: 3, v: [m.v[c as usize], m.v[n1 as usize], m.v[p1 as usize], -1], feat: [e(p1), e(c), e(n1), false], src: f as i32 });
            } else {
                let mut c = -1;
                for k in 0..3 {
                    let o = m.o[3 * f + k];
                    if o >= 0 && face_of(o) == g { c = (3 * f + k) as i32; break; }
                }
                let d = m.o[c as usize];
                let (n1, p1, n2, p2) = (nxt(c), prv(c), nxt(d), prv(d));
                let e = |x: i32| m.e[x as usize] != 0;
                polys.push(Poly { n: 4, v: [m.v[c as usize], m.v[n1 as usize], m.v[d as usize], m.v[p1 as usize]], feat: [e(p1), e(n2), e(p2), e(n1)], src: f as i32 });
            }
        }
        self.p = m.p.clone();
        self.vtype = m.vtype.clone();
        self.p.truncate(m.num_vert_slots() as usize);
        let mut mid: HashMap<u64, i32> = HashMap::with_capacity(polys.len() * 3);
        self.q.clear();
        self.quad_source.clear();
        for poly in &polys {
            let mut mids = [0i32; 4];
            let mut centre = Vec3::ZERO;
            for i in 0..poly.n {
                let (a, b) = (poly.v[i], poly.v[(i + 1) % poly.n]);
                let key = edge_key(a, b);
                mids[i] = match mid.get(&key) {
                    Some(&id) => id,
                    None => {
                        let id = self.p.len() as i32;
                        let pt = (self.p[a as usize] + self.p[b as usize]) * 0.5;
                        self.p.push(pt);
                        self.vtype.push(if poly.feat[i] { V_CREASE } else { V_FREE });
                        mid.insert(key, id);
                        id
                    }
                };
                centre += self.p[poly.v[i] as usize];
            }
            let cid = self.p.len() as i32;
            self.p.push(centre / poly.n as f64);
            self.vtype.push(V_FREE);
            for i in 0..poly.n {
                self.q.push([poly.v[i], mids[i], cid, mids[(i + poly.n - 1) % poly.n]]);
                self.quad_source.push(poly.src);
            }
        }
    }

    // ------------------------------------------------------------------ relaxation

    fn relax(&mut self) {
        let n_v = self.p.len();
        let n_q = self.q.len();
        let threads = self.prm.threads;
        // incident quads (CSR)
        let mut start = vec![0usize; n_v + 1];
        for q in &self.q { for k in 0..4 { start[q[k] as usize + 1] += 1; } }
        for v in 0..n_v { start[v + 1] += start[v]; }
        let mut incident = vec![0usize; start[n_v]];
        {
            let mut fill = start[..n_v].to_vec();
            for (qi, q) in self.q.iter().enumerate() {
                for k in 0..4 { incident[fill[q[k] as usize]] = qi * 4 + k; fill[q[k] as usize] += 1; }
            }
        }
        // feature neighbours of crease vertices (along quad edges that lie on a feature line)
        let mut fnb = vec![[-1i32; 2]; n_v];
        {
            let (p, vt, feat, l) = (&self.p, &self.vtype, self.feat, self.l);
            let on_feature = |a: i32, b: i32| {
                if vt[a as usize] == V_FREE || vt[b as usize] == V_FREE || feat.is_empty() { return false; }
                let h = feat.closest((p[a as usize] + p[b as usize]) * 0.5);
                h.d2 < 1e-6 * l * l
            };
            for q in &self.q {
                for k in 0..4 {
                    let (a, b) = (q[k], q[(k + 1) % 4]);
                    if !on_feature(a, b) { continue; }
                    for s in 0..2 {
                        let (u, w) = if s == 1 { (b, a) } else { (a, b) };
                        if vt[u as usize] != V_CREASE { continue; }
                        let f = &mut fnb[u as usize];
                        if f[0] == w || f[1] == w { continue; }
                        if f[0] < 0 { f[0] = w; } else if f[1] < 0 { f[1] = w; }
                    }
                }
            }
        }
        // field direction per quad (taken from the triangle it came from)
        let qdir: Vec<Vec3> = (0..n_q).map(|qi| {
            let f = self.quad_source[qi] as usize;
            axis_from_z(&self.frames, f, self.z[f])
        }).collect();

        let mut proposal: Vec<[Vec3; 4]> = vec![[Vec3::ZERO; 4]; n_q];
        let mut next_p = vec![Vec3::ZERO; n_v];
        let mut vnormal = vec![Vec3::ZERO; n_v];
        let use_field = self.prm.use_field;
        let field_align = self.prm.field_align;
        let relax_step = self.prm.relax_step;
        let project = self.prm.project;
        for it in 0..self.prm.relax_iterations {
            // 1. square proposals
            {
                let (p, qs, qdir) = (&self.p, &self.q, &qdir);
                par_fill(&mut proposal, threads, |qi| {
                    let q = qs[qi];
                    let cur = [p[q[0] as usize], p[q[1] as usize], p[q[2] as usize], p[q[3] as usize]];
                    let (p0, p1, p2, p3) = (cur[0], cur[1], cur[2], cur[3]);
                    let c = (p0 + p1 + p2 + p3) * 0.25;
                    let n = (p2 - p0).cross(p3 - p1).normalized();
                    if n.length2() < 0.5 { return cur; }
                    let mut u1 = p0 - c;
                    u1 = (u1 - n * n.dot(u1)).normalized();
                    if u1.length2() < 0.5 { return cur; }
                    let u2 = n.cross(u1);
                    let mut z = [Cplx::ZERO; 4];
                    for k in 0..4 { z[k] = Cplx::new((cur[k] - c).dot(u1), (cur[k] - c).dot(u2)); }
                    let i = Cplx::new(0.0, 1.0);
                    let mut w = (z[0] - i * z[1] - z[2] + i * z[3]) * 0.25;
                    if use_field && field_align > 0.0 && w.abs() > 1e-300 {
                        let mut d = qdir[qi];
                        d = (d - n * n.dot(d)).normalized();
                        if d.length2() > 0.5 {
                            let theta_d = d.dot(u2).atan2(d.dot(u1));
                            // square sides run at angle(w)+135deg; they should be a multiple of 90deg from theta_d
                            let target = theta_d + PI / 4.0;
                            let mut delta = (w.arg() - target) % (PI / 2.0);
                            if delta > PI / 4.0 { delta -= PI / 2.0; }
                            if delta < -PI / 4.0 { delta += PI / 2.0; }
                            w = Cplx::polar(w.abs(), w.arg() - field_align * delta);
                        }
                    }
                    let mut out = [Vec3::ZERO; 4];
                    let mut r = w;
                    for k in 0..4 { out[k] = c + u1 * r.re + u2 * r.im; r = r * i; }
                    out
                });
            }
            // 2. vertex normals (for the projection filter)
            for v in vnormal.iter_mut() { *v = Vec3::ZERO; }
            for q in &self.q {
                let n = (self.p[q[2] as usize] - self.p[q[0] as usize]).cross(self.p[q[3] as usize] - self.p[q[1] as usize]);
                for k in 0..4 { vnormal[q[k] as usize] += n; }
            }
            // 3. gather, move, snap
            {
                let (p, vt, prop, inc, st, fnb, vn) = (&self.p, &self.vtype, &proposal, &incident, &start, &fnb, &vnormal);
                let (reference, feat) = (self.reference, self.feat);
                par_fill(&mut next_p, threads, |v| {
                    let mut pos = p[v];
                    if vt[v] == V_FIXED || st[v] == st[v + 1] { return pos; }
                    if vt[v] == V_FREE {
                        let mut acc = Vec3::ZERO;
                        let mut cnt = 0;
                        for i in st[v]..st[v + 1] { let e = inc[i]; acc += prop[e / 4][e % 4]; cnt += 1; }
                        pos = pos + (acc / cnt as f64 - pos) * relax_step;
                        if project {
                            let nn = vn[v].normalized();
                            let hit = reference.closest(pos, Some(nn), 0.1);
                            if hit.tri >= 0 { pos = hit.p; }
                        }
                    } else {
                        let (a, b) = (fnb[v][0], fnb[v][1]);
                        if a >= 0 && b >= 0 {
                            pos = pos + ((p[a as usize] + p[b as usize]) * 0.5 - pos) * relax_step;
                            if project && !feat.is_empty() { pos = feat.closest(pos).p; }
                        }
                    }
                    pos
                });
            }
            std::mem::swap(&mut self.p, &mut next_p);
            if self.prm.progress.is_some() && it % 5 == 0 {
                self.report(80 + 15 * it / self.prm.relax_iterations.max(1), "Relaxing quads");
            }
        }
    }

    // ------------------------------------------------------------------ driver

    pub fn run(&mut self, out: &mut PolyMesh, st: &mut QuadStats) -> Result<(), String> {
        let t0 = Instant::now();
        self.frames = build_frames(self.m);
        self.z = solve_cross_field(self.m, &self.frames, &FieldInputs::default());
        st.field_seconds = t0.elapsed().as_secs_f64();
        self.report(75, "Pairing triangles");
        let t1 = Instant::now();
        let mut mate = self.match_triangles(st);
        self.refine_matching(&mut mate, st);
        st.match_seconds = t1.elapsed().as_secs_f64();
        let t2 = Instant::now();
        self.subdivide(&mate);
        self.relax();
        st.relax_seconds = t2.elapsed().as_secs_f64();
        st.quads = self.q.len() as i32;
        if self.q.is_empty() { return Err("no quads were produced".into()); }
        out.p = self.p.clone();
        out.sizes = vec![4; self.q.len()];
        out.idx = self.q.iter().flat_map(|q| q.iter().copied()).collect();
        out.features.clear();
        out.density.clear();
        Ok(())
    }

    /// Field and quads, for callers that want to inspect them.
    pub fn quads(&self) -> &[[i32; 4]] { &self.q }
}

/// State of the pole-annealing search (see `Quadifier::refine_matching`).
struct Refiner<'a> {
    m: &'a CMesh,
    cost_c: Vec<f64>,
    k: Vec<i32>,
    w: Vec<f64>,
    bnd: Vec<bool>,
    d: Vec<i32>,
    dd: Vec<i32>,
    touched: Vec<usize>,
    mate: Vec<i32>,
    path: [i32; 16],
    best_path: [i32; 16],
    best_len: usize,
    best_delta: f64,
    pole_weight: f64,
    cycle_pairs: i32,
}

impl<'a> Refiner<'a> {
    fn ideal(&self, v: usize) -> i32 { if self.bnd[v] { 2 } else { 4 } }
    fn err(&self, v: usize) -> i32 { self.k[v] - self.d[v] - self.ideal(v) }
    fn irregular(&self) -> i32 {
        (0..self.d.len()).filter(|&v| self.w[v] > 0.0 && self.k[v] > 0 && self.err(v) != 0).count() as i32
    }
    fn shared_corner(&self, f: i32, g: i32) -> i32 {
        for j in 0..3 {
            let o = self.m.o[(3 * f + j) as usize];
            if o >= 0 && face_of(o) == g { return 3 * f + j; }
        }
        -1
    }
    fn add_edge(&mut self, c: i32, sign: i32) {
        let (a, b) = (self.m.v[nxt(c) as usize] as usize, self.m.v[prv(c) as usize] as usize);
        self.d[a] += sign;
        self.d[b] += sign;
    }
    fn bump(&mut self, c: i32, sign: i32) {
        for v in [self.m.v[nxt(c) as usize] as usize, self.m.v[prv(c) as usize] as usize] {
            if self.dd[v] == 0 { self.touched.push(v); }
            self.dd[v] += sign;
        }
    }
    fn clear_touched(&mut self) {
        for &v in &self.touched { self.dd[v] = 0; }
    }
    /// Delta of toggling the alternating cycle path[0..len) (old pairs (0,1),(2,3).. new pairs (1,2),(3,4)..(len-1,0)).
    fn evaluate(&mut self, len: usize) -> f64 {
        let mut dc = 0.0;
        self.touched.clear();
        let mut i = 0;
        while i < len {
            let c = self.shared_corner(self.path[i], self.path[i + 1]);
            if c < 0 { self.clear_touched(); return 1e30; }
            dc -= self.cost_c[c as usize];
            self.bump(c, -1);
            i += 2;
        }
        let mut i = 1;
        while i <= len {
            let (a, b) = (self.path[i], self.path[(i + 1) % len]);
            let c = self.shared_corner(a, b);
            if c < 0 || self.cost_c[c as usize] > 1e29 { self.clear_touched(); return 1e30; }
            dc += self.cost_c[c as usize];
            self.bump(c, 1);
            i += 2;
        }
        let mut dv = 0.0;
        for &v in &self.touched {
            if self.dd[v] != 0 && self.w[v] > 0.0 {
                let e0 = self.err(v);
                let e1 = e0 - self.dd[v];
                dv += self.w[v] * ((e1 as f64) * (e1 as f64) - (e0 as f64) * (e0 as f64));
            }
        }
        self.clear_touched();
        dc + self.pole_weight * dv
    }
    fn neighbour_face(&self, f: i32, j: i32) -> i32 {
        let c = (3 * f + j) as usize;
        if self.m.o[c] < 0 || self.m.e[c] != 0 { -1 } else { face_of(self.m.o[c]) }
    }
    fn in_path(&self, f: i32, len: usize) -> bool { self.path[..len].contains(&f) }

    /// Depth-first search of alternating cycles through path[0]=a, path[1]=mate[a].
    fn dfs(&mut self, len: usize) {
        let a = self.path[0];
        let y = self.path[len - 1];
        if len >= 4 {
            // does y touch a through a free (unmatched) dual edge?
            for j in 0..3 {
                if self.neighbour_face(y, j) == a {
                    let dl = self.evaluate(len);
                    if dl < self.best_delta {
                        self.best_delta = dl;
                        self.best_len = len;
                        self.best_path[..len].copy_from_slice(&self.path[..len]);
                    }
                    break;
                }
            }
        }
        if (len / 2) as i32 >= self.cycle_pairs || len + 2 > 16 { return; }
        for j in 0..3 {
            let c = self.neighbour_face(y, j);
            if c < 0 || self.mate[c as usize] < 0 || self.in_path(c, len) || c == self.mate[y as usize] { continue; }
            let e = self.mate[c as usize];
            if e < 0 || self.in_path(e, len) { continue; }
            self.path[len] = c;
            self.path[len + 1] = e;
            self.dfs(len + 2);
        }
    }
}
