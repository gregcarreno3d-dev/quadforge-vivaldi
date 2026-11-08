//! Dynamic triangle mesh stored as a "corner table".
//!
//! Corner `c = 3*face + k`. `v[c]` is its vertex, `o[c]` the opposite corner in the neighbouring face (or -1 on
//! an open border). The edge opposite corner `c` runs between `v[next(c)]` and `v[prev(c)]`. `e[c]` flags that
//! edge as a feature line (kept sharp); open borders are always features.
use crate::io::fmesh::PolyMesh;
use crate::math::{Vec3, PI};
use crate::stages::repair::{edge_key, TriSoup};

pub const V_FREE: u8 = 0;
pub const V_CREASE: u8 = 1;
pub const V_FIXED: u8 = 2;

#[inline] pub fn nxt(c: i32) -> i32 { if c % 3 == 2 { c - 2 } else { c + 1 } }
#[inline] pub fn prv(c: i32) -> i32 { if c % 3 == 0 { c + 2 } else { c - 1 } }
#[inline] pub fn face_of(c: i32) -> i32 { c / 3 }

#[derive(Clone, Copy, Debug)]
pub struct Nb { pub v: i32, pub feature: bool }

#[derive(Clone, Default)]
pub struct CMesh {
    pub p: Vec<Vec3>,
    /// Some corner of each vertex (-1 for dead vertices).
    pub vcorner: Vec<i32>,
    pub vtype: Vec<u8>,
    pub valive: Vec<bool>,
    /// Edge-length multiplier per vertex.
    pub vdens: Vec<f32>,
    pub v: Vec<i32>,
    pub o: Vec<i32>,
    pub e: Vec<u8>,
    pub falive: Vec<bool>,
    pub alive_faces: i32,
    pub alive_verts: i32,
}

impl CMesh {
    pub fn num_face_slots(&self) -> i32 { self.falive.len() as i32 }
    pub fn num_vert_slots(&self) -> i32 { self.p.len() as i32 }

    // ----------------------------------------------------------- construction
    pub fn build(soup: &TriSoup, corner_angle_deg: f64) -> Result<CMesh, String> {
        let n_f = soup.f.len();
        let n_v = soup.p.len();
        let mut m = CMesh {
            p: soup.p.clone(),
            vdens: if soup.density.is_empty() { vec![1.0; n_v] } else { soup.density.clone() },
            v: vec![0; 3 * n_f],
            o: vec![-1; 3 * n_f],
            e: vec![0; 3 * n_f],
            falive: vec![true; n_f],
            ..Default::default()
        };
        for f in 0..n_f { for k in 0..3 { m.v[3 * f + k] = soup.f[f][k]; } }
        // opposite corners through sorted directed edges
        let mut dir: Vec<(u64, i32)> = (0..3 * n_f as i32).map(|c| {
            let a = m.v[nxt(c) as usize] as u32 as u64;
            let b = m.v[prv(c) as usize] as u32 as u64;
            ((a << 32) | b, c)
        }).collect();
        dir.sort();
        for i in 1..dir.len() {
            if dir[i].0 == dir[i - 1].0 { return Err("two faces share the same directed edge".into()); }
        }
        for c in 0..3 * n_f as i32 {
            let a = m.v[prv(c) as usize] as u32 as u64;
            let b = m.v[nxt(c) as usize] as u32 as u64;
            let key = (a << 32) | b;
            if let Ok(i) = dir.binary_search_by(|x| x.0.cmp(&key)) { m.o[c as usize] = dir[i].1; }
        }
        let mut fkeys: Vec<u64> = soup.features.iter().map(|e| edge_key(e[0], e[1])).collect();
        fkeys.sort_unstable();
        for c in 0..3 * n_f as i32 {
            if m.o[c as usize] < 0 { m.e[c as usize] = 1; continue; }
            if fkeys.binary_search(&edge_key(m.v[nxt(c) as usize], m.v[prv(c) as usize])).is_ok() { m.e[c as usize] = 1; }
        }
        m.vcorner = vec![-1; n_v];
        for c in 0..3 * n_f { m.vcorner[m.v[c] as usize] = c as i32; }
        m.valive = m.vcorner.iter().map(|&c| c >= 0).collect();
        m.alive_faces = n_f as i32;
        m.alive_verts = m.valive.iter().filter(|&&a| a).count() as i32;
        m.classify_vertices(corner_angle_deg);
        Ok(m)
    }

