//! Chamfer / bevel ("band") detector.
//!
//! A band is a narrow, elongated strip of surface that joins two much larger surfaces and turns the
//! surface direction between them: a flat strip is a chamfer, a rounded one (several facets or a
//! curved strip) is a bevel / fillet.
//!
//! Steps: weld → flat regions (region growing on normals) → narrow elongated candidates (width relative to the
//! area-weighted width of the big regions) → connected clusters → measurement of each cluster (width, length,
//! turn between the two outer neighbours, curvature, radius, convex/concave).
//!
//! Needs no feature marks: it works directly on the triangulated CAD mesh (STL).
use crate::io::fmesh::PolyMesh;
use crate::math::Vec3;
use std::collections::HashMap;

#[derive(Clone, Debug)]
pub struct BandParams {
    /// Edges whose normals differ by at least this (degrees) separate regions.
    pub sharp_deg: f64,
    /// Faces whose normal turns faster than `curved_factor / max_width` (rad per unit) count as curved.
    pub curved_factor: f64,
    /// A band is at most this fraction of the width of the narrower of its two outer neighbours.
    pub narrow_ratio: f64,
    /// Candidate strips are linked into one band when the normals across their shared edge differ by at most this (degrees).
    pub link_deg: f64,
    /// Strips are linked only when their widths differ by less than this factor.
    pub link_width_ratio: f64,
    /// Widest strip that still counts as a band (model units); 0 = 4 % of the bounding box diagonal.
    pub max_width: f64,
    /// Minimum length / width of a candidate.
    pub min_elong: f64,
    /// Accepted total turn between the two outer neighbours (degrees).
    pub min_turn_deg: f64,
    pub max_turn_deg: f64,
}

impl Default for BandParams {
    fn default() -> Self { BandParams { sharp_deg: 25.0, curved_factor: 1.0, narrow_ratio: 0.6, link_deg: 35.0, link_width_ratio: 1e9, max_width: 0.0, min_elong: 2.0, min_turn_deg: 8.0, max_turn_deg: 135.0 } }
}

#[derive(Clone, Debug)]
pub struct Band {
    /// "chamfer" or "bevel".
    pub kind: &'static str,
    pub convex: bool,
    pub faces: usize,
    pub facets: usize,
    pub area: f64,
    pub width: f64,
    pub length: f64,
    pub turn_deg: f64,
    /// Normal spread inside the band (degrees); ~0 for a chamfer.
    pub curvature_deg: f64,
    /// Bevel radius estimate (0 for chamfers).
    pub radius: f64,
}

#[derive(Clone, Debug, Default)]
pub struct BandReport {
    /// Per input polygon: band index or -1.
    pub face_band: Vec<i32>,
    pub bands: Vec<Band>,
    pub regions: usize,
    pub ref_width: f64,
}

impl BandReport {
    pub fn to_json(&self, with_faces: bool) -> String {
        let mut s = String::from("{");
        s += &format!("\"regions\":{},\"ref_width\":{:.6},\"bands\":[", self.regions, self.ref_width);
        for (i, b) in self.bands.iter().enumerate() {
            if i > 0 { s.push(','); }
            s += &format!(
                "{{\"id\":{},\"kind\":\"{}\",\"convex\":{},\"faces\":{},\"facets\":{},\"area\":{:.6},\"width\":{:.6},\"length\":{:.6},\"turn\":{:.2},\"curvature\":{:.2},\"radius\":{:.6}}}",
                i, b.kind, b.convex, b.faces, b.facets, b.area, b.width, b.length, b.turn_deg, b.curvature_deg, b.radius
            );
        }
        s.push(']');
        if with_faces {
            s += ",\"face_band\":[";
            for (i, v) in self.face_band.iter().enumerate() { if i > 0 { s.push(','); } s += &v.to_string(); }
            s.push(']');
        }
        s.push('}');
        s
    }
}

fn angle(a: Vec3, b: Vec3) -> f64 { a.dot(b).clamp(-1.0, 1.0).acos().to_degrees() }

fn find(p: &mut Vec<usize>, mut x: usize) -> usize {
    while p[x] != x { p[x] = p[p[x]]; x = p[x]; }
    x
}

