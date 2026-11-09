//! Seamless integer-grid parametrization (a compact mixed-integer quadrangulation).
//!
//! Input: a closed triangle mesh and its face cross field. Output: (u,v) at every triangle corner such that the
//! iso-lines u = integer, v = integer form the quad layout (one lattice cell per rho x rho).
//!
//!   1. Field matching: neighbouring faces agree on the 4 directions up to a rotation of k * 90 degrees.
//!      Vertices where the rotations around do not close are the singularities (poles).
//!   2. Gauge: one of the four directions per face is chosen by propagating along a spanning tree of the dual
//!      graph. Edges where the choice still jumps by k != 0 form the cut graph (it ends at the singularities);
//!      on a surface with handles a few extra cycles are added.
//!   3. Sheets: corners around a vertex that are not separated by cut edges share one (u,v) pair.
//!   4. Least squares: grad u = U / rho, grad v = V / rho on every face. Across a cut edge the two sides must
//!      agree up to (u,v)_B = Rot(k)(u,v)_A + t. The agreement is a stiff penalty; t is free per cut path.
//!   5. Integer rounding: the translations t and the (u,v) of the singular vertices must be integers. They are
//!      rounded gradually (the nearest first), solving again after each batch.
//!
//! Work areas: `Topology::build` (steps 1-3) · `Param::assemble` (step 4) · `Param::solve_and_round` (step 5).
//! The experimental seam-repair search of the C++ engine (FORGE_SEAMFIX, off by default) is not ported.
use crate::math::{Vec3, PI};
use crate::mesh::cmesh::{nxt, prv, CMesh};
use crate::stages::amg::{Amg, Csr};
use crate::stages::field::FaceFrames;
use crate::math::Cplx;
use crate::util::parallel::par_fill;
use std::collections::{HashMap, VecDeque};
use std::time::Instant;

pub type Progress = Box<dyn Fn(i32, &str) + Sync>;

#[derive(Default)]
pub struct ParamResult {
    /// (u,v) per face corner.
    pub uv: Vec<[[f64; 2]; 3]>,
    /// Chosen field directions per face.
    pub u: Vec<Vec3>,
    pub v: Vec<Vec3>,
    pub singular: Vec<i32>,
    pub cut_edges: i32,
    pub sheets: i32,
    pub genus_loops: i32,
    pub solves: i32,
    /// Mean |grad - target| * rho.
    pub face_error: f64,
    /// Max |seam residual|.
    pub seam_error: f64,
    /// Seams whose residual is above 0.25 of a cell (an integer defect at a cut junction).
    pub bad_seams: i32,
    pub seconds: f64,
    /// Real-valued solution, reused when rho changes.
    pub warm: Vec<f64>,
    pub warm_rho: f64,
    /// The density (size multiplier) was raised to this power (1 = full, 0 = ignored).
    pub density_power: f64,
    pub error: String,
}

pub struct ParamOptions {
    pub align_features: bool,
    pub density_power: f64,
    pub progress: Option<Progress>,
    pub time_limit: f64,
    pub threads: usize,
}

impl Default for ParamOptions {
    fn default() -> Self { ParamOptions { align_features: false, density_power: 1.0, progress: None, time_limit: 0.0, threads: 1 } }
}

fn mod4(a: i32) -> i32 { ((a % 4) + 4) % 4 }
fn env_set(name: &str) -> bool { std::env::var_os(name).is_some() }
fn env_f64(name: &str) -> Option<f64> { std::env::var(name).ok().and_then(|s| s.trim().parse().ok()) }
fn dbg() -> bool { env_set("FORGE_DBG") }

fn rot_m(k: i32) -> [[f64; 2]; 2] {
    let c = match k { 0 => 1.0, 2 => -1.0, _ => 0.0 };
    let s = match k { 1 => 1.0, 3 => -1.0, _ => 0.0 };
    [[c, -s], [s, c]]
}

#[derive(Clone, Copy)]
struct Seam { sa: usize, sb: usize, te: usize, k: i32 }

/// Union-find with path halving.
struct Dsu(Vec<usize>);
impl Dsu {
    fn new(n: usize) -> Self { Dsu((0..n).collect()) }
    fn find(&mut self, mut a: usize) -> usize {
        while self.0[a] != a { self.0[a] = self.0[self.0[a]]; a = self.0[a]; }
        a
    }
    fn union(&mut self, a: usize, b: usize) { let (ra, rb) = (self.find(a), self.find(b)); self.0[ra] = rb; }
}

// ------------------------------------------------------------------------------------------------ topology

struct Topology {
    n_e: usize,
    ec0: Vec<i32>,
    edge_of_corner: Vec<i32>,
    theta: Vec<f64>,
    singular: Vec<i32>,
    is_sing: Vec<bool>,
    gauge: Vec<i32>,
    dual_roots: Vec<i32>,
    cut: Vec<bool>,
    jump: Vec<i32>,
    cut_index: Vec<i32>,
    n_cut: usize,
    seg_junction: Vec<Vec<i32>>,
    f_left: Vec<bool>,
    sheet_of: Vec<i32>,
    n_s: usize,
    genus_loops: i32,
    cut_edges: i32,
}

