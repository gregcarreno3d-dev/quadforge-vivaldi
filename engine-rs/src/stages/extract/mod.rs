//! Global quad extraction ("grid route").
//!
//! A global lattice is laid over the surface instead of pairing triangles:
//!   1. a 4-direction field per face (`field`), a frame (normal, two lattice axes) per vertex
//!   2. an integer-grid parametrization (`param`) gives (u,v) at every triangle corner. If it fails, the older
//!      position-field heuristic (Gauss-Seidel on lattice origins, Jakob et al. 2015) is used instead.
//!   3. every vertex snaps to its nearest lattice node; vertices on the same node form one cell, neighbouring
//!      nodes create an edge (`lattice`)
//!   4. faces are traced in the rotation system of the cell graph; loops of 4 are quads (`lattice`)
//!   5. polygons become quads, relaxed and cleaned (`finish`)
//!
//! Work areas: `Extractor::prepare` (frames) · `Extractor::solve_param` (parametrization retries) ·
//! `Extractor::position_field` (fallback) · `lattice::*` (cells and faces) · `finish::*` (all-quad cleanup).
pub mod finish;
pub mod lattice;

use crate::io::fmesh::PolyMesh;
use crate::math::{Cplx, Vec3};
use crate::mesh::bvh::{SegmentIndex, SurfaceIndex};
use crate::mesh::cmesh::{face_of, nxt, prv, CMesh, Nb, V_FIXED};
use crate::stages::field::{axis_from_z, build_frames, solve_cross_field, FaceFrames, FieldInputs};
use crate::stages::param::{IntegerParam, ParamOptions, ParamResult};
use crate::util::parallel::par_fill;
use crate::util::rng::Mt19937;
use std::sync::Arc;
use std::time::Instant;

pub type SharedProgress = Arc<dyn Fn(i32, &str) + Send + Sync>;

pub struct ExtractParams<'g> {
    /// Lattice spacing.
    pub rho: f64,
    /// Position field sweeps (fallback path).
    pub iterations: i32,
    /// Final cell relaxation passes.
    pub smooth: i32,
    /// Split every face into quads (all-quad output).
    pub subdivide: bool,
    /// Chord rotation moves per face (0 = off).
    pub pole_moves: f64,
    pub constrain_lattice: bool,
    pub threads: usize,
    /// Relaxation passes after subdividing.
    pub final_smooth: i32,
    /// Integer-grid parametrization instead of the position field.
    pub use_param: bool,
    /// Seconds for one parametrization run (0 = no limit); on expiry the caller falls back.
    pub param_time_limit: f64,
    /// Keep marked edges and align the grid to them (needs `use_param`).
    pub align_features: bool,
    /// Pull of the cross field towards creases (0 = off).
    pub curv_align: f64,
    /// Bending angle that counts as a crease.
    pub curv_gate: f64,
    /// User flow guides (soft hints).
    pub guides: Option<&'g SegmentIndex>,
    pub guide_strength: f64,
    /// Reach of a guide in mean edge lengths of the field mesh.
    pub guide_radius: f64,
    pub progress: Option<SharedProgress>,
}

impl<'g> Default for ExtractParams<'g> {
    fn default() -> Self {
        ExtractParams {
            rho: 0.0, iterations: 60, smooth: 6, subdivide: false, pole_moves: 0.0, constrain_lattice: false, threads: 1,
            final_smooth: 15, use_param: false, param_time_limit: 0.0, align_features: false, curv_align: 0.0, curv_gate: 0.2,
            guides: None, guide_strength: 0.0, guide_radius: 4.0, progress: None,
        }
    }
}

#[derive(Default, Clone, Debug)]
pub struct ExtractStats {
    pub filled: i32,
    pub cells: i32,
    pub quads: i32,
    pub tris: i32,
    pub other: i32,
    pub dropped_loops: i32,
    pub edges: i32,
    /// Mean lattice mismatch / rho after the last sweep.
    pub residual: f64,
    pub field_seconds: f64,
    pub position_seconds: f64,
    pub graph_seconds: f64,
}

fn env_f64(name: &str) -> Option<f64> { std::env::var(name).ok().and_then(|s| s.trim().parse().ok()) }
fn env_set(name: &str) -> bool { std::env::var_os(name).is_some() }
fn dbg() -> bool { env_set("FORGE_DBG") }

