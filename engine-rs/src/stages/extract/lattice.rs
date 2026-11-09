//! From a parametrization (or position field) to polygons: lattice cells, the cell graph and its faces.
//!
//! Every mesh vertex snaps to its nearest lattice node. Vertices on the same node are merged into one cell;
//! vertices on neighbouring nodes create an edge. Where the parametrization stretches the grid, triangles are
//! subdivided first so that every lattice point gets a cell. Faces are traced in the rotation system of the cell
//! graph (loops of four become quads); small holes left by loops that did not close are filled.
//!
//! Work areas: `snap_and_link` · `build_cells` · `trace_faces` · `fill_holes` · `relax_cells`.
use super::{ExtractStats, VertexFrames};
use crate::io::fmesh::PolyMesh;
use crate::math::Vec3;
use crate::mesh::bvh::{SegmentIndex, SurfaceIndex};
use crate::mesh::cmesh::CMesh;
use crate::stages::param::ParamResult;
use std::collections::{BTreeMap, HashMap, HashSet};

pub struct LatticeContext<'a> {
    pub m: &'a CMesh,
    pub reference: &'a SurfaceIndex,
    pub feat: &'a SegmentIndex,
    pub vf: &'a VertexFrames,
    pub rho: f64,
    pub param: Option<&'a ParamResult>,
    pub density_power: f64,
    pub smooth: i32,
}

fn dbg() -> bool { std::env::var_os("FORGE_DBG").is_some() }
fn env_set(name: &str) -> bool { std::env::var_os(name).is_some() }

const K_MERGE: f64 = 0.6;
const K_LO: f64 = 0.4;
const K_HI: f64 = 1.6;

struct Dsu(Vec<usize>);
impl Dsu {
    fn find(&mut self, mut x: usize) -> usize {
        while self.0[x] != x { self.0[x] = self.0[self.0[x]]; x = self.0[x]; }
        x
    }
    fn union(&mut self, a: usize, b: usize) { let (ra, rb) = (self.find(a), self.find(b)); self.0[ra] = rb; }
}

fn ekey2(a: i32, b: i32) -> u64 {
    let (a, b) = if a > b { (b, a) } else { (a, b) };
    ((a as u32 as u64) << 32) | b as u32 as u64
}
fn dkey(a: i32, b: i32) -> u64 { ((a as u32 as u64) << 32) | b as u32 as u64 }

#[derive(Clone, Copy)]
struct Tri { v: [i32; 3], uv: [[f64; 2]; 3], f: usize }

/// Vertices that do not exist in the mesh (midpoints added by the lattice subdivision), index = n_vs + i.
#[derive(Default)]
struct Virtual { p: Vec<Vec3>, n: Vec<Vec3>, scale: Vec<f64> }

/// Vertices snapped to lattice nodes, merged vertex groups (`dsu`) and links between neighbouring nodes.
struct Snapped {
    s: Vec<Vec3>,
    dsu: Dsu,
    cell_ids: Vec<i32>,
    exn: Vec<Vec3>,
    links: Vec<(i32, i32)>,
}