impl Topology {
    fn end_a(&self, m: &CMesh, e: usize) -> usize { m.v[nxt(self.ec0[e]) as usize] as usize }
    fn end_b(&self, m: &CMesh, e: usize) -> usize { m.v[prv(self.ec0[e]) as usize] as usize }

    fn build(m: &CMesh, fr: &FaceFrames, z: &[Cplx]) -> Result<Topology, String> {
        let n_f = m.num_face_slots() as usize;
        let n_vs = m.num_vert_slots() as usize;
        let n_c = 3 * n_f;
        // closed surface only
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            for k in 0..3 { if m.o[3 * f + k] < 0 { return Err("the surface has a border".into()); } }
        }
        // edges
        let mut edge_of_corner = vec![-1i32; n_c];
        let mut ec0: Vec<i32> = Vec::new();
        for c in 0..n_c {
            if !m.falive[c / 3] { continue; }
            let d = m.o[c];
            if (d as i64) < c as i64 { continue; } // canonical = smaller corner
            edge_of_corner[c] = ec0.len() as i32;
            edge_of_corner[d as usize] = ec0.len() as i32;
            ec0.push(c as i32);
        }
        let n_e = ec0.len();
        let mut t = Topology {
            n_e, ec0, edge_of_corner, theta: vec![0.0; n_f], singular: vec![], is_sing: vec![false; n_vs], gauge: vec![-1; n_f],
            dual_roots: vec![], cut: vec![false; n_e], jump: vec![0; n_e], cut_index: vec![-1; n_e], n_cut: 0, seg_junction: vec![],
            f_left: vec![true; n_e], sheet_of: vec![-1; n_c], n_s: 0, genus_loops: 0, cut_edges: 0,
        };
        // matching between faces
        for f in 0..n_f { if m.falive[f] { t.theta[f] = z[f].arg() / 4.0; } }
        let half_pi = PI / 2.0;
        let transport_angle = |c: usize| -> f64 { // Delta for crossing the edge opposite corner c
            let (f, k, d) = (c / 3, c % 3, m.o[c] as usize);
            let (g, kk) = (d / 3, d % 3);
            fr.alpha[g][kk] + PI - fr.alpha[f][k]
        };
        let match_index = |c: usize, theta: &[f64]| -> i32 { // r such that theta_f + Delta = theta_g + r*90deg
            let (f, g) = (c / 3, m.o[c] as usize / 3);
            mod4(((theta[f] + transport_angle(c) - theta[g]) / half_pi).round() as i32)
        };
        let r_edge: Vec<i32> = (0..n_e).map(|e| match_index(t.ec0[e] as usize, &t.theta)).collect();
        // r of the edge seen from corner c's face
        let r_at = |t: &Topology, c: i32, e: usize| -> i32 { if c == t.ec0[e] { r_edge[e] } else { mod4(-r_edge[e]) } };

        // singular vertices
        let mut scratch: Vec<i32> = Vec::new();
        for v in 0..n_vs {
            if !m.valive[v] { continue; }
            m.orbit(v as i32, &mut scratch);
            let mut sum = 0;
            for &c in &scratch {
                let ce = prv(c); // edge shared with the next face around v
                let e = t.edge_of_corner[ce as usize] as usize;
                sum += r_at(&t, ce, e);
            }
            if mod4(sum) != 0 { t.is_sing[v] = true; t.singular.push(v as i32); }
        }

