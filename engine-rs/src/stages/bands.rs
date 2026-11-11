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
    /// Curved faces grow over neighbours whose curvature is at least this fraction of the threshold.
    pub curved_low: f64,
    /// A band is at most this fraction of the size (square root of the area) of the smaller of its two outer neighbours.
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
    fn default() -> Self { BandParams { sharp_deg: 25.0, curved_factor: 1.5, curved_low: 0.9, narrow_ratio: 0.35, link_deg: 35.0, link_width_ratio: 1e9, max_width: 0.0, min_elong: 2.0, min_turn_deg: 8.0, max_turn_deg: 135.0 } }
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
    /// Debug (FORGE_BANDS_DEBUG): region id and curvature per input polygon.
    pub dbg_region: Vec<i32>,
    pub dbg_kappa: Vec<f32>,
    pub dbg_regions: Vec<(f64, f64, f64, bool)>,
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
            if !self.dbg_region.is_empty() {
                s += ",\"face_region\":[";
                for (i, v) in self.dbg_region.iter().enumerate() { if i > 0 { s.push(','); } s += &v.to_string(); }
                s += "],\"face_kappa\":[";
                for (i, v) in self.dbg_kappa.iter().enumerate() { if i > 0 { s.push(','); } s += &format!("{:.3}", v); }
                s += "],\"regions_info\":[";
                for (i, r) in self.dbg_regions.iter().enumerate() { if i > 0 { s.push(','); } s += &format!("[{:.4},{:.4},{:.2},{}]", r.0, r.1, r.2, r.3); }
                s.push(']');
            }
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

fn point_seg(p: Vec3, a: Vec3, b: Vec3) -> f64 {
    let ab = b - a;
    let l2 = ab.dot(ab);
    let t = if l2 > 0.0 { ((p - a).dot(ab) / l2).clamp(0.0, 1.0) } else { 0.0 };
    p.distance(a + ab * t)
}

fn seg_seg(a: Vec3, b: Vec3, c: Vec3, d: Vec3) -> f64 {
    point_seg(a, c, d).min(point_seg(b, c, d)).min(point_seg(c, a, b)).min(point_seg(d, a, b))
}