fn snap_and_link(c: &LatticeContext) -> Snapped {
    let m = c.m;
    let vf = c.vf;
    let rho = c.rho;
    let n_vs = m.num_vert_slots() as usize;
    let n_f = m.num_face_slots() as usize;
    let node = |o: Vec3, q: Vec3, r: Vec3, x: Vec3| {
        let d = x - o;
        o + q * ((d.dot(q) / rho).round() * rho) + r * ((d.dot(r) / rho).round() * rho)
    };
    let mut s = vec![Vec3::ZERO; n_vs];
    for &v in &vf.ids { let v = v as usize; s[v] = node(vf.o[v], vf.q[v], vf.r[v], m.p[v]); }
    let mut dsu = Dsu((0..n_vs).collect());
    let mut cell_ids = vf.ids.clone();
    let mut exn: Vec<Vec3> = Vec::new();
    let mut links: Vec<(i32, i32)> = Vec::new();
    if let Some(pr) = c.param {
        for &v in &vf.ids {
            let v = v as usize;
            let cc = m.vcorner[v] as usize;
            let (f, k) = (cc / 3, cc % 3);
            let (u, w) = (pr.uv[f][k][0], pr.uv[f][k][1]);
            let mut rl = rho * if m.vdens.is_empty() { 1.0 } else { m.vdens[v] as f64 };
            if pr.density_power != 1.0 && !m.vdens.is_empty() { rl = rho * (m.vdens[v] as f64).powf(pr.density_power); }
            s[v] = m.p[v] + pr.u[f] * ((u.round() - u) * rl) + pr.v[f] * ((w.round() - w) * rl);
        }
        // Where the parametrization stretches the grid, the mesh has fewer vertices than the lattice has points and
        // some points would get no cell (holes). Subdivide the triangles whose edges span a lattice step; the map is
        // linear inside a triangle, so the new vertices need no new solve.
        let mut tris: Vec<Tri> = Vec::new();
        for f in 0..n_f {
            if !m.falive[f] { continue; }
            let mut t = Tri { v: [0; 3], uv: [[0.0; 2]; 3], f };
            for k in 0..3 { t.v[k] = m.v[3 * f + k]; t.uv[k] = pr.uv[f][k]; }
            tris.push(t);
        }
        let mut ex = Virtual::default();
        let mut mid_of: HashMap<u64, i32> = HashMap::new();
        let base_scale = |v: i32, ex: &Virtual| -> f64 {
            if (v as usize) < n_vs { if m.vdens.is_empty() { 1.0 } else { m.vdens[v as usize] as f64 } } else { ex.scale[v as usize - n_vs] }
        };
        let pos_of = |v: i32, ex: &Virtual| if (v as usize) < n_vs { m.p[v as usize] } else { ex.p[v as usize - n_vs] };
        let nrm_of = |v: i32, ex: &Virtual, vf: &VertexFrames| if (v as usize) < n_vs { vf.n[v as usize] } else { ex.n[v as usize - n_vs] };
        let mut added_tris: i64 = 0;
        if !env_set("FORGE_NOSUBDIV") {
            for _round in 0..3 {
                let mut split: HashSet<u64> = HashSet::new();
                for t in &tris {
                    for k in 0..3 {
                        let k2 = (k + 1) % 3;
                        if (t.uv[k2][0] - t.uv[k][0]).abs().max((t.uv[k2][1] - t.uv[k][1]).abs()) > 0.8 { split.insert(ekey2(t.v[k], t.v[k2])); }
                    }
                }
                if split.is_empty() { break; }
                let mut next: Vec<Tri> = Vec::with_capacity(tris.len() + tris.len() / 8);
                for t in &tris {
                    let mut mm = [-1i32; 3];
                    let mut muv = [[0.0f64; 2]; 3];
                    let mut nsplit = 0;
                    for k in 0..3 {
                        let k2 = (k + 1) % 3;
                        let key = ekey2(t.v[k], t.v[k2]);
                        if !split.contains(&key) { continue; }
                        nsplit += 1;
                        muv[k] = [0.5 * (t.uv[k][0] + t.uv[k2][0]), 0.5 * (t.uv[k][1] + t.uv[k2][1])];
                        let id = match mid_of.get(&key) {
                            Some(&id) => id,
                            None => {
                                let mut pm = (pos_of(t.v[k], &ex) + pos_of(t.v[k2], &ex)) * 0.5;
                                let nm = (nrm_of(t.v[k], &ex, vf) + nrm_of(t.v[k2], &ex, vf)).normalized();
                                let h = c.reference.closest_any(pm);
                                if h.tri >= 0 { pm = h.p; }
                                let sc = 0.5 * (base_scale(t.v[k], &ex) + base_scale(t.v[k2], &ex));
                                ex.p.push(pm);
                                ex.n.push(nm);
                                ex.scale.push(sc);
                                let id = (n_vs + ex.p.len() - 1) as i32;
                                s.push(Vec3::ZERO);
                                dsu.0.push(id as usize);
                                cell_ids.push(id);
                                // snapped position from this triangle's frame
                                let mut scl = ex.scale[id as usize - n_vs];
                                if pr.density_power != 1.0 { scl = scl.powf(pr.density_power); }
                                let rl = rho * scl;
                                s[id as usize] = pm + pr.u[t.f] * ((muv[k][0].round() - muv[k][0]) * rl) + pr.v[t.f] * ((muv[k][1].round() - muv[k][1]) * rl);
                                mid_of.insert(key, id);
                                id
                            }
                        };
                        mm[k] = id;
                    }
                    let mut push = |a: i32, ua: [f64; 2], b: i32, ub: [f64; 2], cc: i32, uc: [f64; 2]| {
                        next.push(Tri { v: [a, b, cc], uv: [ua, ub, uc], f: t.f });
                    };
                    if nsplit == 0 { push(t.v[0], t.uv[0], t.v[1], t.uv[1], t.v[2], t.uv[2]); continue; }
                    // rotate so that the pattern is canonical: the unsplit (or the only split) edge is edge 0
                    let mut r = 0usize;
                    if nsplit == 1 { while mm[r] < 0 { r += 1; } }
                    else if nsplit == 2 { while mm[(r + 2) % 3] >= 0 || mm[r] < 0 || mm[(r + 1) % 3] < 0 { r += 1; } }
                    let (i0, i1, i2) = (r, (r + 1) % 3, (r + 2) % 3);
                    let (v0, v1, v2) = (t.v[i0], t.v[i1], t.v[i2]);
                    let (u0, u1, u2) = (t.uv[i0], t.uv[i1], t.uv[i2]);
                    let (m0, m1, m2) = (mm[i0], mm[i1], mm[i2]);
                    let (mu0, mu1, mu2) = (muv[i0], muv[i1], muv[i2]);
                    if nsplit == 3 {
                        push(v0, u0, m0, mu0, m2, mu2);
                        push(m0, mu0, v1, u1, m1, mu1);
                        push(m2, mu2, m1, mu1, v2, u2);
                        push(m0, mu0, m1, mu1, m2, mu2);
                    } else if nsplit == 2 { // edges 0 and 1 are split, edge 2 is not
                        push(m0, mu0, v1, u1, m1, mu1);
                        push(v0, u0, m0, mu0, m1, mu1);
                        push(v0, u0, m1, mu1, v2, u2);
                    } else { // only edge 0 is split
                        push(v0, u0, m0, mu0, v2, u2);
                        push(m0, mu0, v1, u1, v2, u2);
                    }
                }
                added_tris += next.len() as i64 - tris.len() as i64;
                tris = next;
            }
        }
        if dbg() { eprintln!("lattice subdivision: {} virtual vertices, {} extra triangles", ex.p.len(), added_tris); }
        exn = ex.n;
        for t in &tris {
            for k in 0..3 {
                let k2 = (k + 1) % 3;
                let (a, b) = (t.v[k], t.v[k2]);
                let da = t.uv[k2][0].round() as i32 - t.uv[k][0].round() as i32;
                let db = t.uv[k2][1].round() as i32 - t.uv[k][1].round() as i32;
                if da == 0 && db == 0 { dsu.union(a as usize, b as usize); }
                else if da.abs() + db.abs() == 1 { links.push((a, b)); }
            }
        }
    } else {
        for &i in &vf.ids {
            let i = i as usize;
            for e in vf.adj_start[i]..vf.adj_start[i + 1] {
                let j = vf.adj[e] as usize;
                if j < i { continue; }
                let d = s[j] - s[i];
                let dl = d.length();
                if dl < K_MERGE * rho { dsu.union(i, j); continue; }
                let a = d.dot(vf.q[i]) / rho;
                let b = d.dot(vf.r[i]) / rho;
                let (ra, rb) = (a.round() as i32, b.round() as i32);
                if ra.abs() + rb.abs() == 1 && dl > K_LO * rho && dl < K_HI * rho { links.push((i as i32, j as i32)); }
            }
        }
    }
    Snapped { s, dsu, cell_ids, exn, links }
}