        // spanning trees: primal (vertices) and dual (faces)
        let mut vadj: Vec<Vec<(usize, usize)>> = vec![Vec::new(); n_vs];
        for e in 0..n_e {
            let (a, b) = (t.end_a(m, e), t.end_b(m, e));
            vadj[a].push((b, e));
            vadj[b].push((a, e));
        }
        let mut in_tp = vec![false; n_e];
        let mut pedge = vec![-1i32; n_vs];
        let mut pvert = vec![-1i32; n_vs];
        {
            let mut seen = vec![false; n_vs];
            let mut q: VecDeque<usize> = VecDeque::new();
            let mut grow = |root: usize, seen: &mut Vec<bool>, in_tp: &mut Vec<bool>, pedge: &mut Vec<i32>, pvert: &mut Vec<i32>| {
                if seen[root] { return; }
                q.push_back(root);
                seen[root] = true;
                while let Some(v) = q.pop_front() {
                    for &(w, e) in &vadj[v] {
                        if !seen[w] { seen[w] = true; pedge[w] = e as i32; pvert[w] = v as i32; in_tp[e] = true; q.push_back(w); }
                    }
                }
            };
            for &v in &t.singular { grow(v as usize, &mut seen, &mut in_tp, &mut pedge, &mut pvert); }
            for v in 0..n_vs { if m.valive[v] { grow(v, &mut seen, &mut in_tp, &mut pedge, &mut pvert); } }
        }
        let mut in_td = vec![false; n_e];
        for root in 0..n_f {
            if !m.falive[root] || t.gauge[root] >= 0 { continue; }
            t.dual_roots.push(root as i32);
            let mut q: VecDeque<usize> = VecDeque::new();
            q.push_back(root);
            t.gauge[root] = 0;
            while let Some(f) = q.pop_front() {
                for k in 0..3 {
                    let c = (3 * f + k) as i32;
                    let e = t.edge_of_corner[c as usize] as usize;
                    if in_tp[e] { continue; }
                    let g = m.o[c as usize] as usize / 3;
                    if t.gauge[g] >= 0 { continue; }
                    // theta_f~ + Delta = theta_g~ + k*90  with theta~ = theta + gauge*90
                    let r = r_at(&t, c, e);
                    t.gauge[g] = mod4(t.gauge[f] + r);
                    in_td[e] = true;
                    q.push_back(g);
                }
            }
        }
        // cut edges: jumps after the gauge, plus the generators of handles
        for e in 0..n_e {
            let c = t.ec0[e] as usize;
            let (f, g) = (c / 3, m.o[c] as usize / 3);
            t.jump[e] = mod4(r_edge[e] + t.gauge[f] - t.gauge[g]);
            if t.jump[e] != 0 { t.cut[e] = true; }
        }
        {
            // the cut graph is topological: every singular vertex is connected to the root, and handles are opened
            let add_path = |cut: &mut Vec<bool>, mut v: usize| {
                while pedge[v] >= 0 { cut[pedge[v] as usize] = true; v = pvert[v] as usize; }
            };
            for &v in &t.singular { add_path(&mut t.cut, v as usize); }
            for e in 0..n_e {
                if !in_tp[e] && !in_td[e] {
                    t.cut[e] = true;
                    let (a, b) = (t.end_a(m, e), t.end_b(m, e));
                    add_path(&mut t.cut, a);
                    add_path(&mut t.cut, b);
                    t.genus_loops += 1;
                }
            }
        }
        // dangling cut edges (a cut vertex of degree 1 that is not singular) carry no information: drop them
        {
            let mut deg = vec![0i32; n_vs];
            for e in 0..n_e { if t.cut[e] { deg[t.end_a(m, e)] += 1; deg[t.end_b(m, e)] += 1; } }
            let mut changed = true;
            while changed {
                changed = false;
                for e in 0..n_e {
                    if !t.cut[e] || t.jump[e] != 0 { continue; }
                    let (a, b) = (t.end_a(m, e), t.end_b(m, e));
                    if (deg[a] == 1 && !t.is_sing[a]) || (deg[b] == 1 && !t.is_sing[b]) {
                        t.cut[e] = false;
                        deg[a] -= 1;
                        deg[b] -= 1;
                        changed = true;
                    }
                }
            }
        }
        // translations are shared along a cut path (nothing changes at a vertex of cut degree two)
        {
            let mut cdeg = vec![0i32; n_vs];
            let mut cut_edges_n = 0;
            for e in 0..n_e { if t.cut[e] { cdeg[t.end_a(m, e)] += 1; cdeg[t.end_b(m, e)] += 1; cut_edges_n += 1; } }
            t.cut_edges = cut_edges_n;
            let mut sp = Dsu::new(n_e);
            let mut first_cut = vec![-1i32; n_vs];
            let through = |v: usize, is_sing: &[bool]| cdeg[v] == 2 && !is_sing[v];
            for e in 0..n_e {
                if !t.cut[e] { continue; }
                for v in [t.end_a(m, e), t.end_b(m, e)] {
                    if !through(v, &t.is_sing) { continue; }
                    if first_cut[v] < 0 { first_cut[v] = e as i32; } else { let fc = first_cut[v] as usize; let r = sp.find(fc); let re = sp.find(e); sp.0[re] = r; }
                }
            }
            let mut seg_id: HashMap<usize, usize> = HashMap::new();
            let mut n_cut = 0usize;
            for e in 0..n_e {
                if !t.cut[e] { continue; }
                let r = sp.find(e);
                let id = *seg_id.entry(r).or_insert_with(|| { n_cut += 1; t.seg_junction.push(Vec::new()); n_cut - 1 });
                t.cut_index[e] = id as i32;
                for v in [t.end_a(m, e), t.end_b(m, e)] {
                    if !through(v, &t.is_sing) { t.seg_junction[id].push(v as i32); }
                }
            }
            t.n_cut = n_cut;
            let mut thr = vec![[-1i32; 2]; n_vs];
            for e in 0..n_e {
                if !t.cut[e] { continue; }
                for v in [t.end_a(m, e), t.end_b(m, e)] {
                    if through(v, &t.is_sing) { if thr[v][0] < 0 { thr[v][0] = e as i32; } else { thr[v][1] = e as i32; } }
                }
            }
            let mut done = vec![false; n_e];
            // move on from e through vertex w
            let walk = |t: &mut Topology, done: &mut Vec<bool>, mut e: usize, mut w: usize, forward: bool| {
                loop {
                    if !through(w, &t.is_sing) { break; }
                    let e2 = if thr[w][0] == e as i32 { thr[w][1] } else { thr[w][0] };
                    if e2 < 0 || done[e2 as usize] { break; }
                    let e2 = e2 as usize;
                    done[e2] = true;
                    let (ea, eb) = (t.end_a(m, e2), t.end_b(m, e2));
                    let a_to_b = if forward { w == ea } else { w == eb };
                    t.f_left[e2] = a_to_b;
                    w = if w == ea { eb } else { ea };
                    e = e2;
                }
                let _ = e;
            };
            for e in 0..n_e {
                if !t.cut[e] || done[e] { continue; }
                done[e] = true;
                t.f_left[e] = true;
                let (a, b) = (t.end_a(m, e), t.end_b(m, e));
                walk(&mut t, &mut done, e, b, true);
                walk(&mut t, &mut done, e, a, false);
            }
        }
        // sheets
        let mut par = Dsu::new(n_c);
        for e in 0..n_e {
            if t.cut[e] { continue; }
            let c0 = t.ec0[e];
            let d0 = m.o[c0 as usize];
            par.union(nxt(c0) as usize, prv(d0) as usize);
            par.union(prv(c0) as usize, nxt(d0) as usize);
        }
        let mut id: HashMap<usize, i32> = HashMap::new();
        let mut n_s = 0i32;
        for c in 0..n_c {
            if !m.falive[c / 3] { continue; }
            let r = par.find(c);
            let s = *id.entry(r).or_insert_with(|| { n_s += 1; n_s - 1 });
            t.sheet_of[c] = s;
        }
        t.n_s = n_s as usize;
        Ok(t)
    }
}

