//! Clean-up of grid results on surfaces whose borders were closed with caps (see `capping`):
//! drop the quads that lie on a cap, then weld the very short edges that appear along the border line.
use crate::io::fmesh::PolyMesh;
use crate::math::Vec3;
use crate::mesh::bvh::{SegmentIndex, SurfaceIndex};
use crate::stages::capping::CapInfo;
use std::collections::{HashMap, HashSet};

fn ukey(a: i32, b: i32) -> u64 {
    let (a, b) = if a > b { (b, a) } else { (a, b) };
    ((a as u32 as u64) << 32) | b as u32 as u64
}

struct Dsu(Vec<usize>);
impl Dsu {
    fn find(&mut self, mut x: usize) -> usize {
        while self.0[x] != x { self.0[x] = self.0[self.0[x]]; x = self.0[x]; }
        x
    }
}

/// Drop the quads that lie on a cap. Vertices on the border line itself are neutral; a quad goes if one of its
/// vertices is on the cap side, or if all lie on the line.
pub fn drop_cap_quads(quads: &PolyMesh, lines: &SegmentIndex, cap_idx: &SurfaceIndex, info: &CapInfo, rho: f64) -> PolyMesh {
    let mut side = vec![0u8; quads.p.len()]; // 0 real, 1 cap, 2 on the border line
    for v in 0..quads.p.len() {
        let hl = lines.closest(quads.p[v]);
        if hl.seg >= 0 && hl.p.distance(quads.p[v]) < 0.08 * rho { side[v] = 2; continue; }
        let h = cap_idx.closest_any(quads.p[v]);
        side[v] = if h.tri >= 0 && info.removable_face[h.tri as usize] { 1 } else { 0 };
    }
    let mut remap = vec![-1i32; quads.p.len()];
    let mut cut = PolyMesh::default();
    let mut at = 0usize;
    for &sz in &quads.sizes {
        let sz = sz as usize;
        let (mut any_cap, mut any_real) = (false, false);
        for k in 0..sz {
            match side[quads.idx[at + k] as usize] { 1 => any_cap = true, 0 => any_real = true, _ => {} }
        }
        if !any_cap && any_real {
            cut.sizes.push(sz as i32);
            for k in 0..sz {
                let v = quads.idx[at + k] as usize;
                if remap[v] < 0 { remap[v] = cut.p.len() as i32; cut.p.push(quads.p[v]); }
                cut.idx.push(remap[v]);
            }
        }
        at += sz;
    }
    cut
}

/// The first row along a border can end up squeezed onto the line: weld very short edges. The welded mesh is
/// kept only if it is still a clean manifold.
pub fn weld_short_edges(quads: PolyMesh, lines: &SegmentIndex, rho: f64) -> PolyMesh {
    let dbg = std::env::var_os("FORGE_DBG").is_some();
    let mut seen: HashSet<u64> = HashSet::new();
    let mut sum = 0.0;
    let mut edge_list: Vec<[i32; 2]> = Vec::new();
    let mut a1 = 0usize;
    for &sz in &quads.sizes {
        let sz = sz as usize;
        for k in 0..sz {
            let (a, b) = (quads.idx[a1 + k], quads.idx[a1 + (k + 1) % sz]);
            if seen.insert(ukey(a, b)) { edge_list.push([a, b]); sum += quads.p[a as usize].distance(quads.p[b as usize]); }
        }
        a1 += sz;
    }
    let thr = if edge_list.is_empty() { 0.0 } else { 0.2 * sum / edge_list.len() as f64 };
    let mut par = Dsu((0..quads.p.len()).collect());
    let mut welded = 0;
    for e in &edge_list {
        if quads.p[e[0] as usize].distance(quads.p[e[1] as usize]) < thr {
            let (ra, rb) = (par.find(e[0] as usize), par.find(e[1] as usize));
            if ra != rb { par.0[ra] = rb; welded += 1; }
        }
    }
    {   // vertices on the border line that sit on top of each other (not necessarily joined by an edge)
        let on_line: Vec<usize> = (0..quads.p.len()).filter(|&v| {
            let hl = lines.closest(quads.p[v]);
            hl.seg >= 0 && hl.p.distance(quads.p[v]) < 0.08 * rho
        }).collect();
        if on_line.len() <= 6000 {
            for i in 0..on_line.len() {
                for j in i + 1..on_line.len() {
                    if quads.p[on_line[i]].distance(quads.p[on_line[j]]) < thr {
                        let (ra, rb) = (par.find(on_line[i]), par.find(on_line[j]));
                        if ra != rb { par.0[ra] = rb; welded += 1; }
                    }
                }
            }
        }
    }
    if welded == 0 { return quads; }
    let n = quads.p.len();
    let mut acc = vec![Vec3::ZERO; n];
    let mut cnt = vec![0i32; n];
    for i in 0..n { let r = par.find(i); acc[r] += quads.p[i]; cnt[r] += 1; }
    let mut w = PolyMesh::default();
    let mut rid = vec![-1i32; n];
    let mut a2 = 0usize;
    for &sz in &quads.sizes {
        let sz = sz as usize;
        let mut poly: Vec<usize> = Vec::new();
        for k in 0..sz {
            let r = par.find(quads.idx[a2 + k] as usize);
            if poly.last() != Some(&r) { poly.push(r); }
        }
        if poly.len() > 1 && poly.first() == poly.last() { poly.pop(); }
        a2 += sz;
        let mut uniq: Vec<usize> = Vec::new();
        for &r in &poly { if !uniq.contains(&r) { uniq.push(r); } }
        if uniq.len() != poly.len() || poly.len() < 3 { continue; }
        w.sizes.push(poly.len() as i32);
        for &r in &poly {
            if rid[r] < 0 { rid[r] = w.p.len() as i32; w.p.push(acc[r] / cnt[r] as f64); }
            w.idx.push(rid[r]);
        }
    }
    // clean: no directed edge used twice
    let mut dir: HashMap<u64, i32> = HashMap::new();
    let mut clean = true;
    let mut a3 = 0usize;
    for &sz in &w.sizes {
        let sz = sz as usize;
        for k in 0..sz {
            let (a, b) = (w.idx[a3 + k] as u32 as u64, w.idx[a3 + (k + 1) % sz] as u32 as u64);
            let c = dir.entry((a << 32) | b).or_insert(0);
            *c += 1;
            if *c > 1 { clean = false; }
        }
        a3 += sz;
    }
    if dbg { eprintln!("border: welded {welded} short edges, clean {clean}"); }
    if clean { w } else { quads }
}
