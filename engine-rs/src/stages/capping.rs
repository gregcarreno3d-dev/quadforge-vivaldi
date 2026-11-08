//! Close open borders with caps so that the closed-surface machinery can run.
//!
//! Every boundary loop gets a fan of triangles around its centroid. Loops long enough to matter are marked as
//! removable: their border edges become feature lines (the grid aligns to them) and the cap triangles are
//! labelled so that the quads on the cap can be dropped afterwards. Short loops are simply left closed.
use crate::math::Vec3;
use crate::stages::repair::TriSoup;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug, Default)]
pub struct CapInfo {
    pub loops: i32,
    pub removable: i32,
    pub original_faces: usize,
    /// Per face of the capped soup: true = cap to drop.
    pub removable_face: Vec<bool>,
}

fn key(a: i32, b: i32) -> u64 { ((a as u32 as u64) << 32) | b as u32 as u64 }

/// Isolated flat slivers: a triangle whose three edges are all open and whose vertices lie on a line has no area
/// and belongs to no surface (a leftover of a bad weld). It would break the grid, so it is removed.
pub fn stitch_slits(soup: &mut TriSoup) -> i32 {
    let mut directed: HashSet<u64> = HashSet::with_capacity(soup.f.len() * 3);
    for t in &soup.f { for k in 0..3 { directed.insert(key(t[k], t[(k + 1) % 3])); } }
    let mut keep = Vec::with_capacity(soup.f.len());
    let mut removed = 0;
    for t in &soup.f {
        let all_open = (0..3).all(|k| !directed.contains(&key(t[(k + 1) % 3], t[k])));
        if all_open {
            let p = |i: usize| soup.p[t[i] as usize];
            let (a, b, c) = (p(0).distance(p(1)), p(1).distance(p(2)), p(2).distance(p(0)));
            let longest = a.max(b).max(c);
            if a + b + c - longest <= 1.02 * longest { removed += 1; continue; }
        }
        keep.push(*t);
    }
    if removed > 0 { soup.f = keep; }
    removed
}

/// Returns false (soup untouched) when the border is not made of simple loops.
pub fn cap_borders(soup: &mut TriSoup, min_loop_length: f64, info: &mut CapInfo) -> bool {
    *info = CapInfo { original_faces: soup.f.len(), ..Default::default() };
    let mut directed: HashSet<u64> = HashSet::with_capacity(soup.f.len() * 3);
    for t in &soup.f { for k in 0..3 { directed.insert(key(t[k], t[(k + 1) % 3])); } }
    let mut next_of: HashMap<i32, i32> = HashMap::new(); // boundary edge a -> b
    // iterate in face order so loops come out in a deterministic order
    let mut order: Vec<i32> = Vec::new();
    for t in &soup.f {
        for k in 0..3 {
            let (a, b) = (t[k], t[(k + 1) % 3]);
            if directed.contains(&key(b, a)) { continue; }
            if next_of.contains_key(&a) { return false; } // a vertex with two outgoing border edges
            next_of.insert(a, b);
            order.push(a);
        }
    }
    if next_of.is_empty() { return true; }
    let mut loops: Vec<Vec<i32>> = Vec::new();
    let mut seen: HashSet<i32> = HashSet::new();
    for &start in &order {
        if seen.contains(&start) { continue; }
        let mut lp = Vec::new();
        let mut v = start;
        while !seen.contains(&v) {
            seen.insert(v);
            lp.push(v);
            match next_of.get(&v) { Some(&n) => v = n, None => return false }
        }
        if v != start || lp.len() < 3 { return false; }
        loops.push(lp);
    }
    let mut out = soup.clone();
    info.removable_face = vec![false; soup.f.len()];
    for lp in &loops {
        let mut len = 0.0;
        let mut c = Vec3::ZERO;
        let mut dens = 0f32;
        for i in 0..lp.len() {
            let (a, b) = (lp[i], lp[(i + 1) % lp.len()]);
            len += soup.p[a as usize].distance(soup.p[b as usize]);
            c += soup.p[a as usize];
            if !soup.density.is_empty() { dens += soup.density[a as usize]; }
        }
        c = c / lp.len() as f64;
        let cv = out.p.len() as i32;
        out.p.push(c);
        if !soup.density.is_empty() { out.density.push(dens / lp.len() as f32); }
        let removable = len >= min_loop_length;
        for i in 0..lp.len() {
            let (a, b) = (lp[i], lp[(i + 1) % lp.len()]);
            out.f.push([b, a, cv]);
            info.removable_face.push(removable);
            if removable { out.features.push([a, b]); }
        }
        info.loops += 1;
        if removable { info.removable += 1; }
    }
    *soup = out;
    true
}