    // ----------------------------------------------------------- topology queries
    /// Rotate around `v[c]` across the edge to `v[prev(c)]`.
    pub fn swing_left(&self, c: i32) -> i32 {
        let o = self.o[nxt(c) as usize];
        if o < 0 { -1 } else { nxt(o) }
    }
    /// Rotate across the edge to `v[next(c)]`.
    pub fn swing_right(&self, c: i32) -> i32 {
        let o = self.o[prv(c) as usize];
        if o < 0 { -1 } else { prv(o) }
    }

    /// All corners at vertex `vi`, in rotation order (starting at the border when there is one).
    /// Returns true when the fan is closed (interior vertex).
    pub fn orbit(&self, vi: i32, out: &mut Vec<i32>) -> bool {
        out.clear();
        let start = self.vcorner[vi as usize];
        let mut first = start;
        let mut closed = false;
        for _ in 0..1_000_000 {
            let n = self.swing_left(first);
            if n < 0 { break; }
            if n == start { closed = true; first = start; break; }
            first = n;
        }
        let mut c = first;
        for _ in 0..1_000_000 {
            out.push(c);
            c = self.swing_right(c);
            if c < 0 || c == first { break; }
        }
        closed
    }

    /// Neighbouring vertices of `vi` with the feature flag of the connecting edge.
    pub fn neighbors(&self, vi: i32, out: &mut Vec<Nb>, scratch: &mut Vec<i32>) -> bool {
        let closed = self.orbit(vi, scratch);
        out.clear();
        for &c in scratch.iter() {
            out.push(Nb { v: self.v[nxt(c) as usize], feature: self.e[prv(c) as usize] != 0 });
        }
        if !closed && !scratch.is_empty() {
            // the fan starts at a border edge towards v[prev(first)]; no other corner lists that neighbour
            let first = scratch[0];
            out.push(Nb { v: self.v[prv(first) as usize], feature: self.e[nxt(first) as usize] != 0 });
        }
        closed
    }

    pub fn valence(&self, vi: i32, scratch: &mut Vec<i32>) -> i32 {
        let closed = self.orbit(vi, scratch);
        scratch.len() as i32 + if closed { 0 } else { 1 }
    }

    pub fn is_boundary_vertex(&self, vi: i32, scratch: &mut Vec<i32>) -> bool { !self.orbit(vi, scratch) }

    pub fn face_normal_unnormalized(&self, f: i32) -> Vec3 {
        let b = 3 * f as usize;
        let p0 = self.p[self.v[b] as usize];
        (self.p[self.v[b + 1] as usize] - p0).cross(self.p[self.v[b + 2] as usize] - p0)
    }

    // ----------------------------------------------------------- vertex classification
    pub fn classify_vertices(&mut self, corner_angle_deg: f64) {
        let n_v = self.num_vert_slots() as usize;
        self.vtype = vec![V_FREE; n_v];
        let mut feat_count = vec![0i32; n_v];
        for c in 0..self.num_face_slots() * 3 {
            if !self.falive[face_of(c) as usize] || self.e[c as usize] == 0 { continue; }
            let oc = self.o[c as usize];
            if oc >= 0 && oc < c { continue; } // count each edge once
            feat_count[self.v[nxt(c) as usize] as usize] += 1;
            feat_count[self.v[prv(c) as usize] as usize] += 1;
        }
        let cos_limit = (corner_angle_deg * PI / 180.0).cos();
        let mut scratch = Vec::new();
        let mut nbs = Vec::new();
        for v in 0..n_v {
            if !self.valive[v] || feat_count[v] == 0 { continue; }
            if feat_count[v] != 2 { self.vtype[v] = V_FIXED; continue; }
            self.neighbors(v as i32, &mut nbs, &mut scratch);
            let mut d = [Vec3::ZERO; 2];
            let mut n = 0;
            for nb in &nbs {
                if nb.feature && n < 2 { d[n] = (self.p[nb.v as usize] - self.p[v]).normalized(); n += 1; }
            }
            self.vtype[v] = if n == 2 && d[0].dot(d[1]) > -cos_limit { V_FIXED } else { V_CREASE }; // sharp turn of the line
        }
    }

    // ----------------------------------------------------------- local operations
    pub fn add_vertex(&mut self, p: Vec3, dens: f32, vtype: u8) -> i32 {
        self.p.push(p);
        self.vcorner.push(-1);
        self.vtype.push(vtype);
        self.valive.push(true);
        self.vdens.push(dens);
        self.alive_verts += 1;
        self.p.len() as i32 - 1
    }