// ------------------------------------------------------------------------------------------------ system

pub struct IntegerParam<'a> {
    m: &'a CMesh,
    fr: &'a FaceFrames,
    z: &'a [Cplx],
    prm: ParamOptions,
}

/// The least-squares system and the state of the rounding.
struct Param<'a> {
    prm: &'a ParamOptions,
    k: Csr,
    amg: Amg,
    use_amg: bool,
    diag: Vec<f64>,
    n_var: usize,
    trip: Vec<[f64; 3]>,
    trip_s: Vec<[f64; 3]>,
    trip_f: Vec<[f64; 3]>,
    rhs: Vec<f64>,
    x: Vec<f64>,
    fixed: Vec<bool>,
    solves: i32,
}

const WF: f64 = 300.0;

impl<'a> Param<'a> {
    fn build_k(&mut self, wt: f64) {
        let mut all = self.trip.clone();
        all.reserve(self.trip_s.len() + self.trip_f.len());
        for t in &self.trip_s { all.push([t[0], t[1], wt * t[2]]); }
        for t in &self.trip_f { all.push([t[0], t[1], WF * t[2]]); }
        self.k.build(self.n_var, &mut all);
        for r in 0..self.n_var {
            for kk in self.k.start[r] as usize..self.k.start[r + 1] as usize {
                if self.k.col[kk] as usize == r { self.diag[r] = self.k.val[kk].max(1e-12); }
            }
        }
        if self.use_amg {
            let mut kr = self.k.clone();
            for r in 0..self.n_var {
                for kk in kr.start[r] as usize..kr.start[r + 1] as usize {
                    if kr.col[kk] as usize == r { kr.val[kk] += 1e-5 * self.diag[r]; }
                }
            }
            let mut gt = self.trip.clone();
            for t in &self.trip_f { gt.push([t[0], t[1], WF * t[2]]); }
            let mut g = Csr::default();
            g.build(self.n_var, &mut gt);
            for r in 0..self.n_var {
                let mut dg = 0.0;
                for kk in g.start[r] as usize..g.start[r + 1] as usize { if g.col[kk] as usize == r { dg = g.val[kk]; } }
                for kk in g.start[r] as usize..g.start[r + 1] as usize { if g.col[kk] as usize == r { g.val[kk] += 1e-5 * dg.max(1e-12); } }
            }
            self.amg.build(&kr, &g, 2, 12, 300);
            if dbg() { eprintln!("param: amg levels {} coarse {} (n {})", self.amg.num_levels(), self.amg.coarse_rows(), self.n_var); }
        }
    }

