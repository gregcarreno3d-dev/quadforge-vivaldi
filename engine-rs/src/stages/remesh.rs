//! Isotropic triangle remeshing (Botsch & Kobbelt style) with
//! * feature lines and open borders kept as lines,
//! * per-vertex density (edge length multiplier),
//! * exact face-count targeting,
//! * snapping onto the original surface after every pass.
use crate::io::fmesh::PolyMesh;
use crate::math::Vec3;
use crate::mesh::bvh::{SegmentIndex, SurfaceIndex};
use crate::mesh::cmesh::{face_of, nxt, prv, CMesh, Nb, V_CREASE, V_FIXED, V_FREE};
use crate::stages::repair::TriSoup;
use crate::util::parallel::par_fill;
use std::time::Instant;

pub type ProgressFn = Box<dyn Fn(i32, &str) + Sync>;

pub struct RemeshParams {
    pub target_faces: i32,
    /// Main passes at the initial edge length.
    pub iterations: i32,
    /// Extra passes that steer the face count to the target.
    pub correction_rounds: i32,
    /// Final smoothing passes.
    pub polish_passes: i32,
    pub threads: usize,
    pub project: bool,
    pub corner_angle_deg: f64,
    pub count_tolerance: f64,
    pub smoothing: f64,
    /// Validate the mesh after every step (slow, for tests).
    pub check: bool,
    pub progress: Option<ProgressFn>,
}