    pub fn add_face(&mut self) -> i32 {
        let f = self.num_face_slots();
        self.v.extend_from_slice(&[-1; 3]);
        self.o.extend_from_slice(&[-1; 3]);
        self.e.extend_from_slice(&[0; 3]);
        self.falive.push(true);
        self.alive_faces += 1;
        f
    }

    pub fn kill_face(&mut self, f: i32) {
        self.falive[f as usize] = false;
        self.alive_faces -= 1;
        for k in 0..3 {
            let c = 3 * f as usize + k;
            self.v[c] = -1;
            self.o[c] = -1;
            self.e[c] = 0;
        }
    }

    /// Flip the edge opposite corner `c`. The caller checks that it is legal.
    pub fn flip_edge(&mut self, c: i32) {
        let d = self.o[c as usize];
        let (n1, p1, n2, p2) = (nxt(c), prv(c), nxt(d), prv(d));
        let (a, b, cc, dd) = (self.v[c as usize], self.v[n1 as usize], self.v[p1 as usize], self.v[d as usize]);
        let (o_bd, o_ca) = (self.o[n2 as usize], self.o[n1 as usize]);
        let (e_bd, e_ca) = (self.e[n2 as usize], self.e[n1 as usize]);
        self.v[p1 as usize] = dd;
        self.v[p2 as usize] = a;
        self.o[n1 as usize] = n2; self.o[n2 as usize] = n1; self.e[n1 as usize] = 0; self.e[n2 as usize] = 0;
        self.o[c as usize] = o_bd; if o_bd >= 0 { self.o[o_bd as usize] = c; } self.e[c as usize] = e_bd;
        self.o[d as usize] = o_ca; if o_ca >= 0 { self.o[o_ca as usize] = d; } self.e[d as usize] = e_ca;
        self.vcorner[a as usize] = c;
        self.vcorner[b as usize] = n1;
        self.vcorner[cc as usize] = n2;
        self.vcorner[dd as usize] = d;
    }

    /// Split the edge opposite corner `c` at position `m`. Returns the new vertex.
    pub fn split_edge(&mut self, c: i32, m: Vec3, dens: f32) -> i32 {
        let d = self.o[c as usize];
        let (n1, p1) = (nxt(c), prv(c));
        let (a, b, cc) = (self.v[c as usize], self.v[n1 as usize], self.v[p1 as usize]);
        let feat = self.e[c as usize] != 0;
        let o_ca = self.o[n1 as usize];
        let e_ca = self.e[n1 as usize];
        let mv = self.add_vertex(m, dens, if feat { V_CREASE } else { V_FREE });
        let f3 = self.add_face();
        let (r0, r1, r2) = (3 * f3, 3 * f3 + 1, 3 * f3 + 2);
        self.v[r0 as usize] = a; self.v[r1 as usize] = mv; self.v[r2 as usize] = cc;
        self.v[p1 as usize] = mv;
        self.o[n1 as usize] = r2; self.o[r2 as usize] = n1; self.e[n1 as usize] = 0; self.e[r2 as usize] = 0;
        self.o[r1 as usize] = o_ca; if o_ca >= 0 { self.o[o_ca as usize] = r1; } self.e[r1 as usize] = e_ca;
        let fe = feat as u8;
        if d >= 0 {
            let (n2, p2) = (nxt(d), prv(d));
            let dd = self.v[d as usize];
            let o_bd = self.o[n2 as usize];
            let e_bd = self.e[n2 as usize];
            let f4 = self.add_face();
            let (q0, q1, q2) = (3 * f4, 3 * f4 + 1, 3 * f4 + 2);
            self.v[q0 as usize] = dd; self.v[q1 as usize] = mv; self.v[q2 as usize] = b;
            self.v[p2 as usize] = mv;
            self.o[c as usize] = q0; self.o[q0 as usize] = c; self.e[c as usize] = fe; self.e[q0 as usize] = fe;
            self.o[r0 as usize] = d; self.o[d as usize] = r0; self.e[r0 as usize] = fe; self.e[d as usize] = fe;
            self.o[n2 as usize] = q2; self.o[q2 as usize] = n2; self.e[n2 as usize] = 0; self.e[q2 as usize] = 0;
            self.o[q1 as usize] = o_bd; if o_bd >= 0 { self.o[o_bd as usize] = q1; } self.e[q1 as usize] = e_bd;
            self.vcorner[a as usize] = c;
            self.vcorner[b as usize] = n1;
            self.vcorner[cc as usize] = n2;
            self.vcorner[dd as usize] = d;
            self.vcorner[mv as usize] = p1;
        } else {
            self.o[c as usize] = -1; self.e[c as usize] = 1;
            self.o[r0 as usize] = -1; self.e[r0 as usize] = 1;
            self.vcorner[a as usize] = c;
            self.vcorner[b as usize] = n1;
            self.vcorner[cc as usize] = r2;
            self.vcorner[mv as usize] = p1;
        }
        mv
    }