    /// Conjugate gradient on the free variables (fixed ones keep their value).
    fn do_solve(&mut self, max_iter: usize, tol: f64) -> usize {
        self.solves += 1;
        let n = self.x.len();
        let threads = if !self.use_amg && self.prm.threads > 1 && n >= 40000 { self.prm.threads.min(1 + n / 20000) } else { 1 };
        let (k, fixed, diag, b) = (&self.k, &self.fixed, &self.diag, &self.rhs);
        let matvec = |v: &[f64], out: &mut Vec<f64>| {
            par_fill(out, threads, |i| {
                let mut s = 0.0;
                for kk in k.start[i] as usize..k.start[i + 1] as usize { s += k.val[kk] * v[k.col[kk] as usize]; }
                s
            });
        };
        let x = &mut self.x;
        let mut r = vec![0.0; n];
        let mut p = vec![0.0; n];
        let mut ap = vec![0.0; n];
        let mut zv = vec![0.0; n];
        let mut rm = vec![0.0; n];
        let amg = &self.amg;
        let use_amg = self.use_amg;
        let precond = |r: &[f64], zv: &mut Vec<f64>, rm: &mut Vec<f64>| {
            if !use_amg {
                zv.resize(n, 0.0);
                for i in 0..n { zv[i] = if fixed[i] { 0.0 } else { r[i] / diag[i] }; }
                return;
            }
            for i in 0..n { rm[i] = if fixed[i] { 0.0 } else { r[i] }; }
            amg.apply(rm, zv);
            for i in 0..n { if fixed[i] { zv[i] = 0.0; } }
        };
        matvec(x, &mut ap);
        let mut bnorm = 0.0;
        for i in 0..n {
            if fixed[i] { r[i] = 0.0; continue; }
            r[i] = b[i] - ap[i];
            bnorm += b[i] * b[i];
        }
        let mut rz = 0.0;
        precond(&r, &mut zv, &mut rm);
        for i in 0..n { p[i] = zv[i]; rz += r[i] * zv[i]; }
        if rz < 1e-300 { return 0; }
        let mut it = 0;
        while it < max_iter {
            matvec(&p, &mut ap);
            let mut pap = 0.0;
            for i in 0..n { if fixed[i] { ap[i] = 0.0; } pap += p[i] * ap[i]; }
            if pap.abs() < 1e-300 { break; }
            let alpha = rz / pap;
            let mut rn = 0.0;
            for i in 0..n {
                if fixed[i] { continue; }
                x[i] += alpha * p[i];
                r[i] -= alpha * ap[i];
                rn += r[i] * r[i];
            }
            if rn <= tol * tol * bnorm.max(1.0) { it += 1; break; }
            precond(&r, &mut zv, &mut rm);
            let mut rz_new = 0.0;
            for i in 0..n { rz_new += r[i] * zv[i]; }
            let beta = rz_new / rz;
            rz = rz_new;
            for i in 0..n { p[i] = if fixed[i] { 0.0 } else { zv[i] + beta * p[i] }; }
            it += 1;
        }
        it
    }

    /// Rounds the given variables to integers, nearest first, solving again after each batch.
    fn round_set(&mut self, mut vars: Vec<usize>) {
        vars.sort_unstable();
        vars.dedup();
        let mut open: Vec<usize> = vars.into_iter().filter(|&v| !self.fixed[v]).collect();
        while !open.is_empty() {
            let mut dist: Vec<(f64, usize)> = open.iter().map(|&v| ((self.x[v] - self.x[v].round()).abs(), v)).collect();
            dist.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let take = (open.len() / 3).max(1);
            let mut cnt = 0;
            while cnt < dist.len() && (cnt < take || dist[cnt].0 < 0.02) { cnt += 1; }
            for i in 0..cnt { let v = dist[i].1; self.x[v] = self.x[v].round(); self.fixed[v] = true; }
            open = dist[cnt..].iter().map(|d| d.1).collect();
            self.do_solve(300, 1e-3);
        }
    }
}