fn cls_area(cfaces: &[Vec<usize>], area: &[f64], c: usize) -> f64 { cfaces[c].iter().map(|&f| area[f]).sum() }

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
    let cen: Vec<Vec3> = tv.iter().map(|t| (wp[t[0] as usize] + wp[t[1] as usize] + wp[t[2] as usize]) * (1.0 / 3.0)).collect();
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
                let dist = ((2.0 * area[f] + 2.0 * area[gi]) / elen[f][i].max(1e-12) / 2.0).max(1e-12);
                kappa[f] = kappa[f].max(d.to_radians() / dist);
            }
        }
    }
    // hysteresis: strongly curved faces seed the curved class, which grows over weakly curved neighbours
    // (the tapering ends of a bevel) so its edge is not ragged
    let kc_lo = prm.curved_low * kc;
    let mut curved: Vec<bool> = kappa.iter().map(|&k| k >= kc).collect();
    let mut stack: Vec<usize> = (0..nf).filter(|&f| curved[f]).collect();
    while let Some(f) = stack.pop() {
        for i in 0..3 {
            let g = adj[f][i];
            if g < 0 { continue; }
            let g = g as usize;
            if !curved[g] && dih[f][i] < prm.sharp_deg && kappa[g] >= kc_lo { curved[g] = true; stack.push(g); }
        }
    }
    // closing: a flat-class face with two curved neighbours is an island inside a curved zone (skinny triangles measure low)
    for _ in 0..4 {
        let mut changed = false;
        let snapshot = curved.clone();
        for f in 0..nf {
            if snapshot[f] { continue; }
            let k = (0..3).filter(|&i| adj[f][i] >= 0 && dih[f][i] < prm.sharp_deg && snapshot[adj[f][i] as usize]).count();
            if k >= 2 { curved[f] = true; changed = true; }
        }
        if !changed { break; }
    }
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
    let dbg = std::env::var("FORGE_BANDS_DEBUG").is_ok();
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
    if dbg {
        rep.dbg_region = vec![-1; m.sizes.len()];
        rep.dbg_kappa = vec![0.0; m.sizes.len()];
        for f in 0..nf { rep.dbg_region[own[f] as usize] = reg[f]; rep.dbg_kappa[own[f] as usize] = kappa[f] as f32; }
        rep.dbg_regions = (0..nreg).map(|r| (r_area[r], r_w[r], r_elong[r], cand[r])).collect();
    }
    let mut bands: Vec<(f64, Band, usize)> = Vec::new();
    for (ci, faces) in cfaces.iter().enumerate() {
        // outer boundary: (midpoint, length, neighbour face, neighbour region, signed turn)
        struct Edge { mid: Vec3, pa: Vec3, pb: Vec3, len: f64, g: usize, rg: i32 }
        let mut edges: Vec<Edge> = Vec::new();
        let (mut a_sum, mut l_out, mut sgn) = (0.0, 0.0, 0.0);
        for &f in faces {
            a_sum += area[f];
            for i in 0..3 {
                let g = adj[f][i];
                let inside = g >= 0 && cand[reg[g as usize] as usize] && comp_of_region(&mut par, reg[g as usize] as usize) == ci;
                if inside { continue; }
                l_out += elen[f][i];
                if g >= 0 {
                    let t = tv[f];
                    let (pa, pb) = (wp[t[i] as usize], wp[t[(i + 1) % 3] as usize]);
                    let sg = nrm[f].cross(nrm[g as usize]).dot(pb - pa);
                    sgn += sg;
                    edges.push(Edge { mid: (pa + pb) * 0.5, pa, pb, len: elen[f][i], g: g as usize, rg: reg[g as usize] });
                }
            }
        }
        let why = |m: &str| { if dbg && a_sum > 0.5 { eprintln!("cluster {ci}: area {:.2} regions {} edges {}: {m}", a_sum, comps[ci].len(), edges.len()); } };
        if l_out <= 0.0 || edges.is_empty() { why("no flanks"); continue; }
        // distance from the band boundary: multi-source Dijkstra over the band's faces, remembering which boundary
        // edge is nearest. Where two faces whose nearest edges are far apart meet (the ridge along the middle of the
        // band), those two edges lie on opposite flanks: they give the pairs used to measure turn and neighbours.
        let mut local: HashMap<usize, usize> = HashMap::new();
        for (k, &f) in faces.iter().enumerate() { local.insert(f, k); }
        // nearest boundary edge of every face centroid (exact point-to-segment distance, so long skinny triangles
        // are measured across the band, not along it)
        let mut dist = vec![f64::INFINITY; faces.len()];
        let mut src = vec![usize::MAX; faces.len()];
        {
            // bounding boxes of the edges to skip far ones quickly
            for (k, &f) in faces.iter().enumerate() {
                let p = cen[f];
                let (mut bd, mut bi) = (f64::INFINITY, usize::MAX);
                for (ei, e) in edges.iter().enumerate() {
                    let (lo, hi) = (Vec3::new(e.pa.x.min(e.pb.x), e.pa.y.min(e.pb.y), e.pa.z.min(e.pb.z)), Vec3::new(e.pa.x.max(e.pb.x), e.pa.y.max(e.pb.y), e.pa.z.max(e.pb.z)));
                    let (dx, dy, dz) = ((lo.x - p.x).max(p.x - hi.x).max(0.0), (lo.y - p.y).max(p.y - hi.y).max(0.0), (lo.z - p.z).max(p.z - hi.z).max(0.0));
                    if dx * dx + dy * dy + dz * dz >= bd * bd { continue; }
                    let d = point_seg(p, e.pa, e.pb);
                    if d < bd { bd = d; bi = ei; }
                }
                dist[k] = bd; src[k] = bi;
            }
        }
        let w0 = 2.0 * a_sum / l_out;
        let (mut tsum, mut tw) = (0.0, 0.0);
        let mut used: std::collections::HashSet<usize> = std::collections::HashSet::new();
        let mut sizes: Vec<f64> = Vec::new();
        let mut wsum = 0.0;
        for (k, &f) in faces.iter().enumerate() {
            for i in 0..3 {
                let g = adj[f][i];
                if g < 0 { continue; }
                let Some(&kg) = local.get(&(g as usize)) else { continue };
                if kg < k || src[k] == usize::MAX || src[kg] == usize::MAX || src[k] == src[kg] { continue; }
                let (ea, eb) = (&edges[src[k]], &edges[src[kg]]);
                let (rm, dsum) = ((cen[f] + cen[g as usize]) * 0.5, dist[k] + dist[kg]);
                let (da, db) = (point_seg(rm, ea.pa, ea.pb), point_seg(rm, eb.pa, eb.pb));
                if dbg && a_sum > 100.0 { eprintln!("   pair da {da:.2} db {db:.2} w0 {w0:.2} segsep {:.2} dist {:.2}+{:.2}", seg_seg(ea.pa, ea.pb, eb.pa, eb.pb), dist[k], dist[kg]); }
                if da < 0.25 * w0 || db < 0.25 * w0 || da + db > 3.0 * w0 || seg_seg(ea.pa, ea.pb, eb.pa, eb.pb) < 0.6 * (da + db) { let _ = dsum; continue; }
                tsum += angle(nrm[ea.g], nrm[eb.g]) * elen[f][i];
                tw += elen[f][i];
                used.insert(src[k]); used.insert(src[kg]);
                let mut size = |r: i32| { let r = r as usize; (if cand[r] { cls_area(&cfaces, &area, comp_of_region(&mut par, r)) } else { r_area[r] }).sqrt() };
                sizes.push(size(ea.rg).max(size(eb.rg)) * elen[f][i]);
                wsum += (da + db) * elen[f][i];
            }
        }
        if tw <= 0.0 { why("no ridge between two flanks"); continue; }
        let _ = &used;
        let w = wsum / tw;
        let len = (a_sum / w.max(1e-12)).max(w);
        if len < 1.5 * max_w { why("too short"); continue; }
        let size = sizes.iter().sum::<f64>() / tw;
        if w > prm.narrow_ratio * size { why(&format!("wider than its neighbours (w {w:.3}, neighbour size {size:.3})")); continue; }
        let turn = tsum / tw;
        if turn < prm.min_turn_deg || turn > prm.max_turn_deg { why(&format!("turn {turn:.0} out of range")); continue; }
        // bevel or chamfer: curved-class faces, or facets that turn against each other
        let curved_faces = faces.iter().filter(|&&f| curved[f]).count();
        let mut facet_turn = 0.0;
        for &f in faces {
            for i in 0..3 {
                let g = adj[f][i];
                if g < 0 { continue; }
                let g = g as usize;
                if reg[g] != reg[f] && cand[reg[g] as usize] && comp_of_region(&mut par, reg[g] as usize) == ci && angle(nrm[f], nrm[g]) >= 8.0 { facet_turn += elen[f][i]; }
            }
        }
        let curv = if curved_faces * 2 >= faces.len() { turn } else { 0.0 };
        let bevel = curved_faces * 2 >= faces.len() || facet_turn >= 0.5 * len;
        let radius = if bevel { w / turn.to_radians() } else { 0.0 };
        bands.push((a_sum, Band {
            kind: if bevel { "bevel" } else { "chamfer" }, convex: sgn > 0.0, faces: faces.len(), facets: comps[ci].len(),
            area: a_sum, width: w, length: len, turn_deg: turn, curvature_deg: curv, radius,
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