/// Cells (merged vertex groups) with position, normal, feature class and the graph between them.
struct Cells {
    cell_of: HashMap<usize, usize>,
    pos: Vec<Vec3>,
    n: Vec<Vec3>,
    feat: Vec<u8>,
    nbr: Vec<Vec<usize>>,
}

fn build_cells(c: &LatticeContext, sn: &mut Snapped, st: &mut ExtractStats) -> Cells {
    let m = c.m;
    let vf = c.vf;
    let n_vs = m.num_vert_slots() as usize;
    let mut cell_of: HashMap<usize, usize> = HashMap::new();
    let (mut cpos, mut cn, mut cnt, mut cfeat): (Vec<Vec3>, Vec<Vec3>, Vec<i32>, Vec<u8>) = (vec![], vec![], vec![], vec![]);
    let mut ccorner: Vec<i32> = Vec::new();
    for &v in &sn.cell_ids {
        let r = sn.dsu.find(v as usize);
        let cc = *cell_of.entry(r).or_insert_with(|| {
            cpos.push(Vec3::ZERO); cn.push(Vec3::ZERO); cnt.push(0); cfeat.push(0); ccorner.push(-1);
            cpos.len() - 1
        });
        cpos[cc] += sn.s[v as usize];
        cn[cc] += if (v as usize) < n_vs { vf.n[v as usize] } else { sn.exn[v as usize - n_vs] };
        cnt[cc] += 1;
        if (v as usize) < n_vs && vf.feat_flag[v as usize] > cfeat[cc] { cfeat[cc] = vf.feat_flag[v as usize]; }
        if (v as usize) < n_vs && vf.feat_flag[v as usize] == 2 && ccorner[cc] < 0 { ccorner[cc] = v; }
    }
    let n_c = cpos.len();
    for cc in 0..n_c {
        cpos[cc] = cpos[cc] / cnt[cc] as f64;
        cn[cc] = cn[cc].normalized();
        if cfeat[cc] == 2 && ccorner[cc] >= 0 { cpos[cc] = m.p[ccorner[cc] as usize]; }
        else if cfeat[cc] >= 1 && !c.feat.is_empty() {
            let h = c.feat.closest(cpos[cc]);
            if h.seg >= 0 { cpos[cc] = h.p; }
        }
    }
    st.cells = n_c as i32;
    let mut nbr: Vec<Vec<usize>> = vec![Vec::new(); n_c];
    let mut seen: HashSet<u64> = HashSet::new();
    for &(la, lb) in &sn.links {
        let a = cell_of[&sn.dsu.find(la as usize)];
        let b = cell_of[&sn.dsu.find(lb as usize)];
        if a == b { continue; }
        if !seen.insert(ekey2(a as i32, b as i32)) { continue; }
        nbr[a].push(b);
        nbr[b].push(a);
        st.edges += 1;
    }
    // prune dangling cells
    let mut stack: Vec<usize> = (0..n_c).filter(|&cc| nbr[cc].len() < 2).collect();
    let mut dead = vec![false; n_c];
    while let Some(cc) = stack.pop() {
        if dead[cc] { continue; }
        dead[cc] = true;
        let list = std::mem::take(&mut nbr[cc]);
        for d in list {
            nbr[d].retain(|&x| x != cc);
            if !dead[d] && nbr[d].len() < 2 { stack.push(d); }
        }
    }
    Cells { cell_of, pos: cpos, n: cn, feat: cfeat, nbr }
}