impl<'a> IntegerParam<'a> {
    pub fn new(m: &'a CMesh, fr: &'a FaceFrames, z: &'a [Cplx], prm: ParamOptions) -> Self { IntegerParam { m, fr, z, prm } }

    fn lap(&self, t0: &Instant, what: &str, solves: i32) {
        if dbg() { eprintln!("param: {:<12} {:.2}s (solves {})", what, t0.elapsed().as_secs_f64(), solves); }
        if let Some(p) = &self.prm.progress {
            let table: [(&str, i32, &str); 8] = [
                ("topology", 44, "Preparing the quad grid"), ("assembled", 46, "Solving the quad grid"), ("real solve", 56, "Placing the singular points"),
                ("singular", 61, "Fixing the grid seams"), ("stiff", 65, "Fixing the grid seams"), ("feature lines", 71, "Aligning to hard edges"),
                ("rounded", 75, "Finishing the grid"), ("final", 77, "Finishing the grid"),
            ];
            for (name, pct, msg) in table { if name == what { p(pct, msg); } }
        }
    }

    pub fn run(&self, rho: f64, out: &mut ParamResult) -> bool {
        out.genus_loops = 0;
        out.solves = 0;
        out.seam_error = 0.0;
        out.density_power = self.prm.density_power;
        out.singular.clear();
        let t0 = Instant::now();
        let time_up = || self.prm.time_limit > 0.0 && t0.elapsed().as_secs_f64() > self.prm.time_limit;
        let m = self.m;
        let fr = self.fr;
        let n_f = m.num_face_slots() as usize;
        let half_pi = PI / 2.0;

        let topo = match Topology::build(m, fr, self.z) {
            Ok(t) => t,
            Err(e) => { out.error = e; return false; }
        };
        out.singular = topo.singular.clone();
        out.cut_edges = topo.cut_edges;
        out.genus_loops = topo.genus_loops;
        out.sheets = topo.n_s as i32;
        let (n_s, n_cut, n_e) = (topo.n_s, topo.n_cut, topo.n_e);
        let sheet = |c: i32| topo.sheet_of[c as usize] as usize;
        self.lap(&t0, "topology", 0);

        // ---- assemble the system ----
        let n_var = 2 * (n_s + n_cut);
        let mut trip: Vec<[f64; 3]> = Vec::with_capacity(n_f * 18 + n_cut * 64);
        let mut rhs = vec![0.0f64; n_var];
        let mut target = vec![[Vec3::ZERO; 2]; n_f];
        let mut face_scale = vec![1.0f64; n_f];
        out.u = vec![Vec3::ZERO; n_f];
        out.v = vec![Vec3::ZERO; n_f];
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let th = topo.theta[f] + topo.gauge[f] as f64 * half_pi;
            let u = fr.e1[f] * th.cos() + fr.e2[f] * th.sin();
            let vd = fr.n[f].cross(u);
            let mut sc = 1.0;
            if !m.vdens.is_empty() {
                sc = (m.vdens[m.v[3 * f] as usize] as f64 + m.vdens[m.v[3 * f + 1] as usize] as f64 + m.vdens[m.v[3 * f + 2] as usize] as f64) / 3.0;
            }
            if self.prm.density_power != 1.0 { sc = sc.powf(self.prm.density_power); }
            face_scale[f] = sc;
            target[f] = [u / (rho * sc), vd / (rho * sc)];
            out.u[f] = u;
            out.v[f] = vd;
            let (p0, p1, p2) = (m.p[m.v[3 * f] as usize], m.p[m.v[3 * f + 1] as usize], m.p[m.v[3 * f + 2] as usize]);
            let nn = (p1 - p0).cross(p2 - p0);
            let a2 = nn.length(); // twice the area
            if !(a2 > 1e-12) { continue; } // a zero-area face carries no information (and would give NaN)
            let area = 0.5 * a2;
            let n = nn / a2;
            let g = [n.cross(p2 - p1) / a2, n.cross(p0 - p2) / a2, n.cross(p1 - p0) / a2];
            for a in 0..3 {
                let sa = sheet((3 * f + a) as i32);
                for b in 0..3 {
                    let sb = sheet((3 * f + b) as i32);
                    let q = area * g[a].dot(g[b]);
                    trip.push([(2 * sa) as f64, (2 * sb) as f64, q]);
                    trip.push([(2 * sa + 1) as f64, (2 * sb + 1) as f64, q]);
                }
                rhs[2 * sa] += area * g[a].dot(target[f][0]);
                rhs[2 * sa + 1] += area * g[a].dot(target[f][1]);
            }
        }
        // seams: x_B - Rot(k) x_A - t = 0 at both end points of every cut edge
        let w = env_f64("PW").unwrap_or(30.0);
        let mut seams: Vec<Seam> = Vec::new();
        for e in 0..n_e {
            if !topo.cut[e] { continue; }
            let c0 = topo.ec0[e];
            let d0 = m.o[c0 as usize];
            let te = n_s + topo.cut_index[e] as usize;
            let (fa, ga) = (sheet(nxt(c0)), sheet(prv(d0))); // end A: f side, g side
            let (fb, gb) = (sheet(prv(c0)), sheet(nxt(d0))); // end B
            if topo.f_left[e] {
                seams.push(Seam { sa: fa, sb: ga, te, k: topo.jump[e] });
                seams.push(Seam { sa: fb, sb: gb, te, k: topo.jump[e] });
            } else {
                let k = mod4(-topo.jump[e]);
                seams.push(Seam { sa: ga, sb: fa, te, k });
                seams.push(Seam { sa: gb, sb: fb, te, k });
            }
        }
        let mut trip_s: Vec<[f64; 3]> = Vec::new();
        for s in &seams {
            // residual r = I x_B - Rot x_A - I t ; add W * r^T r
            let rm = rot_m(s.k);
            let terms: [(usize, [[f64; 2]; 2]); 3] = [
                (s.sb, [[1.0, 0.0], [0.0, 1.0]]),
                (s.sa, [[-rm[0][0], -rm[0][1]], [-rm[1][0], -rm[1][1]]]),
                (s.te, [[-1.0, 0.0], [0.0, -1.0]]),
            ];
            for a in &terms {
                for b in &terms {
                    for i in 0..2 {
                        for j in 0..2 {
                            let mut v = 0.0;
                            for r in 0..2 { v += a.1[r][i] * b.1[r][j]; }
                            if v != 0.0 { trip_s.push([(2 * a.0 + i) as f64, (2 * b.0 + j) as f64, v]); }
                        }
                    }
                }
            }
        }
        // feature edges run along one of the two lattice directions: the other coordinate is constant along them
        let mut trip_f: Vec<[f64; 3]> = Vec::new();
        let mut fdsu = Dsu::new(n_var);
        let mut feat_vars: Vec<usize> = Vec::new();
        if self.prm.align_features {
            for f in 0..n_f {
                if !m.falive[f] { continue; }
                for k in 0..3 {
                    let c = (3 * f + k) as i32;
                    if m.e[c as usize] == 0 { continue; }
                    let (ka, kb) = (nxt(c), prv(c));
                    let (sa, sb) = (sheet(ka), sheet(kb));
                    let d = m.p[m.v[kb as usize] as usize] - m.p[m.v[ka as usize] as usize];
                    let coord = if d.dot(out.u[f]).abs() >= d.dot(out.v[f]).abs() { 1 } else { 0 };
                    let (i, j) = (2 * sa + coord, 2 * sb + coord);
                    if i == j { continue; }
                    let (fi, fj) = (i as f64, j as f64);
                    trip_f.push([fi, fi, 1.0]);
                    trip_f.push([fj, fj, 1.0]);
                    trip_f.push([fi, fj, -1.0]);
                    trip_f.push([fj, fi, -1.0]);
                    fdsu.union(i, j);
                    feat_vars.push(i);
                    feat_vars.push(j);
                }
            }
        }
        let mut ps = Param {
            prm: &self.prm, k: Csr::default(), amg: Amg::new(), use_amg: env_set("FORGE_AMG"), diag: vec![1.0; n_var], n_var,
            trip, trip_s, trip_f, rhs, x: vec![0.0; n_var], fixed: vec![false; n_var], solves: 0,
        };
        ps.build_k(w);
        out.uv = vec![[[0.0; 2]; 3]; n_f];
        self.lap(&t0, "assembled", 0);

        // ---- solve and round ----
        // pin the global translation of every connected component
        for &rf in &topo.dual_roots {
            let pin = sheet(3 * rf);
            ps.fixed[2 * pin] = true;
            ps.fixed[2 * pin + 1] = true;
            ps.x[2 * pin] = 0.0;
            ps.x[2 * pin + 1] = 0.0;
        }
        if out.warm.len() == ps.x.len() && out.warm_rho > 0.0 {
            for i in 0..ps.x.len() { if !ps.fixed[i] { ps.x[i] = out.warm[i] * (out.warm_rho / rho); } }
        }
        let it = ps.do_solve(6000, 1e-5);
        if dbg() {
            self.diagnose(&topo, &seams, &ps.x, &target, &face_scale, rho, out);
            eprintln!("param: real solve iters {} faceErr {:.4} seamErr {:.3e}", it, out.face_error, out.seam_error);
        }
        self.lap(&t0, "real solve", ps.solves);
        if time_up() { out.error = "time limit".into(); return false; }
        out.warm = ps.x.clone();
        out.warm_rho = rho;

        // 1. the singular vertices sit on lattice nodes; the translations around them follow
        let mut sv: Vec<usize> = Vec::new();
        for &v in &topo.singular {
            let s = sheet(m.vcorner[v as usize]);
            sv.push(2 * s);
            sv.push(2 * s + 1);
        }
        ps.round_set(sv);
        // paths that end at a singular vertex have a translation fixed by the lattice node there
        for sm in &seams {
            if sm.sa == sm.sb && ps.fixed[2 * sm.sa] && !ps.fixed[2 * sm.te] {
                let rm = rot_m(sm.k);
                ps.x[2 * sm.te] = (ps.x[2 * sm.sb] - (rm[0][0] * ps.x[2 * sm.sa] + rm[0][1] * ps.x[2 * sm.sa + 1])).round();
                ps.x[2 * sm.te + 1] = (ps.x[2 * sm.sb + 1] - (rm[1][0] * ps.x[2 * sm.sa] + rm[1][1] * ps.x[2 * sm.sa + 1])).round();
                ps.fixed[2 * sm.te] = true;
                ps.fixed[2 * sm.te + 1] = true;
            }
        }
        self.lap(&t0, "singular", ps.solves);
        if time_up() { out.error = "time limit".into(); return false; }
        ps.build_k(1000.0);
        ps.do_solve(3000, 1e-6);
        self.lap(&t0, "stiff", ps.solves);
        if time_up() { out.error = "time limit".into(); return false; }
        // feature lines: every group of constrained coordinates takes one integer value
        if !feat_vars.is_empty() {
            feat_vars.sort_unstable();
            feat_vars.dedup();
            let mut gid: HashMap<usize, usize> = HashMap::new();
            let mut groups: Vec<Vec<usize>> = Vec::new();
            for &v in &feat_vars {
                let r = fdsu.find(v);
                let id = *gid.entry(r).or_insert_with(|| { groups.push(Vec::new()); groups.len() - 1 });
                groups[id].push(v);
            }
            let mut open_g: Vec<usize> = (0..groups.len()).collect();
            let mut rounds = 0;
            while !open_g.is_empty() && rounds < 40 {
                rounds += 1;
                let mut val = vec![0.0f64; groups.len()];
                let mut dist: Vec<(f64, usize)> = Vec::new();
                for &g in &open_g {
                    let (mut sum, mut n, mut have_fixed, mut fv) = (0.0, 0, false, 0.0);
                    for &v in &groups[g] {
                        if ps.fixed[v] { have_fixed = true; fv = ps.x[v]; }
                        sum += ps.x[v];
                        n += 1;
                    }
                    val[g] = if have_fixed { fv } else { sum / n as f64 };
                    dist.push((if have_fixed { 0.0 } else { (val[g] - val[g].round()).abs() }, g));
                }
                dist.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
                let take = (open_g.len() / 4).max(1);
                let mut cnt = 0;
                while cnt < dist.len() && (cnt < take || dist[cnt].0 < 0.02) { cnt += 1; }
                for i in 0..cnt {
                    let g = dist[i].1;
                    let r = val[g].round();
                    for &v in &groups[g] { ps.x[v] = r; ps.fixed[v] = true; }
                }
                open_g = dist[cnt..].iter().map(|d| d.1).collect();
                ps.do_solve(600, 1e-4);
            }
            self.lap(&t0, "feature lines", ps.solves);
            if time_up() { out.error = "time limit".into(); return false; }
        }
        // translations: one batch never holds two paths that meet at a junction, because the last path of a
        // junction is fixed by the others (the loop around the junction must close)
        let mut open: Vec<usize> = (0..n_cut).filter(|&sg| !ps.fixed[2 * (n_s + sg)]).collect();
        let mut touched_at = vec![-1i32; m.num_vert_slots() as usize];
        let mut stamp = 0;
        let r_it = env_f64("FORGE_RIT").map_or(600, |v| v as usize);
        let r_tol = env_f64("FORGE_RTOL").unwrap_or(1e-4);
        while !open.is_empty() {
            let mut dist: Vec<(f64, usize)> = open.iter().map(|&sg| {
                let a = 2 * (n_s + sg);
                ((ps.x[a] - ps.x[a].round()).abs().max((ps.x[a + 1] - ps.x[a + 1].round()).abs()), sg)
            }).collect();
            dist.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            stamp += 1;
            let mut rest: Vec<usize> = Vec::new();
            for &(d, sg) in &dist {
                // a path whose value is already (nearly) integer is determined by the others: take it
                let mut ok = d < 0.06;
                if !ok { // a free choice: one per junction in each batch, the others follow from it
                    ok = true;
                    for &v in &topo.seg_junction[sg] { if touched_at[v as usize] == stamp { ok = false; break; } }
                    if ok { for &v in &topo.seg_junction[sg] { touched_at[v as usize] = stamp; } }
                }
                if !ok { rest.push(sg); continue; }
                let a = 2 * (n_s + sg);
                ps.x[a] = ps.x[a].round();
                ps.x[a + 1] = ps.x[a + 1].round();
                ps.fixed[a] = true;
                ps.fixed[a + 1] = true;
            }
            open = rest;
            ps.do_solve(r_it, r_tol);
        }
        self.lap(&t0, "rounded", ps.solves);
        ps.build_k(1000.0);
        ps.do_solve(3000, 1e-6);
        self.lap(&t0, "final", ps.solves);

        // ---- results and diagnostics ----
        self.diagnose(&topo, &seams, &ps.x, &target, &face_scale, rho, out);
        out.solves = ps.solves;
        out.seconds = t0.elapsed().as_secs_f64();
        if dbg() { eprintln!("param: faceErr {:.4} seamErr {:.3e} badSeams {}", out.face_error, out.seam_error, out.bad_seams); }
        true
    }