    /// Collapse the edge opposite corner `c`: vertex `rem` is merged into `keep` (placed at `new_pos`).
    pub fn collapse_edge(&mut self, c: i32, keep: i32, rem: i32, new_pos: Vec3) {
        let mut rem_corners = Vec::new();
        let mut keep_corners = Vec::new();
        self.orbit(rem, &mut rem_corners);
        self.orbit(keep, &mut keep_corners);
        let d = self.o[c as usize];
        let (n1, p1) = (nxt(c), prv(c));
        let a = self.v[c as usize];
        let (o_ab, o_ca) = (self.o[p1 as usize], self.o[n1 as usize]);
        let (e_ab, e_ca) = (self.e[p1 as usize], self.e[n1 as usize]);
        let (mut dd, mut o_bd, mut o_dc, mut e_bd, mut e_dc) = (-1i32, -1i32, -1i32, 0u8, 0u8);
        if d >= 0 {
            let (n2, p2) = (nxt(d), prv(d));
            dd = self.v[d as usize];
            o_bd = self.o[n2 as usize];
            o_dc = self.o[p2 as usize];
            e_bd = self.e[n2 as usize];
            e_dc = self.e[p2 as usize];
        }
        self.glue(o_ab, e_ab, o_ca, e_ca);
        self.kill_face(face_of(c));
        if d >= 0 {
            self.glue(o_bd, e_bd, o_dc, e_dc);
            self.kill_face(face_of(d));
        }
        for &k in &rem_corners { if self.falive[face_of(k) as usize] { self.v[k as usize] = keep; } }
        self.p[keep as usize] = new_pos;
        self.valive[rem as usize] = false;
        self.vcorner[rem as usize] = -1;
        self.alive_verts -= 1;
        // keep a live corner for every vertex that touched the removed faces
        self.vcorner[keep as usize] = -1;
        for &k in &keep_corners {
            if self.falive[face_of(k) as usize] { self.vcorner[keep as usize] = k; break; }
        }
        if self.vcorner[keep as usize] < 0 {
            for &k in &rem_corners {
                if self.falive[face_of(k) as usize] { self.vcorner[keep as usize] = k; break; }
            }
        }
        let va = self.vcorner[a as usize];
        if va >= 0 && !self.falive[face_of(va) as usize] {
            if o_ab >= 0 { self.vcorner[a as usize] = prv(o_ab); }
            else if o_ca >= 0 { self.vcorner[a as usize] = nxt(o_ca); }
        }
        if dd >= 0 {
            let vd = self.vcorner[dd as usize];
            if vd < 0 || !self.falive[face_of(vd) as usize] {
                if o_bd >= 0 { self.vcorner[dd as usize] = nxt(o_bd); }
                else if o_dc >= 0 { self.vcorner[dd as usize] = prv(o_dc); }
            }
        }
    }

    /// Remove dead faces/vertices and renumber. Keeps relative order.
    pub fn compact(&mut self) {
        let n_f = self.num_face_slots() as usize;
        let n_v = self.num_vert_slots() as usize;
        let mut fmap = vec![-1i32; n_f];
        let mut vmap = vec![-1i32; n_v];
        let (mut nf, mut nv) = (0usize, 0usize);
        for f in 0..n_f { if self.falive[f] { fmap[f] = nf as i32; nf += 1; } }
        for v in 0..n_v { if self.valive[v] { vmap[v] = nv as i32; nv += 1; } }
        let mut v2 = vec![0i32; 3 * nf];
        let mut o2 = vec![-1i32; 3 * nf];
        let mut e2 = vec![0u8; 3 * nf];
        for f in 0..n_f {
            if !self.falive[f] { continue; }
            for k in 0..3 {
                let c = 3 * f + k;
                let c2 = 3 * fmap[f] as usize + k;
                v2[c2] = vmap[self.v[c] as usize];
                e2[c2] = self.e[c];
                let oc = self.o[c];
                o2[c2] = if oc < 0 { -1 } else { 3 * fmap[face_of(oc) as usize] + oc % 3 };
            }
        }
        let mut p2 = vec![Vec3::ZERO; nv];
        let mut vc2 = vec![0i32; nv];
        let mut vt2 = vec![0u8; nv];
        let mut vd2 = vec![0f32; nv];
        for v in 0..n_v {
            if !self.valive[v] { continue; }
            let c = self.vcorner[v];
            let nvv = vmap[v] as usize;
            p2[nvv] = self.p[v];
            vc2[nvv] = 3 * fmap[face_of(c) as usize] + c % 3;
            vt2[nvv] = self.vtype[v];
            vd2[nvv] = self.vdens[v];
        }
        self.p = p2; self.vcorner = vc2; self.vtype = vt2; self.vdens = vd2;
        self.valive = vec![true; nv];
        self.v = v2; self.o = o2; self.e = e2;
        self.falive = vec![true; nf];
        self.alive_faces = nf as i32;
        self.alive_verts = nv as i32;
    }