/// Faces from the rotation system: neighbours sorted counter clockwise around the cell normal, then loops traced.
fn trace_faces(cells: &mut Cells, st: &mut ExtractStats) -> Vec<Vec<usize>> {
    let n_c = cells.pos.len();
    for cc in 0..n_c {
        if cells.nbr[cc].is_empty() { continue; }
        let n = cells.n[cc];
        let helper = if n.x.abs() < 0.9 { Vec3::new(1.0, 0.0, 0.0) } else { Vec3::new(0.0, 1.0, 0.0) };
        let t1 = n.cross(helper).normalized();
        let t2 = n.cross(t1);
        let mut key: Vec<(f64, usize)> = cells.nbr[cc].iter().map(|&d| {
            let e = cells.pos[d] - cells.pos[cc];
            (e.dot(t2).atan2(e.dot(t1)), d)
        }).collect();
        key.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        for (k, kv) in key.iter().enumerate() { cells.nbr[cc][k] = kv.1; }
    }
    let nbr = &cells.nbr;
    let pos_in = |cc: usize, d: usize| nbr[cc].iter().position(|&x| x == d).unwrap_or(nbr[cc].len());
    // visited half edges: (a, index of b in nbr[a])
    let mut used: Vec<Vec<bool>> = nbr.iter().map(|v| vec![false; v.len()]).collect();
    let mut faces: Vec<Vec<usize>> = Vec::new();
    for a in 0..n_c {
        for s in 0..nbr[a].len() {
            if used[a][s] { continue; }
            let mut lp: Vec<usize> = Vec::new();
            let (mut u, mut k) = (a, s);
            let mut ok = true;
            for guard in 0..64 {
                if used[u][k] { ok = false; break; }
                used[u][k] = true;
                lp.push(u);
                let v = nbr[u][k];
                // at v, continue with the neighbour just before u in counter clockwise order
                let pu = pos_in(v, u);
                let deg = nbr[v].len();
                let nk = (pu + deg - 1) % deg;
                u = v;
                k = nk;
                if u == a && k == s { break; }
                if guard == 63 { ok = false; }
            }
            if !ok || lp.len() < 3 {
                st.dropped_loops += 1;
                if dbg() { eprintln!("dropped loop (open walk, {} cells)", lp.len()); }
                continue;
            }
            // keep counter clockwise loops only
            let mut ctr = Vec3::ZERO;
            for &cc in &lp { ctr += cells.pos[cc]; }
            ctr = ctr / lp.len() as f64;
            let mut nsum = Vec3::ZERO;
            for q in 0..lp.len() {
                let p0 = cells.pos[lp[q]] - ctr;
                let p1 = cells.pos[lp[(q + 1) % lp.len()]] - ctr;
                nsum += p0.cross(p1);
            }
            let mut navg = Vec3::ZERO;
            for &cc in &lp { navg += cells.n[cc]; }
            let area = nsum.dot(navg);
            if area <= 0.0 || lp.len() > 8 {
                st.dropped_loops += 1;
                if dbg() { eprintln!("dropped loop ({}, {} cells)", if area <= 0.0 { "reversed" } else { "long" }, lp.len()); }
                continue;
            }
            faces.push(lp);
        }
    }
    faces
}