/// Per-vertex data shared by the stages of the extraction.
pub struct VertexFrames {
    /// Live vertices.
    pub ids: Vec<i32>,
    /// Normal, lattice axes and lattice origin per vertex.
    pub n: Vec<Vec3>,
    pub q: Vec<Vec3>,
    pub r: Vec<Vec3>,
    pub o: Vec<Vec3>,
    pub adj_start: Vec<usize>,
    pub adj: Vec<i32>,
    /// 0 free, 1 crease (line), 2 corner (point).
    pub feat_flag: Vec<u8>,
}

pub struct Extractor<'a, 'g> {
    m: &'a CMesh,
    reference: &'a SurfaceIndex,
    feat: &'a SegmentIndex,
    pub prm: ExtractParams<'g>,
    vf: Option<VertexFrames>,
    param_runs: i32,
    /// How much of the density the grid can follow (lowered when the fit fails).
    dens_pow: f64,
    pr: ParamResult,
    /// Cross field of the working mesh.
    zf: Vec<Cplx>,
    coarse: Option<&'a CMesh>,
}

impl<'a, 'g> Extractor<'a, 'g> {
    pub fn new(m: &'a CMesh, reference: &'a SurfaceIndex, feat: &'a SegmentIndex, prm: ExtractParams<'g>) -> Self {
        Extractor {
            m, reference, feat, prm, vf: None, param_runs: 0, dens_pow: env_f64("FORGE_DENSPOW").unwrap_or(1.0),
            pr: ParamResult::default(), zf: Vec::new(), coarse: None,
        }
    }

    pub fn set_rho(&mut self, rho: f64) { self.prm.rho = rho; }
    /// Solve the cross field on a coarser mesh of the same surface and transfer it (much faster).
    pub fn set_field_mesh(&mut self, coarse: &'a CMesh) { self.coarse = Some(coarse); }

    fn report(&self, pct: i32, s: &str) { if let Some(f) = &self.prm.progress { f(pct, s); } }

    fn node(&self, o: Vec3, q: Vec3, r: Vec3, x: Vec3) -> Vec3 {
        let rho = self.prm.rho;
        let d = x - o;
        let a = (d.dot(q) / rho).round();
        let b = (d.dot(r) / rho).round();
        o + q * (a * rho) + r * (b * rho)
    }

    // ------------------------------------------------------------------------------------ 1. field and frames

    fn field_inputs(&self) -> FieldInputs<'g> {
        FieldInputs {
            use_features: self.prm.align_features, curv_strength: self.prm.curv_align, curv_gate: self.prm.curv_gate,
            guides: self.prm.guides, guide_strength: self.prm.guide_strength, guide_radius: self.prm.guide_radius,
        }
    }

    /// Cross field of the working mesh (solved on the coarse mesh and transferred when one was given).
    fn working_field(&self, fr: &FaceFrames) -> Vec<Cplx> {
        let m = self.m;
        let n_f = m.num_face_slots() as usize;
        let inp = self.field_inputs();
        let Some(coarse) = self.coarse else { return solve_cross_field(m, fr, &inp) };
        let cf = build_frames(coarse);
        let cz = solve_cross_field(coarse, &cf, &inp);
        let mut ct: Vec<[i32; 3]> = Vec::new();
        let mut cface: Vec<usize> = Vec::new();
        for f in 0..coarse.num_face_slots() as usize {
            if coarse.falive[f] { ct.push([coarse.v[3 * f], coarse.v[3 * f + 1], coarse.v[3 * f + 2]]); cface.push(f); }
        }
        let cidx = SurfaceIndex::new(&coarse.p, &ct);
        let mut z = vec![Cplx::ONE; n_f];
        par_fill(&mut z, self.prm.threads, |f| {
            if !m.falive[f] { return Cplx::ONE; }
            let c = (m.p[m.v[3 * f] as usize] + m.p[m.v[3 * f + 1] as usize] + m.p[m.v[3 * f + 2] as usize]) / 3.0;
            let h = cidx.closest_any(c);
            if h.tri < 0 { return Cplx::ONE; }
            let g = cface[h.tri as usize];
            let a = axis_from_z(&cf, g, cz[g]);
            Cplx::polar(1.0, 4.0 * a.dot(fr.e2[f]).atan2(a.dot(fr.e1[f])))
        });
        if self.prm.align_features {
            // faces next to a marked edge keep the exact alignment (the coarse mesh only knows it approximately)
            for f in 0..n_f {
                if !m.falive[f] { continue; }
                let (mut target, mut conflict) = (None::<Cplx>, false);
                for k in 0..3 {
                    if !fr.feat[f][k] { continue; }
                    let t = Cplx::polar(1.0, 4.0 * fr.alpha[f][k]);
                    match target {
                        None => target = Some(t),
                        Some(tg) => if (tg.conj() * t).re < 0.5 { conflict = true; },
                    }
                }
                if let (Some(t), false) = (target, conflict) { z[f] = t; }
            }
        }
        z
    }