    /// Full consistency check; returns an empty string when the mesh is valid.
    pub fn check(&self) -> String {
        let n_f = self.num_face_slots();
        let mut corners_per_vertex = vec![0i32; self.p.len()];
        let mut live_faces = 0;
        for f in 0..n_f {
            if !self.falive[f as usize] { continue; }
            live_faces += 1;
            for k in 0..3 {
                let c = 3 * f + k;
                let v = self.v[c as usize];
                if v < 0 || v >= self.num_vert_slots() || !self.valive[v as usize] { return format!("corner {c} has a dead vertex"); }
                corners_per_vertex[v as usize] += 1;
                let (vn, vp) = (self.v[nxt(c) as usize], self.v[prv(c) as usize]);
                if vn == vp || v == vn { return format!("degenerate face {f}"); }
                let d = self.o[c as usize];
                if self.e[c as usize] != 0 && (self.vtype[vn as usize] == V_FREE || self.vtype[vp as usize] == V_FREE) {
                    return format!("feature edge touches a free vertex at corner {c}");
                }
                if d < 0 {
                    if self.e[c as usize] == 0 { return "border edge not flagged as feature".into(); }
                    continue;
                }
                if !self.falive[face_of(d) as usize] { return "opposite corner in a dead face".into(); }
                if self.o[d as usize] != c { return format!("opposite is not symmetric at corner {c}"); }
                if vn != self.v[prv(d) as usize] || vp != self.v[nxt(d) as usize] { return format!("opposite edge mismatch at corner {c}"); }
                if self.e[c as usize] != self.e[d as usize] { return format!("feature flag mismatch at corner {c}"); }
            }
        }
        if live_faces != self.alive_faces { return "alive face counter is wrong".into(); }
        let mut live_verts = 0;
        let mut scratch = Vec::new();
        for v in 0..self.num_vert_slots() {
            if !self.valive[v as usize] { continue; }
            live_verts += 1;
            let c = self.vcorner[v as usize];
            if c < 0 || !self.falive[face_of(c) as usize] || self.v[c as usize] != v { return format!("bad vertex corner for vertex {v}"); }
            self.orbit(v, &mut scratch);
            if scratch.len() as i32 != corners_per_vertex[v as usize] {
                return format!("vertex {v} has more than one fan (non-manifold)");
            }
        }
        if live_verts != self.alive_verts { return "alive vertex counter is wrong".into(); }
        String::new()
    }

    /// Export the live faces as indexed triangles (vertices renumbered densely).
    pub fn export_triangles(&self) -> PolyMesh {
        let mut vmap = vec![-1i32; self.p.len()];
        let mut nv = 0;
        for v in 0..self.p.len() { if self.valive[v] { vmap[v] = nv; nv += 1; } }
        let mut out = PolyMesh::default();
        out.p = vec![Vec3::ZERO; nv as usize];
        for v in 0..self.p.len() { if self.valive[v] { out.p[vmap[v] as usize] = self.p[v]; } }
        for f in 0..self.falive.len() {
            if !self.falive[f] { continue; }
            out.sizes.push(3);
            for k in 0..3 { out.idx.push(vmap[self.v[3 * f + k] as usize]); }
        }
        out
    }

    fn glue(&mut self, x: i32, ex: u8, y: i32, ey: u8) {
        if x >= 0 && y >= 0 {
            self.o[x as usize] = y;
            self.o[y as usize] = x;
            self.e[x as usize] = ex | ey;
            self.e[y as usize] = ex | ey;
        } else if x >= 0 {
            self.o[x as usize] = -1;
            self.e[x as usize] = 1;
        } else if y >= 0 {
            self.o[y as usize] = -1;
            self.e[y as usize] = 1;
        }
    }
}