/// Fill the small holes left by loops that did not close (never along real borders).
fn fill_holes(faces: &mut Vec<Vec<usize>>, st: &mut ExtractStats) {
    let mut has_edge: HashSet<u64> = HashSet::new();
    for f in faces.iter() { for k in 0..f.len() { has_edge.insert(dkey(f[k] as i32, f[(k + 1) % f.len()] as i32)); } }
    let mut out_: BTreeMap<usize, Vec<usize>> = BTreeMap::new(); // boundary edges by start cell
    for f in faces.iter() {
        for k in 0..f.len() {
            let (a, b) = (f[k], f[(k + 1) % f.len()]);
            if !has_edge.contains(&dkey(b as i32, a as i32)) { out_.entry(a).or_default().push(b); }
        }
    }
    let mut done: HashSet<u64> = HashSet::new();
    const MAX_HOLE: usize = 64;
    let mut extra: Vec<Vec<usize>> = Vec::new();
    for (&a0, list) in &out_ {
        for &b0 in list {
            if done.contains(&dkey(a0 as i32, b0 as i32)) { continue; }
            let mut lp = vec![a0];
            let (mut a, mut b) = (a0, b0);
            let mut ok = true;
            for guard in 0..=MAX_HOLE {
                done.insert(dkey(a as i32, b as i32));
                if b == a0 { break; }
                lp.push(b);
                let nx = out_.get(&b).and_then(|l| l.iter().copied().find(|&c| !done.contains(&dkey(b as i32, c as i32))));
                match nx { Some(n) => { a = b; b = n; } None => { ok = false; break; } }
                if guard == MAX_HOLE { ok = false; }
            }
            if !ok || lp.len() < 3 || lp.len() > MAX_HOLE { continue; }
            lp.reverse();
            extra.push(lp);
        }
    }
    for l in extra { faces.push(l); st.filled += 1; }
}

/// Relax the cell positions on the surface.
fn relax_cells(c: &LatticeContext, cells: &mut Cells, faces: &[Vec<usize>]) {
    let n_c = cells.pos.len();
    let mut in_face = vec![false; n_c];
    for f in faces { for &cc in f { in_face[cc] = true; } }
    for _ in 0..c.smooth {
        let mut np = cells.pos.clone();
        for cc in 0..n_c {
            if !in_face[cc] || cells.nbr[cc].is_empty() { continue; }
            let mut avg = Vec3::ZERO;
            let mut k = 0;
            for &d in &cells.nbr[cc] {
                if in_face[d] && (cells.feat[cc] == 0 || cells.feat[d] >= 1) { avg += cells.pos[d]; k += 1; }
            }
            if k == 0 { continue; }
            avg = avg / k as f64;
            let p = cells.pos[cc] + (avg - cells.pos[cc]) * 0.5;
            if cells.feat[cc] == 2 { continue; }
            if cells.feat[cc] == 1 {
                let h = c.feat.closest(p);
                np[cc] = if h.seg >= 0 { h.p } else { p };
                continue;
            }
            let h = c.reference.closest(p, Some(cells.n[cc]), 0.0);
            np[cc] = if h.tri >= 0 { h.p } else { p };
        }
        cells.pos = np;
    }
}

/// The polygon mesh of the grid and the feature class (0 free, 1 line, 2 corner) of each of its vertices.
pub fn extract_polygons(c: &LatticeContext, st: &mut ExtractStats) -> Result<(PolyMesh, Vec<u8>), String> {
    let mut sn = snap_and_link(c);
    let mut cells = build_cells(c, &mut sn, st);
    let mut faces = trace_faces(&mut cells, st);
    fill_holes(&mut faces, st);
    relax_cells(c, &mut cells, &faces);
    let _ = &cells.cell_of;
    let mut remap = vec![-1i32; cells.pos.len()];
    let mut out = PolyMesh::default();
    let mut feat: Vec<u8> = Vec::new();
    for f in &faces {
        for &cc in f {
            if remap[cc] < 0 { remap[cc] = out.p.len() as i32; out.p.push(cells.pos[cc]); feat.push(cells.feat[cc]); }
        }
        out.sizes.push(f.len() as i32);
        for &cc in f { out.idx.push(remap[cc]); }
    }
    Ok((out, feat))
}