    /// Writes (u,v) per corner and the error measures.
    fn diagnose(&self, topo: &Topology, seams: &[Seam], x: &[f64], target: &[[Vec3; 2]], face_scale: &[f64], rho: f64, out: &mut ParamResult) {
        let m = self.m;
        let n_f = m.num_face_slots() as usize;
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            for k in 0..3 {
                let s = topo.sheet_of[3 * f + k] as usize;
                out.uv[f][k] = [x[2 * s], x[2 * s + 1]];
            }
        }
        let (mut fe, mut fa) = (0.0, 0.0);
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let (p0, p1, p2) = (m.p[m.v[3 * f] as usize], m.p[m.v[3 * f + 1] as usize], m.p[m.v[3 * f + 2] as usize]);
            let nn = (p1 - p0).cross(p2 - p0);
            let a2 = nn.length();
            if !(a2 > 1e-12) { continue; }
            let n = nn / a2;
            let g = [n.cross(p2 - p1) / a2, n.cross(p0 - p2) / a2, n.cross(p1 - p0) / a2];
            let (mut gu, mut gv) = (Vec3::ZERO, Vec3::ZERO);
            for a in 0..3 { gu += g[a] * out.uv[f][a][0]; gv += g[a] * out.uv[f][a][1]; }
            let e = ((gu - target[f][0]).length2() + (gv - target[f][1]).length2()).sqrt() * rho * face_scale[f];
            fe += e * 0.5 * a2;
            fa += 0.5 * a2;
        }
        out.face_error = if fa > 0.0 { fe / fa } else { 0.0 };
        out.seam_error = 0.0;
        out.bad_seams = 0;
        for s in seams {
            let rm = rot_m(s.k);
            let ru = x[2 * s.sb] - (rm[0][0] * x[2 * s.sa] + rm[0][1] * x[2 * s.sa + 1]) - x[2 * s.te];
            let rv = x[2 * s.sb + 1] - (rm[1][0] * x[2 * s.sa] + rm[1][1] * x[2 * s.sa + 1]) - x[2 * s.te + 1];
            out.seam_error = out.seam_error.max((ru * ru + rv * rv).sqrt());
            if ru * ru + rv * rv > 0.0625 { out.bad_seams += 1; }
        }
    }
}