    fn prepare(&mut self) {
        let m = self.m;
        let n_vs = m.num_vert_slots() as usize;
        let n_f = m.num_face_slots() as usize;
        let fr = build_frames(m);
        let z = self.working_field(&fr);
        let mut n = vec![Vec3::ZERO; n_vs];
        let mut q = vec![Vec3::ZERO; n_vs];
        let mut r = vec![Vec3::ZERO; n_vs];
        let mut o = vec![Vec3::ZERO; n_vs];
        let mut acc = vec![Cplx::ZERO; n_vs];
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let nn = m.face_normal_unnormalized(f as i32);
            for k in 0..3 { n[m.v[3 * f + k] as usize] += nn; }
        }
        let mut ids: Vec<i32> = Vec::new();
        let mut t1 = vec![Vec3::ZERO; n_vs];
        for v in 0..n_vs {
            if !m.valive[v] { continue; }
            ids.push(v as i32);
            n[v] = n[v].normalized();
            let helper = if n[v].x.abs() < 0.9 { Vec3::new(1.0, 0.0, 0.0) } else { Vec3::new(0.0, 1.0, 0.0) };
            t1[v] = n[v].cross(helper).normalized();
        }
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let a = axis_from_z(&fr, f, z[f]);
            let w = m.face_normal_unnormalized(f as i32).length();
            for k in 0..3 {
                let v = m.v[3 * f + k] as usize;
                let mut ap = a - n[v] * n[v].dot(a);
                let l = ap.length();
                if l < 1e-6 { continue; }
                ap = ap / l;
                let phi = ap.dot(n[v].cross(t1[v])).atan2(ap.dot(t1[v]));
                acc[v] += Cplx::polar(w * l, 4.0 * phi);
            }
        }
        for &v in &ids {
            let v = v as usize;
            let th = if acc[v].abs() > 1e-30 { acc[v].arg() / 4.0 } else { 0.0 };
            let t2 = n[v].cross(t1[v]);
            q[v] = t1[v] * th.cos() + t2 * th.sin();
            r[v] = n[v].cross(q[v]);
            o[v] = m.p[v];
        }
        // features: neighbours across feature edges / borders, lattice aligned to the crease
        let mut feat_nb: Vec<Vec<i32>> = vec![Vec::new(); n_vs];
        for c in 0..(n_f * 3) as i32 {
            if !m.falive[face_of(c) as usize] || m.e[c as usize] == 0 { continue; }
            let (a, b) = (m.v[nxt(c) as usize], m.v[prv(c) as usize]);
            if !feat_nb[a as usize].contains(&b) { feat_nb[a as usize].push(b); }
            if !feat_nb[b as usize].contains(&a) { feat_nb[b as usize].push(a); }
        }
        let mut ftype = vec![0u8; n_vs];
        for &v in &ids {
            let v = v as usize;
            if feat_nb[v].is_empty() { continue; }
            if feat_nb[v].len() != 2 || m.vtype[v] == V_FIXED { ftype[v] = 2; continue; }
            ftype[v] = 1;
            let d0 = if feat_nb[v][1] == feat_nb[v][0] { Vec3::ZERO } else { m.p[feat_nb[v][1] as usize] - m.p[feat_nb[v][0] as usize] };
            let mut d = d0 - n[v] * n[v].dot(d0);
            if d.length() < 1e-12 { ftype[v] = 2; continue; }
            d = d.normalized();
            if d.dot(q[v]).abs() >= d.dot(r[v]).abs() {
                q[v] = if d.dot(q[v]) >= 0.0 { d } else { -d };
            } else {
                let qq = n[v].cross(d);
                q[v] = if qq.dot(q[v]) >= 0.0 { qq } else { -qq };
            }
            r[v] = n[v].cross(q[v]);
        }
        let mut feat_flag = ftype;
        if !self.prm.constrain_lattice && !self.prm.align_features { feat_flag.iter_mut().for_each(|f| *f = 0); }
        // adjacency
        let mut adj_start = vec![0usize; n_vs + 1];
        let mut tmp: Vec<Vec<i32>> = vec![Vec::new(); n_vs];
        let mut nb: Vec<Nb> = Vec::new();
        let mut scratch: Vec<i32> = Vec::new();
        for &v in &ids {
            m.neighbors(v, &mut nb, &mut scratch);
            tmp[v as usize] = nb.iter().map(|e| e.v).collect();
        }
        for v in 0..n_vs { adj_start[v + 1] = adj_start[v] + tmp[v].len(); }
        let adj: Vec<i32> = tmp.into_iter().flatten().collect();
        self.zf = z;
        self.vf = Some(VertexFrames { ids, n, q, r, o, adj_start, adj, feat_flag });
    }

    // ------------------------------------------------------------------------------------ 2a. parametrization

    fn param_options(&self, first: bool) -> ParamOptions {
        let progress = if first { self.prm.progress.clone() } else { None };
        ParamOptions {
            align_features: self.prm.align_features, density_power: self.dens_pow, time_limit: self.prm.param_time_limit,
            threads: self.prm.threads,
            progress: progress.map(|p| -> crate::stages::param::Progress { Box::new(move |a, b| p(a, b)) }),
        }
    }

    /// Runs the parametrization, softening the density when the surface cannot follow it.
    /// Returns Ok(param_ok) or Err(message) when the whole extraction must stop.
    fn solve_param(&mut self, rho: f64) -> Result<bool, String> {
        let m = self.m;
        let pfr = build_frames(m);
        let ids = &self.vf.as_ref().unwrap().ids;
        let has_dens = ids.iter().any(|&v| (m.vdens[v as usize] - 1.0).abs() > 1e-3);
        if dbg() {
            let (lo, hi) = ids.iter().fold((1e9f32, -1e9f32), |a, &v| (a.0.min(m.vdens[v as usize]), a.1.max(m.vdens[v as usize])));
            eprintln!("vdens range {lo:.3}..{hi:.3} hasDens {has_dens} pow {:.2}", self.dens_pow);
        }
        let mut tried: Vec<(f64, f64)> = Vec::new(); // (density power, fit) of the attempts that were too poor
        let mut param_ok = false;
        let mut pr = std::mem::take(&mut self.pr);
        pr.uv.clear();
        pr.singular.clear();
        for _ in 0..5 {
            let first = self.param_runs == 0;
            self.param_runs += 1;
            let ip = IntegerParam::new(m, &pfr, &self.zf, self.param_options(first));
            param_ok = ip.run(rho, &mut pr);
            // a size gradient the surface cannot follow (it needs singularities the field does not have)
            // makes the fit fail: follow only part of it instead
            // softening that does not improve the fit (the error grows as the power falls) is a dead end: do not
            // spend three more solves on it when even the best attempt is far too poor
            if param_ok && has_dens {
                if let Some(&(_, prev)) = tried.last() {
                    if pr.face_error >= prev * 0.97 && prev > 0.5 {
                        if dbg() { eprintln!("param: softening does not help ({prev:.3} -> {:.3}), giving up on the density", pr.face_error); }
                        self.pr = pr;
                        return Err("the size variation is too strong for a regular grid on this shape".into());
                    }
                }
            }
            if !param_ok || !has_dens || self.dens_pow <= 0.0 || pr.face_error <= 0.45 { break; }
            if dbg() { eprintln!("param: density fit {:.3} too poor at power {:.2}, softening", pr.face_error, self.dens_pow); }
            tried.push((self.dens_pow, pr.face_error));
            self.dens_pow = if self.dens_pow > 0.3 { self.dens_pow * 0.5 } else { 0.0 };
            pr.warm.clear();
            pr.warm_rho = 0.0;
        }
        // The 0.45 limit is absolute, but a model with many constrained borders (zones, creases) can already sit
        // above it with little or no density. Then judge the density by what it adds: if a larger power costs
        // only a little more than the plain fit, use it.
        if param_ok && has_dens && !tried.is_empty() {
            let base = pr.face_error;
            for &(power, fit) in &tried {
                if fit <= base * 1.12 + 0.02 {
                    if dbg() { eprintln!("param: plain fit {base:.3}, taking density power {power:.2} (fit {fit:.3})"); }
                    self.dens_pow = power;
                    let mut again = ParamResult::default();
                    let ip = IntegerParam::new(m, &pfr, &self.zf, self.param_options(false));
                    if ip.run(rho, &mut again) { pr = again; } else { self.dens_pow = (self.dens_pow * 0.5).max(0.0); }
                    break;
                }
            }
        }
        // (The experimental retry with a slightly different cell size, FORGE_RETRY, is not ported.)
        if !param_ok && pr.error == "time limit" {
            self.pr = pr;
            return Err("the grid solve took longer than the time limit".into());
        }
        if param_ok && has_dens && pr.face_error > 0.5 {
            self.pr = pr;
            return Err("the size variation is too strong for a regular grid on this shape".into());
        }
        if dbg() {
            eprintln!("param: ok {} '{}' sing {} cut {} faceErr {:.3} seamErr {:.1e} solves {}",
                param_ok, pr.error, pr.singular.len(), pr.cut_edges, pr.face_error, pr.seam_error, pr.solves);
        }
        self.pr = pr;
        Ok(param_ok)
    }

    // ------------------------------------------------------------------------------------ 2b. position field

    /// The older heuristic: optimise lattice origins so neighbouring lattices share nodes (Gauss-Seidel).
    fn position_field(&mut self, rho: f64, st: &mut ExtractStats) {
        let m = self.m;
        let node = |o: Vec3, q: Vec3, r: Vec3, x: Vec3| {
            let d = x - o;
            o + q * ((d.dot(q) / rho).round() * rho) + r * ((d.dot(r) / rho).round() * rho)
        };
        let progress = self.prm.progress.clone();
        let iterations = self.prm.iterations;
        let vf = self.vf.as_mut().unwrap();
        let mut order = vf.ids.clone();
        let mut rng = Mt19937::new(12345);
        for i in (1..order.len()).rev() {
            let j = (rng.next_u32() as usize) % (i + 1);
            order.swap(i, j);
        }
        let best_frame = |vf: &VertexFrames, i: usize, j: usize| -> (Vec3, Vec3) {
            // the rotation of j's axes closest to i's axes
            let cand = [vf.q[j], vf.r[j], -vf.q[j], -vf.r[j]];
            let (mut best, mut bd) = (0usize, -2.0f64);
            for k in 0..4 { let d = cand[k].dot(vf.q[i]); if d > bd { bd = d; best = k; } }
            let qj = cand[best];
            (qj, vf.n[j].cross(qj))
        };
        let fix_o = |vf: &VertexFrames, j: usize, o: Vec3| -> Vec3 {
            if vf.feat_flag[j] == 2 { return m.p[j]; }
            let d0 = o - m.p[j];
            let d = d0 - vf.n[j] * vf.n[j].dot(d0);
            if vf.feat_flag[j] == 1 {
                let a = (d.dot(vf.q[j]) / rho).round();
                return m.p[j] + vf.q[j] * (d.dot(vf.q[j]) - a * rho);
            }
            let a = (d.dot(vf.q[j]) / rho).round();
            let b = (d.dot(vf.r[j]) / rho).round();
            m.p[j] + d - vf.q[j] * (a * rho) - vf.r[j] * (b * rho)
        };
        // initial guess: propagate the lattice along a breadth first tree
        let mut seen = vec![false; m.num_vert_slots() as usize];
        for &s in &order {
            if seen[s as usize] { continue; }
            seen[s as usize] = true;
            let mut queue = vec![s as usize];
            let mut h = 0;
            while h < queue.len() {
                let i = queue[h];
                h += 1;
                for e in vf.adj_start[i]..vf.adj_start[i + 1] {
                    let j = vf.adj[e] as usize;
                    if seen[j] { continue; }
                    seen[j] = true;
                    queue.push(j);
                    let (qj, rj) = best_frame(vf, i, j);
                    let mid = (m.p[i] + m.p[j]) * 0.5;
                    let ti = node(vf.o[i], vf.q[i], vf.r[i], mid);
                    let tj = node(vf.o[j], qj, rj, ti);
                    let no = fix_o(vf, j, vf.o[j] + (ti - tj));
                    vf.o[j] = no;
                }
            }
        }
        for it in 0..iterations {
            let (mut resid, mut cnt) = (0.0f64, 0i64);
            for idx in 0..order.len() {
                let i = (if it & 1 == 1 { order[order.len() - 1 - idx] } else { order[idx] }) as usize;
                let (mut sum, mut ws) = (Vec3::ZERO, 0.0);
                for e in vf.adj_start[i]..vf.adj_start[i + 1] {
                    let j = vf.adj[e] as usize;
                    let (qj, rj) = best_frame(vf, i, j);
                    let mid = (m.p[i] + m.p[j]) * 0.5;
                    let ti = node(vf.o[i], vf.q[i], vf.r[i], mid);
                    let tj = node(vf.o[j], qj, rj, ti);
                    sum += vf.o[i] + (tj - ti);
                    ws += 1.0;
                    resid += (tj - ti).length();
                    cnt += 1;
                }
                if ws == 0.0 { continue; }
                let no = fix_o(vf, i, sum / ws);
                vf.o[i] = no;
            }
            st.residual = if cnt > 0 { resid / (cnt as f64 * rho) } else { 0.0 };
            if dbg() && (it < 5 || it % 20 == 0) { eprintln!("it {it} resid {:.4}", st.residual); }
            if it % 10 == 0 { if let Some(p) = &progress { p(78 + (it * 12) / iterations.max(1), "Optimising the quad grid"); } }
        }
    }

    // ------------------------------------------------------------------------------------ driver

    pub fn run(&mut self, out: &mut PolyMesh, st: &mut ExtractStats) -> Result<(), String> {
        let rho = self.prm.rho;
        let t0 = Instant::now();
        if self.vf.is_none() {
            self.prepare();
        } else {
            let m = self.m;
            let vf = self.vf.as_mut().unwrap();
            for &v in &vf.ids { vf.o[v as usize] = m.p[v as usize]; }
        }
        st.field_seconds = t0.elapsed().as_secs_f64();
        let t1 = Instant::now();
        // ---- 2. parametrization or position field
        let mut param_ok = false;
        if self.prm.use_param { param_ok = self.solve_param(rho)?; }
        self.report(78, "Optimising the quad grid");
        if !param_ok { self.position_field(rho, st); }
        st.position_seconds = t1.elapsed().as_secs_f64();
        // ---- 3-5. cells, faces and the all-quad cleanup
        let t2 = Instant::now();
        let vf = self.vf.as_ref().unwrap();
        let ctx = lattice::LatticeContext {
            m: self.m, reference: self.reference, feat: self.feat, vf, rho, param: if param_ok { Some(&self.pr) } else { None },
            density_power: self.pr.density_power, smooth: self.prm.smooth,
        };
        self.report(91, "Extracting quads");
        let (poly, cfeat) = lattice::extract_polygons(&ctx, st).map_err(|e| e.to_string())?;
        if poly.sizes.is_empty() { return Err("extraction produced no faces".into()); }
        for &sz in &poly.sizes { match sz { 4 => st.quads += 1, 3 => st.tris += 1, _ => st.other += 1 } }
        *out = poly;
        if self.prm.subdivide {
            let fp = finish::FinishParams { rho, final_smooth: self.prm.final_smooth, pole_moves: self.prm.pole_moves };
            *out = finish::finish(out, &cfeat, self.feat, self.reference, &fp);
        }
        st.graph_seconds = t2.elapsed().as_secs_f64();
        Ok(())
    }
}