pub fn detect(m: &PolyMesh, prm: &BandParams) -> BandReport {
    let mut rep = BandReport { face_band: vec![-1; m.sizes.len()], ..Default::default() };
    if m.p.is_empty() { return rep; }
    // ---- weld vertices by position
    let (mut lo, mut hi) = (m.p[0], m.p[0]);
    for p in &m.p {
        lo = Vec3::new(lo.x.min(p.x), lo.y.min(p.y), lo.z.min(p.z));
        hi = Vec3::new(hi.x.max(p.x), hi.y.max(p.y), hi.z.max(p.z));
    }
    let diag = (hi - lo).length().max(1e-12);
    let eps = diag * 1e-6;
    let mut keymap: HashMap<(i64, i64, i64), u32> = HashMap::new();
    let mut wp: Vec<Vec3> = Vec::new();
    let mut remap = vec![0u32; m.p.len()];
    for (i, p) in m.p.iter().enumerate() {
        let k = ((p.x / eps).round() as i64, (p.y / eps).round() as i64, (p.z / eps).round() as i64);
        remap[i] = *keymap.entry(k).or_insert_with(|| { wp.push(*p); (wp.len() - 1) as u32 });
    }
    // ---- triangles (fan for polygons), owner polygon
    let mut tv: Vec<[u32; 3]> = Vec::new();
    let mut own: Vec<u32> = Vec::new();
    let mut at = 0usize;
    for (k, &sz) in m.sizes.iter().enumerate() {
        let sz = sz as usize;
        for j in 1..sz.saturating_sub(1) {
            let t = [remap[m.idx[at] as usize], remap[m.idx[at + j] as usize], remap[m.idx[at + j + 1] as usize]];
            if t[0] != t[1] && t[1] != t[2] && t[0] != t[2] { tv.push(t); own.push(k as u32); }
        }
        at += sz;
    }
    let nf = tv.len();
    if nf == 0 { return rep; }
    let mut nrm = vec![Vec3::ZERO; nf];
    let mut area = vec![0.0f64; nf];
    for (f, t) in tv.iter().enumerate() {
        let c = (wp[t[1] as usize] - wp[t[0] as usize]).cross(wp[t[2] as usize] - wp[t[0] as usize]);
        area[f] = 0.5 * c.length();
        nrm[f] = c.normalized();
    }
    // ---- adjacency: adj[f][i] = neighbour across edge (t[i], t[i+1]) or -1; elen = edge length
    let ekey = |a: u32, b: u32| -> u64 { let (x, y) = if a < b { (a, b) } else { (b, a) }; ((x as u64) << 32) | y as u64 };
    let mut emap: HashMap<u64, Vec<i32>> = HashMap::new();
    for (f, t) in tv.iter().enumerate() {
        for i in 0..3 { emap.entry(ekey(t[i], t[(i + 1) % 3])).or_default().push(f as i32); }
    }
    let mut adj = vec![[-1i32; 3]; nf];
    let mut elen = vec![[0.0f64; 3]; nf];
    for (f, t) in tv.iter().enumerate() {
        for i in 0..3 {
            let (a, b) = (t[i], t[(i + 1) % 3]);
            elen[f][i] = wp[a as usize].distance(wp[b as usize]);
            let l = &emap[&ekey(a, b)];
            if l.len() == 2 { adj[f][i] = if l[0] == f as i32 { l[1] } else { l[0] }; }
        }
    }
    drop(emap);
    // ---- regions: faces split by sharp edges and by "curved" (bevel-like) versus "flat" class
    let max_w = if prm.max_width > 0.0 { prm.max_width } else { 0.04 * diag };
    let kc = prm.curved_factor / max_w;
    let mut dih = vec![[0.0f64; 3]; nf];
    let mut kappa = vec![0.0f64; nf];
    for f in 0..nf {
        for i in 0..3 {
            let g = adj[f][i];
            if g < 0 { dih[f][i] = 180.0; continue; }
            let d = angle(nrm[f], nrm[g as usize]);
            dih[f][i] = d;
            if d < prm.sharp_deg {
                // distance between the two centroids measured across the shared edge (unfolded)
                let gi = g as usize;
                let dist = ((2.0 * area[f] + 2.0 * area[gi]) / elen[f][i].max(1e-12) / 3.0).max(1e-12);
                kappa[f] = kappa[f].max(d.to_radians() / dist);
            }
        }
    }
    let curved: Vec<bool> = kappa.iter().map(|&k| k >= kc).collect();
    let mut reg = vec![-1i32; nf];
    let mut nreg = 0usize;
    for s in 0..nf {
        if reg[s] >= 0 { continue; }
        let id = nreg as i32; nreg += 1;
        reg[s] = id;
        let mut stack = vec![s];
        while let Some(f) = stack.pop() {
            for i in 0..3 {
                let g = adj[f][i];
                if g < 0 || reg[g as usize] >= 0 { continue; }
                let g = g as usize;
                if dih[f][i] < prm.sharp_deg && curved[f] == curved[g] { reg[g] = id; stack.push(g); }
            }
        }
    }
    rep.regions = nreg;
    // ---- region measures
    let mut r_area = vec![0.0f64; nreg];
    let mut r_len = vec![0.0f64; nreg];
    let mut r_nrm = vec![Vec3::ZERO; nreg];
    for f in 0..nf {
        let r = reg[f] as usize;
        r_area[r] += area[f];
        r_nrm[r] = r_nrm[r] + nrm[f] * area[f];
        for i in 0..3 { if adj[f][i] < 0 || reg[adj[f][i] as usize] != reg[f] { r_len[r] += elen[f][i]; } }
    }
    let r_w: Vec<f64> = (0..nreg).map(|r| if r_len[r] > 0.0 { 2.0 * r_area[r] / r_len[r] } else { 0.0 }).collect();
    let r_elong: Vec<f64> = (0..nreg).map(|r| { let w = r_w[r].max(1e-12); ((r_len[r] * 0.5 - w) / w).max(0.0) }).collect();
    for n in r_nrm.iter_mut() { *n = n.normalized(); }
    // area-weighted 75th percentile of region widths = width of the "big" surfaces
    let mut by_w: Vec<usize> = (0..nreg).collect();
    by_w.sort_by(|&a, &b| r_w[a].partial_cmp(&r_w[b]).unwrap_or(std::cmp::Ordering::Equal));
    let total: f64 = r_area.iter().sum();
    let (mut acc, mut wref) = (0.0, 0.0);
    for &r in &by_w { acc += r_area[r]; wref = r_w[r]; if acc >= 0.75 * total { break; } }
    rep.ref_width = wref;
    let cand: Vec<bool> = (0..nreg).map(|r| r_elong[r] >= prm.min_elong && r_w[r] <= max_w).collect();
    if std::env::var("FORGE_DBG").is_ok() { for r in 0..nreg { eprintln!("region {r}: area {:.2} len {:.2} w {:.3} elong {:.1} cand {} n {:?}", r_area[r], r_len[r], r_w[r], r_elong[r], cand[r], r_nrm[r]); } }
    // ---- clusters of candidates touching each other
    // two strips belong to the same band when they run side by side (long shared contact), turn gently
    // across it and have similar widths; strips meeting end to end (a wall and a ring) stay apart.
    let mut par: Vec<usize> = (0..nreg).collect();
    let mut contact: HashMap<(usize, usize), f64> = HashMap::new();
    for f in 0..nf {
        for i in 0..3 {
            let g = adj[f][i];
            if g < 0 { continue; }
            let (a, b) = (reg[f] as usize, reg[g as usize] as usize);
            if a < b && cand[a] && cand[b] && angle(nrm[f], nrm[g as usize]) <= prm.link_deg { *contact.entry((a, b)).or_insert(0.0) += elen[f][i]; }
        }
    }
    for (&(a, b), &c) in &contact {
        let (wa, wb) = (r_w[a].max(1e-12), r_w[b].max(1e-12));
        let ratio = if wa > wb { wa / wb } else { wb / wa };
        if ratio <= prm.link_width_ratio && c >= 0.35 * 0.5 * r_len[a].min(r_len[b]) { let (x, y) = (find(&mut par, a), find(&mut par, b)); par[x] = y; }
    }
    let mut comp_of = vec![usize::MAX; nreg];
    let mut comps: Vec<Vec<usize>> = Vec::new(); // regions per component
    for r in 0..nreg {
        if !cand[r] { continue; }
        let root = find(&mut par, r);
        if comp_of[root] == usize::MAX { comp_of[root] = comps.len(); comps.push(Vec::new()); }
        comps[comp_of[root]].push(r);
    }
    let comp_of_region = |par: &mut Vec<usize>, r: usize| -> usize { comp_of[find(par, r)] };
    // per component face lists
    let mut cfaces: Vec<Vec<usize>> = vec![Vec::new(); comps.len()];
    for f in 0..nf { if cand[reg[f] as usize] { let c = comp_of_region(&mut par, reg[f] as usize); cfaces[c].push(f); } }
    // pass 1: outer boundary, width and neighbours of every cluster
    struct Cl { a: f64, l_out: f64, w: f64, sgn: f64, nb: Vec<(i32, f64, Vec3)> }
    let mut cls: Vec<Cl> = Vec::new();
    for (ci, faces) in cfaces.iter().enumerate() {
        let (mut a_sum, mut l_out, mut sgn) = (0.0, 0.0, 0.0);
        let mut nb: HashMap<i32, (f64, Vec3)> = HashMap::new();
        for &f in faces {
            a_sum += area[f];
            for i in 0..3 {
                let g = adj[f][i];
                let inside = g >= 0 && cand[reg[g as usize] as usize] && comp_of_region(&mut par, reg[g as usize] as usize) == ci;
                if inside { continue; }
                l_out += elen[f][i];
                if g >= 0 {
                    let e = nb.entry(reg[g as usize]).or_insert((0.0, Vec3::ZERO));
                    e.0 += elen[f][i];
                    e.1 = e.1 + nrm[g as usize] * elen[f][i];
                    let t = tv[f];
                    let dir = wp[t[(i + 1) % 3] as usize] - wp[t[i] as usize];
                    let sg = nrm[f].cross(nrm[g as usize]).dot(dir);
                    sgn += sg;
                }
            }
        }
        let mut v: Vec<(i32, f64, Vec3)> = nb.into_iter().map(|(k, (l, n))| (k, l, n.normalized())).collect();
        v.sort_by(|x, y| y.1.partial_cmp(&x.1).unwrap_or(std::cmp::Ordering::Equal));
        let w = if l_out > 0.0 { 2.0 * a_sum / l_out } else { 0.0 };
        cls.push(Cl { a: a_sum, l_out, w, sgn, nb: v });
    }
    // width of whatever a neighbour region is: its cluster when it is a strip, itself otherwise
    let nb_width = |par: &mut Vec<usize>, r: usize| -> f64 { if cand[r] { cls[comp_of_region(par, r)].w } else { r_w[r] } };
    let mut bands: Vec<(f64, Band, usize)> = Vec::new();
    for (ci, c) in cls.iter().enumerate() {
        if c.l_out <= 0.0 || c.nb.len() < 2 || c.nb[1].1 < 0.15 * c.l_out { continue; }
        let (n0, n1) = (c.nb[0], c.nb[1]);
        let turn = angle(n0.2, n1.2);
        if turn < prm.min_turn_deg || turn > prm.max_turn_deg { continue; }
        let wn = nb_width(&mut par, n0.0 as usize).min(nb_width(&mut par, n1.0 as usize));
        if c.w > prm.narrow_ratio * wn { continue; }
        let flank = n0.1 + n1.1;
        let w = 2.0 * c.a / flank;
        let len = (flank * 0.5).max(w);
        if len < 1.5 * max_w { continue; }
        let mut mean = Vec3::ZERO;
        for &f in &cfaces[ci] { mean = mean + nrm[f] * area[f]; }
        let mean = mean.normalized();
        let dev = cfaces[ci].iter().map(|&f| angle(nrm[f], mean)).fold(0.0, f64::max);
        let curv = 2.0 * dev;
        let bevel = curv >= 4.0;
        let radius = if bevel { w / turn.to_radians() } else { 0.0 };
        bands.push((c.a, Band {
            kind: if bevel { "bevel" } else { "chamfer" }, convex: c.sgn > 0.0, faces: cfaces[ci].len(), facets: comps[ci].len(),
            area: c.a, width: w, length: len, turn_deg: turn, curvature_deg: curv, radius,
        }, ci));
    }
    bands.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let mut comp_band = vec![-1i32; comps.len()];
    for (i, (_, b, ci)) in bands.iter().enumerate() { comp_band[*ci] = i as i32; rep.bands.push(b.clone()); }
    for f in 0..nf {
        let r = reg[f] as usize;
        if !cand[r] { continue; }
        let c = comp_of_region(&mut par, r);
        if comp_band[c] >= 0 { rep.face_band[own[f] as usize] = comp_band[c]; }
    }
    rep
}