impl Default for RemeshParams {
    fn default() -> Self {
        RemeshParams {
            target_faces: 10000, iterations: 8, correction_rounds: 10, polish_passes: 3, threads: 1, project: true,
            corner_angle_deg: 35.0, count_tolerance: 0.02, smoothing: 0.8, check: false, progress: None,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub struct RemeshStats {
    pub input_faces: i32,
    pub output_faces: i32,
    pub output_verts: i32,
    pub edge_length: f64,
    pub rounds: i32,
    pub seconds: f64,
    pub error: String,
}

struct EdgeRef { key: f64, a: i32, b: i32 }

/// Reusable pieces (the quad stage builds on the same mesh and reference) live in public fields.
pub struct Remesher<'a> {
    soup: &'a TriSoup,
    pub prm: RemeshParams,
    pub mesh: CMesh,
    pub reference: SurfaceIndex,
    pub feature_lines: SegmentIndex,
    pub base_length: f64,
    normals: Vec<Vec3>,
    pub splits: i64,
    pub collapses: i64,
    pub flips: i64,
}

impl<'a> Remesher<'a> {
    pub fn new(soup: &'a TriSoup, prm: RemeshParams) -> Self {
        Remesher {
            soup, prm, mesh: CMesh::default(), reference: SurfaceIndex::default(), feature_lines: SegmentIndex::default(),
            base_length: 0.0, normals: Vec::new(), splits: 0, collapses: 0, flips: 0,
        }
    }

    fn target_len(&self, u: i32, v: i32, l: f64) -> f64 {
        l * 0.5 * (self.mesh.vdens[u as usize] as f64 + self.mesh.vdens[v as usize] as f64)
    }

    fn report(&self, pct: i32, msg: &str) { if let Some(f) = &self.prm.progress { f(pct, msg); } }

    fn validate(&self, wher: &str, st: &mut RemeshStats) -> bool {
        if !self.prm.check { return true; }
        let e = self.mesh.check();
        if e.is_empty() { return true; }
        st.error = format!("mesh check failed after {wher}: {e}");
        false
    }

    fn compute_normals(&mut self) {
        let m = &self.mesh;
        let mut n = vec![Vec3::ZERO; m.p.len()];
        for f in 0..m.num_face_slots() {
            if !m.falive[f as usize] { continue; }
            let fnn = m.face_normal_unnormalized(f); // area weighted
            for k in 0..3 { n[m.v[3 * f as usize + k] as usize] += fnn; }
        }
        for v in n.iter_mut() { *v = v.normalized(); }
        self.normals = n;
    }

    /// Corner x such that the edge opposite x joins vertices a and b, or -1.
    fn find_edge(&self, a: i32, b: i32, sc: &mut Vec<i32>) -> i32 {
        let m = &self.mesh;
        if a < 0 || b < 0 || !m.valive[a as usize] || !m.valive[b as usize] { return -1; }
        m.orbit(a, sc);
        for &c in sc.iter() {
            if m.v[nxt(c) as usize] == b { return prv(c); }
            if m.v[prv(c) as usize] == b { return nxt(c); }
        }
        -1
    }

    // Edges are collected as vertex pairs first and looked up again when processed: corner numbers change under
    // splits, collapses and flips, so walking corner indices would skip edges.
    fn split_pass(&mut self, l: f64) -> i32 {
        let mut cand: Vec<EdgeRef> = Vec::new();
        let n0 = self.mesh.num_face_slots() * 3;
        for c in 0..n0 {
            let m = &self.mesh;
            if !m.falive[face_of(c) as usize] { continue; }
            let d = m.o[c as usize];
            if d >= 0 && d < c { continue; }
            let (a, b) = (m.v[nxt(c) as usize], m.v[prv(c) as usize]);
            let len = m.p[a as usize].distance(m.p[b as usize]);
            if len > 4.0 / 3.0 * self.target_len(a, b, l) { cand.push(EdgeRef { key: -len, a, b }); } // longest first
        }
        cand.sort_by(|x, y| x.key.partial_cmp(&y.key).unwrap_or(std::cmp::Ordering::Equal));
        let mut count = 0;
        let mut sc = Vec::new();
        for e in &cand {
            let x = self.find_edge(e.a, e.b, &mut sc);
            if x < 0 { continue; }
            let m = &mut self.mesh;
            let dens = 0.5 * (m.vdens[e.a as usize] + m.vdens[e.b as usize]);
            let mid = (m.p[e.a as usize] + m.p[e.b as usize]) * 0.5;
            m.split_edge(x, mid, dens);
            count += 1;
        }
        self.splits += count as i64;
        count
    }

    fn try_collapse(&mut self, c: i32, keep: i32, rem: i32, l: f64) -> bool {
        let (tk, tr) = (self.mesh.vtype[keep as usize], self.mesh.vtype[rem as usize]);
        if tr == V_FIXED { return false; }
        if tr == V_CREASE && (self.mesh.e[c as usize] == 0 || tk == V_FREE) { return false; } // creases only merge along their own line
        let m = &self.mesh;
        let d = m.o[c as usize];
        let a = m.v[c as usize];
        let dd = if d >= 0 { m.v[d as usize] } else { -1 };
        let fc = face_of(c);
        let fd = if d >= 0 { face_of(d) } else { -1 };

        // link condition
        let mut nk: Vec<Nb> = Vec::new();
        let mut nr: Vec<Nb> = Vec::new();
        let mut scratch: Vec<i32> = Vec::new();
        m.neighbors(keep, &mut nk, &mut scratch);
        m.neighbors(rem, &mut nr, &mut scratch);
        let mut common = 0;
        for x in &nk { for y in &nr { if x.v == y.v { common += 1; } } }
        if common != if d >= 0 { 2 } else { 1 } { return false; }

        // valence of the two apex vertices must stay valid
        {
            let closed_a = m.orbit(a, &mut scratch);
            let va = scratch.len() as i32 + if closed_a { 0 } else { 1 };
            if va - 1 < if closed_a { 3 } else { 2 } { return false; }
            if dd >= 0 {
                let closed_d = m.orbit(dd, &mut scratch);
                let vd = scratch.len() as i32 + if closed_d { 0 } else { 1 };
                if vd - 1 < if closed_d { 3 } else { 2 } { return false; }
            }
        }

        let mut new_pos = m.p[keep as usize];
        if tk == V_FREE && tr == V_FREE { new_pos = (m.p[keep as usize] + m.p[rem as usize]) * 0.5; }
        let moved = new_pos != m.p[keep as usize];

        // geometry: no flipped or degenerate triangles, no overlong edges
        let check_fan = |v: i32, scratch: &mut Vec<i32>| -> bool {
            m.orbit(v, scratch);
            for &k in scratch.iter() {
                let f = face_of(k);
                if f == fc || f == fd { continue; }
                let (x, y) = (m.v[nxt(k) as usize], m.v[prv(k) as usize]);
                let pv = m.p[v as usize];
                let n_old = (m.p[x as usize] - pv).cross(m.p[y as usize] - pv);
                let n_new = (m.p[x as usize] - new_pos).cross(m.p[y as usize] - new_pos);
                let (lo, ln) = (n_old.length(), n_new.length());
                if ln <= 1e-14 * (lo + 1e-300) || ln < 1e-18 { return false; }
                if n_old.dot(n_new) < 0.3 * lo * ln { return false; }
                if v == rem || moved {
                    let max_a = 4.0 / 3.0 * self.target_len(keep, x, l);
                    let max_b = 4.0 / 3.0 * self.target_len(keep, y, l);
                    if new_pos.distance(m.p[x as usize]) > max_a || new_pos.distance(m.p[y as usize]) > max_b { return false; }
                }
            }
            true
        };
        if !check_fan(rem, &mut scratch) { return false; }
        if moved && !check_fan(keep, &mut scratch) { return false; }

        self.mesh.collapse_edge(c, keep, rem, new_pos);
        true
    }

    fn collapse_pass(&mut self, l: f64) -> i32 {
        let mut cand: Vec<EdgeRef> = Vec::new();
        let n0 = self.mesh.num_face_slots() * 3;
        for c in 0..n0 {
            let m = &self.mesh;
            if !m.falive[face_of(c) as usize] { continue; }
            let d = m.o[c as usize];
            if d >= 0 && d < c { continue; }
            let (u, v) = (m.v[nxt(c) as usize], m.v[prv(c) as usize]);
            let len = m.p[u as usize].distance(m.p[v as usize]);
            if len < 0.8 * self.target_len(u, v, l) { cand.push(EdgeRef { key: len, a: u, b: v }); } // shortest first
        }
        cand.sort_by(|x, y| x.key.partial_cmp(&y.key).unwrap_or(std::cmp::Ordering::Equal));
        let mut count = 0;
        let mut sc = Vec::new();
        for e in &cand {
            let x = self.find_edge(e.a, e.b, &mut sc);
            if x < 0 { continue; }
            if self.mesh.p[e.a as usize].distance(self.mesh.p[e.b as usize]) >= 0.8 * self.target_len(e.a, e.b, l) { continue; }
            if self.try_collapse(x, e.a, e.b, l) || self.try_collapse(x, e.b, e.a, l) { count += 1; }
        }
        self.collapses += count as i64;
        count
    }

    fn flip_pass(&mut self, l: f64) -> i32 {
        let mut count = 0;
        let mut sc: Vec<i32> = Vec::new();
        let mut nb: Vec<Nb> = Vec::new();
        let mut cand: Vec<(i32, i32)> = Vec::new();
        let n0 = self.mesh.num_face_slots() * 3;
        {
            let m = &self.mesh;
            for c in 0..n0 {
                if !m.falive[face_of(c) as usize] { continue; }
                let d0 = m.o[c as usize];
                if d0 < 0 || d0 < c || m.e[c as usize] != 0 { continue; }
                cand.push((m.v[nxt(c) as usize], m.v[prv(c) as usize]));
            }
        }
        for &(ea, eb) in &cand {
            let c = self.find_edge(ea, eb, &mut sc);
            if c < 0 { continue; }
            let m = &self.mesh;
            let d = m.o[c as usize];
            if d < 0 || m.e[c as usize] != 0 { continue; }
            let (a, b, cc, dd) = (m.v[c as usize], m.v[nxt(c) as usize], m.v[prv(c) as usize], m.v[d as usize]);
            if a == dd { continue; }
            let ids = [a, dd, b, cc];
            let mut val = [0i32; 4];
            let mut target = [0i32; 4];
            let mut closed = [false; 4];
            for i in 0..4 {
                closed[i] = m.orbit(ids[i], &mut sc);
                val[i] = sc.len() as i32 + if closed[i] { 0 } else { 1 };
                target[i] = if closed[i] { 6 } else { 4 };
            }
            if val[2] - 1 < if closed[2] { 3 } else { 2 } || val[3] - 1 < if closed[3] { 3 } else { 2 } { continue; }
            let sq = |x: i32| x * x;
            let before = sq(val[0] - target[0]) + sq(val[1] - target[1]) + sq(val[2] - target[2]) + sq(val[3] - target[3]);
            let after = sq(val[0] + 1 - target[0]) + sq(val[1] + 1 - target[1]) + sq(val[2] - 1 - target[2]) + sq(val[3] - 1 - target[3]);
            if after >= before { continue; }
            // A and D must not be connected already
            m.neighbors(a, &mut nb, &mut sc);
            if nb.iter().any(|x| x.v == dd) { continue; }
            // geometry
            let (pa, pb, pc, pd) = (m.p[a as usize], m.p[b as usize], m.p[cc as usize], m.p[dd as usize]);
            let n1 = (pb - pa).cross(pc - pa);
            let n2 = (pc - pd).cross(pb - pd); // old (A,B,C) and (D,C,B)
            let m1 = (pb - pa).cross(pd - pa);
            let m2 = (pc - pd).cross(pa - pd); // new (A,B,D) and (D,C,A)
            let (l1, l2) = (m1.length(), m2.length());
            if l1 < 1e-18 || l2 < 1e-18 { continue; }
            if m1.dot(m2) < 0.2 * l1 * l2 { continue; }
            let nold = n1 + n2;
            if m1.dot(nold) <= 0.0 || m2.dot(nold) <= 0.0 { continue; }
            if pa.distance(pd) > 4.0 / 3.0 * self.target_len(a, dd, l) { continue; }
            self.mesh.flip_edge(c);
            count += 1;
        }
        self.flips += count as i64;
        count
    }

    /// Move vertices to even out the triangles and put them back on the reference surface.
    pub fn snap_to_reference(&mut self, smoothing: f64, tangential_smooth: bool) {
        self.compute_normals();
        let n_v = self.mesh.p.len();
        let have_dens = !self.soup.density.is_empty();
        let limit2 = 4.0 * self.base_length * self.base_length * 4.0; // never move more than 4 edge lengths
        let mut out = vec![(Vec3::ZERO, 0f32); n_v];
        {
            let m = &self.mesh;
            let normals = &self.normals;
            let soup = self.soup;
            let reference = &self.reference;
            let feature_lines = &self.feature_lines;
            let project = self.prm.project;
            par_fill(&mut out, self.prm.threads, |v| {
                let mut dens = m.vdens[v];
                if !m.valive[v] || m.vtype[v] == V_FIXED { return (m.p[v], dens); }
                let mut p = m.p[v];
                if tangential_smooth {
                    let mut nb = Vec::new();
                    let mut sc = Vec::new();
                    m.neighbors(v as i32, &mut nb, &mut sc);
                    if m.vtype[v] == V_FREE {
                        if nb.is_empty() { return (m.p[v], dens); }
                        let mut q = Vec3::ZERO;
                        for x in &nb { q += m.p[x.v as usize]; }
                        q = q / nb.len() as f64;
                        let mut dlt = q - p;
                        dlt = dlt - normals[v] * normals[v].dot(dlt);
                        p = p + dlt * smoothing;
                    } else { // crease: slide along the line
                        let mut q = Vec3::ZERO;
                        let mut n = 0;
                        for x in &nb { if x.feature { q += m.p[x.v as usize]; n += 1; } }
                        if n == 2 { p = p + (q / 2.0 - p) * smoothing; }
                    }
                }
                if project {
                    if m.vtype[v] == V_FREE {
                        let hit = reference.closest(p, Some(normals[v]), 0.1);
                        if hit.tri >= 0 && hit.p.distance2(m.p[v]) < limit2 {
                            p = hit.p;
                            if have_dens {
                                let t = soup.f[hit.tri as usize];
                                dens = ((1.0 - hit.u - hit.v) * soup.density[t[0] as usize] as f64
                                    + hit.u * soup.density[t[1] as usize] as f64
                                    + hit.v * soup.density[t[2] as usize] as f64) as f32;
                            }
                        }
                    } else if !feature_lines.is_empty() {
                        let hit = feature_lines.closest(p);
                        if hit.seg >= 0 && hit.p.distance2(m.p[v]) < limit2 { p = hit.p; }
                    }
                }
                (p, dens)
            });
        }
        for v in 0..n_v {
            self.mesh.p[v] = out[v].0;
            if have_dens { self.mesh.vdens[v] = out[v].1; }
        }
    }

    fn step(&mut self, l: f64, smooth_repeats: i32) {
        let mut ns = 0;
        for _ in 0..6 {
            let n = self.split_pass(l);
            ns += n;
            if n == 0 { break; }
        }
        let nf0 = self.mesh.alive_faces;
        let nc = self.collapse_pass(l);
        let nfl = self.flip_pass(l);
        if self.prm.check {
            eprintln!("step: splits {ns} -> faces {nf0}, collapses {nc}, flips {nfl}, faces now {}", self.mesh.alive_faces);
        }
        for _ in 0..smooth_repeats { let s = self.prm.smoothing; self.snap_to_reference(s, true); }
        self.mesh.compact();
    }

    pub fn run(&mut self, out: &mut PolyMesh, st: &mut RemeshStats) -> bool {
        let t0 = Instant::now();
        let soup = self.soup;
        st.input_faces = soup.f.len() as i32;
        if soup.f.is_empty() { st.error = "the mesh has no valid faces".into(); return false; }
        match CMesh::build(soup, self.prm.corner_angle_deg) {
            Ok(m) => self.mesh = m,
            Err(e) => { st.error = e; return false; }
        }
        if !self.validate("build", st) { return false; }

        self.reference = SurfaceIndex::new(&soup.p, &soup.f);
        {
            let m = &self.mesh;
            let mut segs = Vec::new();
            for c in 0..m.num_face_slots() * 3 {
                if m.e[c as usize] == 0 { continue; }
                if m.o[c as usize] >= 0 && m.o[c as usize] < c { continue; }
                segs.push([m.p[m.v[nxt(c) as usize] as usize], m.p[m.v[prv(c) as usize] as usize]]);
            }
            self.feature_lines = SegmentIndex::new(segs);
        }

        // initial edge length from the (density weighted) surface area
        let mut weighted_area = 0.0;
        for t in &soup.f {
            let (a, b, c) = (soup.p[t[0] as usize], soup.p[t[1] as usize], soup.p[t[2] as usize]);
            let area = 0.5 * (b - a).cross(c - a).length();
            let mut dn = 1.0;
            if !soup.density.is_empty() {
                dn = (soup.density[t[0] as usize] as f64 + soup.density[t[1] as usize] as f64 + soup.density[t[2] as usize] as f64) / 3.0;
            }
            weighted_area += area / (dn * dn).max(1e-6);
        }
        let n_target = self.prm.target_faces.max(4) as f64;
        let mut l = (4.0 * weighted_area / (3f64.sqrt() * n_target)).sqrt();
        self.base_length = l;
        if !(l > 0.0) || !l.is_finite() { st.error = "the mesh has zero surface area".into(); return false; }

        let total = self.prm.iterations + self.prm.correction_rounds + self.prm.polish_passes;
        let mut done = 0;
        for _ in 0..self.prm.iterations {
            self.step(l, 2);
            if !self.validate("main pass", st) { return false; }
            done += 1;
            self.report(10 + 60 * done / total, &format!("Remeshing faces={} L={}", self.mesh.alive_faces, l));
        }
        // steer the face count towards the target
        let mut rounds = 0;
        while rounds < self.prm.correction_rounds {
            let ratio = self.mesh.alive_faces as f64 / n_target;
            if (ratio - 1.0).abs() <= self.prm.count_tolerance && rounds > 0 { break; }
            if (ratio - 1.0).abs() > self.prm.count_tolerance {
                l *= ratio.sqrt();
                self.base_length = l;
            }
            self.step(l, 2);
            if !self.validate("correction pass", st) { return false; }
            done += 1;
            self.report(10 + 60 * done / total, "Matching the target");
            rounds += 1;
        }
        st.rounds = rounds;
        for _ in 0..self.prm.polish_passes {
            self.flip_pass(l);
            let s = self.prm.smoothing;
            self.snap_to_reference(s, true);
            if !self.validate("polish", st) { return false; }
            done += 1;
            self.report(10 + 60 * done / total, "Polishing");
        }
        self.mesh.compact();
        self.snap_to_reference(0.0, false); // final exact snap, no tangential motion
        if !self.validate("final", st) { return false; }

        *out = self.mesh.export_triangles();
        st.output_faces = self.mesh.alive_faces;
        st.output_verts = self.mesh.alive_verts;
        st.edge_length = l;
        st.seconds = t0.elapsed().as_secs_f64();
        true
    }
}
