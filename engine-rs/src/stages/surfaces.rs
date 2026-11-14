//! Whole-model surface segmentation for CAD meshes ("what is every wall of this part?").
//!
//! The chamfer / bevel detector (`bands.rs`) finds the narrow transition strips. This stage takes everything else
//! and splits it into **surfaces** the way a CAD B-rep does: planes, cylinders (also coarse polygonal ones, such as a
//! 14-sided hole), spheres and free-form patches. Every band becomes a surface of its own (kind chamfer / bevel).
//! The result also lists the **borders** between surfaces (length, angle, sharp / tangent / band) so the part is
//! described as areas plus limits.
//!
//! Steps: weld → bands → coplanar patches (region growing on a plane) → cylinders grown over neighbouring patches
//! whose vertices lie on one circle and whose shared edges run along the axis → spheres / free-form on the leftovers →
//! merge coplanar neighbours → borders.
use crate::io::fmesh::PolyMesh;
use crate::math::Vec3;
use crate::stages::bands::{self, BandParams, BandReport};
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug)]
pub struct SurfParams {
    pub bands: BandParams,
    /// Normals closer than this (degrees) and vertices closer than `plane_tol` belong to one plane.
    pub plane_deg: f64,
    /// Distance tolerance as a fraction of the bounding box diagonal.
    pub tol_rel: f64,
    /// Patches meeting at less than this angle (degrees) may belong to one curved surface.
    pub smooth_deg: f64,
    /// A border at or above this mean angle is "sharp", below it "tangent".
    pub sharp_deg: f64,
    /// Non-band surfaces smaller than this fraction of the total area are absorbed by the neighbour they share most border with.
    pub min_area_rel: f64,
}

impl Default for SurfParams {
    fn default() -> Self { SurfParams { bands: BandParams::default(), plane_deg: 3.0, tol_rel: 0.002, smooth_deg: 50.0, sharp_deg: 25.0, min_area_rel: 0.0005 } }
}

#[derive(Clone, Debug, Default)]
pub struct Surface {
    pub kind: &'static str,
    pub triangles: usize,
    pub patches: usize,
    pub area: f64,
    pub perimeter: f64,
    pub center: [f64; 3],
    pub axis: [f64; 3],
    pub radius: f64,
    /// Sweep angle of a cylinder in degrees (360 = closed), 0 otherwise.
    pub sweep: f64,
    /// Largest distance of any vertex from the fitted shape, relative to the bounding box diagonal.
    pub err: f64,
    pub band: i32,
    pub bbox: [f64; 6],
}

#[derive(Clone, Debug, Default)]
pub struct Border {
    pub a: i32,
    pub b: i32,
    pub length: f64,
    pub edges: usize,
    pub angle: f64,
    pub kind: &'static str,
}

#[derive(Clone, Debug, Default)]
pub struct SurfaceReport {
    pub face_surface: Vec<i32>,
    pub surfaces: Vec<Surface>,
    pub borders: Vec<Border>,
    pub bands: BandReport,
    pub diag: f64,
}

fn f3(v: Vec3) -> String { format!("[{:.5},{:.5},{:.5}]", v.x, v.y, v.z) }

impl SurfaceReport {
    pub fn to_json(&self, with_faces: bool) -> String {
        let mut s = format!("{{\"diag\":{:.5},\"surfaces\":[", self.diag);
        for (i, f) in self.surfaces.iter().enumerate() {
            if i > 0 { s.push(','); }
            s += &format!(
                "{{\"id\":{},\"kind\":\"{}\",\"triangles\":{},\"patches\":{},\"area\":{:.5},\"perimeter\":{:.5},\"center\":[{:.5},{:.5},{:.5}],\"axis\":[{:.5},{:.5},{:.5}],\"radius\":{:.5},\"sweep\":{:.1},\"err\":{:.6},\"band\":{},\"bbox\":[{:.4},{:.4},{:.4},{:.4},{:.4},{:.4}]}}",
                i, f.kind, f.triangles, f.patches, f.area, f.perimeter, f.center[0], f.center[1], f.center[2], f.axis[0], f.axis[1], f.axis[2],
                f.radius, f.sweep, f.err, f.band, f.bbox[0], f.bbox[1], f.bbox[2], f.bbox[3], f.bbox[4], f.bbox[5]
            );
        }
        s += "],\"borders\":[";
        for (i, b) in self.borders.iter().enumerate() {
            if i > 0 { s.push(','); }
            s += &format!("{{\"a\":{},\"b\":{},\"length\":{:.5},\"edges\":{},\"angle\":{:.2},\"kind\":\"{}\"}}", b.a, b.b, b.length, b.edges, b.angle, b.kind);
        }
        s += "],\"bands\":";
        s += &self.bands.to_json(false);
        if with_faces {
            s += ",\"face_surface\":[";
            for (i, v) in self.face_surface.iter().enumerate() { if i > 0 { s.push(','); } s += &v.to_string(); }
            s.push(']');
        }
        s.push('}');
        s
    }
}

fn angle(a: Vec3, b: Vec3) -> f64 { a.dot(b).clamp(-1.0, 1.0).acos().to_degrees() }

/// Solve a small dense linear system (Gauss with partial pivoting). Returns None when singular.
fn solve(mut a: Vec<Vec<f64>>, mut b: Vec<f64>) -> Option<Vec<f64>> {
    let n = b.len();
    for c in 0..n {
        let mut p = c;
        for r in c + 1..n { if a[r][c].abs() > a[p][c].abs() { p = r; } }
        if a[p][c].abs() < 1e-12 { return None; }
        a.swap(c, p);
        b.swap(c, p);
        for r in c + 1..n {
            let f = a[r][c] / a[c][c];
            for k in c..n { let v = a[c][k]; a[r][k] -= f * v; }
            let v = b[c];
            b[r] -= f * v;
        }
    }
    let mut x = vec![0.0; n];
    for c in (0..n).rev() {
        let mut s = b[c];
        for k in c + 1..n { s -= a[c][k] * x[k]; }
        x[c] = s / a[c][c];
    }
    Some(x)
}

/// Least-squares circle through points projected on the plane perpendicular to `axis`. Returns (centre in 3D, radius).
fn fit_circle(pts: &[Vec3], axis: Vec3) -> Option<(Vec3, f64)> {
    if pts.len() < 3 { return None; }
    let helper = if axis.x.abs() < 0.9 { Vec3::new(1.0, 0.0, 0.0) } else { Vec3::new(0.0, 1.0, 0.0) };
    let u = axis.cross(helper).normalized();
    let v = axis.cross(u).normalized();
    let o = pts[0];
    // x^2 + y^2 = 2 a x + 2 b y + c
    let mut ata = vec![vec![0.0; 3]; 3];
    let mut atb = vec![0.0; 3];
    for p in pts {
        let (x, y) = ((*p - o).dot(u), (*p - o).dot(v));
        let row = [2.0 * x, 2.0 * y, 1.0];
        let rhs = x * x + y * y;
        for i in 0..3 { for j in 0..3 { ata[i][j] += row[i] * row[j]; } atb[i] += row[i] * rhs; }
    }
    let s = solve(ata, atb)?;
    let r2 = s[2] + s[0] * s[0] + s[1] * s[1];
    if r2 <= 0.0 { return None; }
    // the centre lies on the axis line through the projected centre; keep the height of the first point
    let h = (pts[0] - o).dot(axis);
    Some((o + u * s[0] + v * s[1] + axis * h, r2.sqrt()))
}

fn dist_to_axis(p: Vec3, c: Vec3, axis: Vec3) -> f64 {
    let d = p - c;
    (d - axis * d.dot(axis)).length()
}

/// Least-squares sphere. Returns (centre, radius).
fn fit_sphere(pts: &[Vec3]) -> Option<(Vec3, f64)> {
    if pts.len() < 4 { return None; }
    let o = pts[0];
    let mut ata = vec![vec![0.0; 4]; 4];
    let mut atb = vec![0.0; 4];
    for p in pts {
        let d = *p - o;
        let row = [2.0 * d.x, 2.0 * d.y, 2.0 * d.z, 1.0];
        let rhs = d.dot(d);
        for i in 0..4 { for j in 0..4 { ata[i][j] += row[i] * row[j]; } atb[i] += row[i] * rhs; }
    }
    let s = solve(ata, atb)?;
    let c = Vec3::new(s[0], s[1], s[2]);
    let r2 = s[3] + c.dot(c);
    if r2 <= 0.0 { return None; }
    Some((o + c, r2.sqrt()))
}


/// Eigen-decomposition of a symmetric 3x3 matrix (Jacobi). Returns eigenvalues and eigenvectors (columns), ascending.
fn eigen3(mut a: [[f64; 3]; 3]) -> ([f64; 3], [[f64; 3]; 3]) {
    let mut v = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];
    for _ in 0..60 {
        let (mut p, mut q, mut big) = (0, 1, 0.0f64);
        for i in 0..3 { for j in i + 1..3 { if a[i][j].abs() > big { big = a[i][j].abs(); p = i; q = j; } } }
        if big < 1e-14 { break; }
        let th = 0.5 * (2.0 * a[p][q]).atan2(a[q][q] - a[p][p]);
        let (c, s) = (th.cos(), th.sin());
        for k in 0..3 { let (x, y) = (a[k][p], a[k][q]); a[k][p] = c * x - s * y; a[k][q] = s * x + c * y; }
        for k in 0..3 { let (x, y) = (a[p][k], a[q][k]); a[p][k] = c * x - s * y; a[q][k] = s * x + c * y; }
        for k in 0..3 { let (x, y) = (v[k][p], v[k][q]); v[k][p] = c * x - s * y; v[k][q] = s * x + c * y; }
    }
    let mut idx = [0usize, 1, 2];
    idx.sort_by(|&x, &y| a[x][x].partial_cmp(&a[y][y]).unwrap_or(std::cmp::Ordering::Equal));
    let w = [a[idx[0]][idx[0]], a[idx[1]][idx[1]], a[idx[2]][idx[2]]];
    let mut vec = [[0.0; 3]; 3];
    for (c, &i) in idx.iter().enumerate() { for k in 0..3 { vec[k][c] = v[k][i]; } }
    (w, vec)
}

/// Cone through planar patches: their planes meet in one point (the apex) and their normals lie on a small circle.
/// `planes` are (normal, point on plane, weight). Returns (apex, axis, half angle in degrees, worst plane distance of the apex).
fn fit_cone(planes: &[(Vec3, Vec3, f64)]) -> Option<(Vec3, Vec3, f64, f64)> {
    let wsum: f64 = planes.iter().map(|p| p.2).sum();
    if planes.len() < 4 || wsum <= 0.0 { return None; }
    let mean = planes.iter().fold(Vec3::ZERO, |a, p| a + p.0 * p.2) * (1.0 / wsum);
    let mut cov = [[0.0; 3]; 3];
    for (n, _, w) in planes {
        let d = [n.x - mean.x, n.y - mean.y, n.z - mean.z];
        for i in 0..3 { for j in 0..3 { cov[i][j] += w * d[i] * d[j] / wsum; } }
    }
    let (ev, vec) = eigen3(cov);
    if ev[1] < 1e-3 { return None; } // normals do not spread over an arc
    let mut axis = Vec3::new(vec[0][0], vec[1][0], vec[2][0]).normalized();
    if axis.dot(mean) < 0.0 { axis = axis * -1.0; }
    let cs: Vec<f64> = planes.iter().map(|p| p.0.dot(axis)).collect();
    let c = cs.iter().zip(planes).map(|(c, p)| c * p.2).sum::<f64>() / wsum;
    let sd = (cs.iter().zip(planes).map(|(x, p)| (x - c) * (x - c) * p.2).sum::<f64>() / wsum).sqrt();
    if sd > 0.03 || c.abs() < 0.06 || c.abs() > 0.97 { return None; }
    // apex: least-squares point on all planes
    let mut a = vec![vec![0.0; 3]; 3];
    let mut b = vec![0.0; 3];
    for (n, p, w) in planes {
        let nn = [n.x, n.y, n.z];
        let d = n.dot(*p);
        for i in 0..3 { for j in 0..3 { a[i][j] += w * nn[i] * nn[j]; } b[i] += w * nn[i] * d; }
    }
    let x = solve(a, b)?;
    let apex = Vec3::new(x[0], x[1], x[2]);
    let worst = planes.iter().map(|(n, p, _)| (n.dot(apex - *p)).abs()).fold(0.0, f64::max);
    Some((apex, axis, c.abs().acos().to_degrees(), worst))
}

struct Tris {
    wp: Vec<Vec3>,
    tv: Vec<[u32; 3]>,
    own: Vec<u32>,
    nrm: Vec<Vec3>,
    area: Vec<f64>,
    adj: Vec<[i32; 3]>,
    elen: Vec<[f64; 3]>,
    diag: f64,
}

fn build(m: &PolyMesh) -> Option<Tris> {
    if m.p.is_empty() { return None; }
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
    if nf == 0 { return None; }
    let mut nrm = vec![Vec3::ZERO; nf];
    let mut area = vec![0.0f64; nf];
    for (f, t) in tv.iter().enumerate() {
        let c = (wp[t[1] as usize] - wp[t[0] as usize]).cross(wp[t[2] as usize] - wp[t[0] as usize]);
        area[f] = 0.5 * c.length();
        nrm[f] = c.normalized();
    }
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
    Some(Tris { wp, tv, own, nrm, area, adj, elen, diag })
}

pub fn analyze(m: &PolyMesh, prm: &SurfParams) -> SurfaceReport {
    let mut rep = SurfaceReport { face_surface: vec![-1; m.sizes.len()], ..Default::default() };
    let Some(t) = build(m) else { return rep };
    let nf = t.tv.len();
    rep.diag = t.diag;
    rep.bands = bands::detect(m, &prm.bands);
    let tol = prm.tol_rel * t.diag;
    let band_of: Vec<i32> = (0..nf).map(|f| rep.bands.face_band[t.own[f] as usize]).collect();

    // ---------- 1. coplanar patches (big triangles first)
    let mut patch = vec![-1i32; nf];
    let mut p_tris: Vec<Vec<usize>> = Vec::new();
    let mut order: Vec<usize> = (0..nf).filter(|&f| band_of[f] < 0).collect();
    order.sort_by(|&a, &b| t.area[b].partial_cmp(&t.area[a]).unwrap_or(std::cmp::Ordering::Equal));
    let cos_pl = prm.plane_deg.to_radians().cos();
    for &seed in &order {
        if patch[seed] >= 0 { continue; }
        let id = p_tris.len() as i32;
        let n0 = t.nrm[seed];
        let c0 = t.tv[seed].iter().map(|&v| t.wp[v as usize]).fold(Vec3::ZERO, |a, b| a + b) * (1.0 / 3.0);
        let mut list = vec![seed];
        patch[seed] = id;
        let mut k = 0;
        while k < list.len() {
            let f = list[k];
            k += 1;
            for i in 0..3 {
                let g = t.adj[f][i];
                if g < 0 { continue; }
                let g = g as usize;
                if patch[g] >= 0 || band_of[g] >= 0 { continue; }
                if t.nrm[g].dot(n0) < cos_pl { continue; }
                if t.tv[g].iter().any(|&v| (t.wp[v as usize] - c0).dot(n0).abs() > tol) { continue; }
                patch[g] = id;
                list.push(g);
            }
        }
        p_tris.push(list);
    }
    let np = p_tris.len();
    let p_area: Vec<f64> = p_tris.iter().map(|l| l.iter().map(|&f| t.area[f]).sum()).collect();
    let p_nrm: Vec<Vec3> = p_tris.iter().map(|l| l.iter().fold(Vec3::ZERO, |a, &f| a + t.nrm[f] * t.area[f]).normalized()).collect();
    let p_verts: Vec<Vec<u32>> = p_tris.iter().map(|l| {
        let mut s: Vec<u32> = l.iter().flat_map(|&f| t.tv[f].iter().copied()).collect();
        s.sort_unstable();
        s.dedup();
        s
    }).collect();

    // ---------- 2. patch adjacency: (a, b) -> (length, sum angle*length, longest shared edge direction, its length)
    #[derive(Clone, Copy)]
    struct Link { len: f64, ang: f64, dir: Vec3, dlen: f64 }
    let mut links: HashMap<(usize, usize), Link> = HashMap::new();
    for f in 0..nf {
        if patch[f] < 0 { continue; }
        for i in 0..3 {
            let g = t.adj[f][i];
            if g < 0 { continue; }
            let g = g as usize;
            if g < f || patch[g] < 0 || patch[g] == patch[f] { continue; }
            let (a, b) = (patch[f] as usize, patch[g] as usize);
            let key = if a < b { (a, b) } else { (b, a) };
            let (u, v) = (t.wp[t.tv[f][i] as usize], t.wp[t.tv[f][(i + 1) % 3] as usize]);
            let e = links.entry(key).or_insert(Link { len: 0.0, ang: 0.0, dir: Vec3::ZERO, dlen: 0.0 });
            e.len += t.elen[f][i];
            e.ang += angle(t.nrm[f], t.nrm[g]) * t.elen[f][i];
            if t.elen[f][i] > e.dlen { e.dlen = t.elen[f][i]; e.dir = (v - u).normalized(); }
        }
    }
    let mut nbr: Vec<Vec<usize>> = vec![Vec::new(); np];
    for (&(a, b), l) in &links {
        let ang = l.ang / l.len.max(1e-12);
        if ang <= prm.smooth_deg { nbr[a].push(b); nbr[b].push(a); }
    }

    // ---------- 2b. surfaces of revolution about an axis-aligned axis through the middle of the part (turned parts, spools, pulleys):
    // a patch belongs when its normal lies in the plane that contains the axis (it has no tangential component)
    let mut in_cyl = vec![-1i32; np];
    let mut rev_groups: Vec<(Vec<usize>, Vec3, Vec3)> = Vec::new(); // patches, centre, axis
    {
        let mut lo = Vec3::new(f64::MAX, f64::MAX, f64::MAX);
        let mut hi = Vec3::new(f64::MIN, f64::MIN, f64::MIN);
        for f in 0..nf { if band_of[f] < 0 { for &v in &t.tv[f] { let q = t.wp[v as usize]; lo = Vec3::new(lo.x.min(q.x), lo.y.min(q.y), lo.z.min(q.z)); hi = Vec3::new(hi.x.max(q.x), hi.y.max(q.y), hi.z.max(q.z)); } } }
        let mid = (lo + hi) * 0.5;
        let cen_p: Vec<Vec3> = p_tris.iter().map(|l| { let mut sm = Vec3::ZERO; let mut w = 0.0; for &f in l { let c = t.tv[f].iter().map(|&v| t.wp[v as usize]).fold(Vec3::ZERO, |a, b| a + b) * (1.0 / 3.0); sm += c * t.area[f]; w += t.area[f]; } sm * (1.0 / w.max(1e-30)) }).collect();
        let total: f64 = p_area.iter().sum();
        let mut taken = vec![false; np];
        for ax in [Vec3::new(0.0, 1.0, 0.0), Vec3::new(0.0, 0.0, 1.0), Vec3::new(1.0, 0.0, 0.0)] {
            let pass: Vec<bool> = (0..np).map(|q| {
                if taken[q] || p_nrm[q].dot(ax).abs() > 0.995 { return false; }
                let rad = cen_p[q] - mid;
                let rad = rad - ax * rad.dot(ax);
                if rad.length() < 4.0 * tol { return false; }
                let tg = ax.cross(rad).normalized();
                p_nrm[q].dot(tg).abs() < 0.05
            }).collect();
            let mut seen = vec![false; np];
            for s0 in 0..np {
                if !pass[s0] || seen[s0] { continue; }
                let mut st = vec![s0];
                seen[s0] = true;
                let mut list = Vec::new();
                while let Some(q) = st.pop() { list.push(q); for &w in &nbr[q] { if pass[w] && !seen[w] { seen[w] = true; st.push(w); } } }
                let area: f64 = list.iter().map(|&q| p_area[q]).sum();
                if list.len() < 8 || area < 0.02 * total { continue; }
                // the patches must go all the way round the axis (a hole wall that happens to point outwards does not)
                let helper = if ax.x.abs() < 0.9 { Vec3::new(1.0, 0.0, 0.0) } else { Vec3::new(0.0, 1.0, 0.0) };
                let u = ax.cross(helper).normalized();
                let v = ax.cross(u).normalized();
                let mut angs: Vec<f64> = list.iter().map(|&q| (cen_p[q] - mid).dot(v).atan2((cen_p[q] - mid).dot(u)).to_degrees()).collect();
                angs.sort_by(|a, b| a.partial_cmp(b).unwrap());
                let mut gap = angs[0] + 360.0 - angs[angs.len() - 1];
                for w in angs.windows(2) { gap = gap.max(w[1] - w[0]); }
                if gap > 120.0 { continue; }
                for &q in &list { taken[q] = true; in_cyl[q] = -2; }
                rev_groups.push((list, mid, ax));
            }
        }
    }
    // ---------- 3. cylinders: seed on a pair of neighbouring patches whose shared edge runs along the axis
    struct Cyl { patches: Vec<usize>, c: Vec3, r: f64, axis: Vec3, err: f64 }
    let mut cyls: Vec<Cyl> = Vec::new();
    let mut pairs: Vec<((usize, usize), f64)> = links.iter()
        .filter(|(_, l)| l.ang / l.len.max(1e-12) <= prm.smooth_deg && l.ang / l.len.max(1e-12) >= prm.plane_deg)
        .map(|(&k, _)| (k, p_area[k.0] + p_area[k.1])).collect();
    pairs.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal).then(a.0.cmp(&b.0)));
    let normals_ok = |q: usize, axis: Vec3| p_tris[q].iter().all(|&f| t.nrm[f].dot(axis).abs() < 0.09);
    for &((a, b), _) in &pairs {
        if in_cyl[a] != -1 || in_cyl[b] != -1 { continue; }
        let axis = p_nrm[a].cross(p_nrm[b]);
        if axis.length() < 0.04 { continue; }
        let axis = axis.normalized();
        let l = links[&(a, b)];
        if l.dir.dot(axis).abs() < 0.97 || !normals_ok(a, axis) || !normals_ok(b, axis) { continue; }
        let mut members = vec![a, b];
        let pts_of = |ms: &[usize]| -> Vec<Vec3> { let mut s: HashSet<u32> = HashSet::new(); for &q in ms { for &v in &p_verts[q] { s.insert(v); } } s.into_iter().map(|v| t.wp[v as usize]).collect() };
        let Some((mut c, mut r)) = fit_circle(&pts_of(&members), axis) else { continue };
        if r < 3.0 * tol { continue; }
        let worst = |ms: &[usize], c: Vec3, r: f64| -> f64 { ms.iter().flat_map(|&q| p_verts[q].iter()).map(|&v| (dist_to_axis(t.wp[v as usize], c, axis) - r).abs()).fold(0.0, f64::max) };
        if worst(&members, c, r) > tol { continue; }
        // grow over neighbouring patches
        let mut changed = true;
        while changed {
            changed = false;
            let mut cand: Vec<usize> = members.iter().flat_map(|&q| nbr[q].iter().copied()).filter(|q| in_cyl[*q] == -1 && !members.contains(q)).collect();
            cand.sort_unstable();
            cand.dedup();
            for q in cand {
                if !normals_ok(q, axis) { continue; }
                if p_verts[q].iter().any(|&v| (dist_to_axis(t.wp[v as usize], c, axis) - r).abs() > tol) { continue; }
                let mut trial = members.clone();
                trial.push(q);
                if let Some((c2, r2)) = fit_circle(&pts_of(&trial), axis) {
                    if worst(&trial, c2, r2) <= tol && (r2 - r).abs() <= 0.1 * r + tol { members = trial; c = c2; r = r2; changed = true; }
                }
            }
        }
        if members.len() < 3 { continue; }
        let err = worst(&members, c, r);
        let id = cyls.len() as i32;
        for &q in &members { in_cyl[q] = id; }
        cyls.push(Cyl { patches: members, c, r, axis, err });
    }

    // ---------- 4. spheres and free-form patches among the remaining smooth neighbours
    let mut comp = vec![-1i32; np];
    let mut comps: Vec<Vec<usize>> = Vec::new();
    for s in 0..np {
        if in_cyl[s] != -1 || comp[s] >= 0 { continue; }
        let id = comps.len() as i32;
        let mut st = vec![s];
        comp[s] = id;
        let mut list = Vec::new();
        while let Some(q) = st.pop() {
            list.push(q);
            for &w in &nbr[q] { if in_cyl[w] == -1 && comp[w] < 0 { comp[w] = id; st.push(w); } }
        }
        comps.push(list);
    }

    // ---------- 5. assemble surfaces. group id per patch
    // kinds: 0 plane patch (own surface, merged later), cylinder, sphere, freeform
    let mut group = vec![-1i32; np];
    let mut gk: Vec<(&'static str, Vec3, Vec3, f64, f64, f64)> = Vec::new(); // kind, centre, axis, radius, sweep, err
    // coaxial cylinders that touch smoothly are one surface of revolution (a spool wall, a stepped turned part)
    let nc = cyls.len();
    let mut cpar: Vec<usize> = (0..nc).collect();
    fn cfind(p: &mut Vec<usize>, mut x: usize) -> usize { while p[x] != x { p[x] = p[p[x]]; x = p[x]; } x }
    for (&(a, b), l) in &links {
        let (ca, cb) = (in_cyl[a], in_cyl[b]);
        if ca < 0 || cb < 0 || ca == cb { continue; }
        if l.ang / l.len.max(1e-12) > prm.smooth_deg { continue; }
        let (x, y) = (&cyls[ca as usize], &cyls[cb as usize]);
        if x.axis.dot(y.axis).abs() < 0.995 { continue; }
        let d = y.c - x.c;
        if (d - x.axis * d.dot(x.axis)).length() > 3.0 * tol { continue; }
        let (ra, rb) = (cfind(&mut cpar, ca as usize), cfind(&mut cpar, cb as usize));
        if ra != rb { cpar[ra] = rb; }
    }
    let mut cgroup: HashMap<usize, Vec<usize>> = HashMap::new();
    for i in 0..nc { let r = cfind(&mut cpar, i); cgroup.entry(r).or_default().push(i); }
    let mut croots: Vec<usize> = cgroup.keys().copied().collect();
    croots.sort_unstable();
    for r in croots {
        let mem = &cgroup[&r];
        let g = gk.len() as i32;
        let mut pts_all: Vec<usize> = Vec::new();
        for &ci in mem { for &q in &cyls[ci].patches { group[q] = g; pts_all.push(q); } }
        let big = mem.iter().copied().max_by(|&x, &y| cyls[x].patches.len().cmp(&cyls[y].patches.len())).unwrap();
        let cy = &cyls[big];
        let rmin = mem.iter().map(|&i| cyls[i].r).fold(f64::MAX, f64::min);
        let rmax = mem.iter().map(|&i| cyls[i].r).fold(0.0, f64::max);
        let err = mem.iter().map(|&i| cyls[i].err).fold(0.0, f64::max);
        let mut angs: Vec<f64> = Vec::new();
        let helper = if cy.axis.x.abs() < 0.9 { Vec3::new(1.0, 0.0, 0.0) } else { Vec3::new(0.0, 1.0, 0.0) };
        let u = cy.axis.cross(helper).normalized();
        let v = cy.axis.cross(u).normalized();
        for &q in &pts_all { let n = p_nrm[q]; angs.push(n.dot(v).atan2(n.dot(u)).to_degrees()); }
        angs.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let mut gap = if angs.len() > 1 { angs[0] + 360.0 - angs[angs.len() - 1] } else { 360.0 };
        for w in angs.windows(2) { gap = gap.max(w[1] - w[0]); }
        let step_guess = if angs.len() > 1 { 360.0 / angs.len() as f64 } else { 0.0 };
        let sweep = if gap > 2.0 * step_guess.max(45.0) || angs.len() < 3 { (360.0 - gap + step_guess).clamp(0.0, 360.0) } else { 360.0 };
        let kind = if rmax - rmin > 2.0 * tol { "revolution" } else { "cylinder" };
        gk.push((kind, cy.c, cy.axis, if kind == "revolution" { rmax } else { cy.r }, sweep, err / t.diag));
    }
    for (list, mid, ax) in &rev_groups {
        let g = gk.len() as i32;
        for &q in list { group[q] = g; }
        // radius against height along the axis: constant = cylinder, straight line = cone, otherwise a general surface of revolution
        let mut set: HashSet<u32> = HashSet::new();
        for &q in list { for &v in &p_verts[q] { set.insert(v); } }
        let rh: Vec<(f64, f64)> = set.iter().map(|&v| { let d = t.wp[v as usize] - *mid; (d.dot(*ax), (d - *ax * d.dot(*ax)).length()) }).collect();
        let (rmin, rmax) = rh.iter().fold((f64::MAX, 0.0f64), |a, b| (a.0.min(b.1), a.1.max(b.1)));
        let n = rh.len() as f64;
        let (mh, mr) = (rh.iter().map(|x| x.0).sum::<f64>() / n, rh.iter().map(|x| x.1).sum::<f64>() / n);
        let (sxx, sxy) = rh.iter().fold((0.0, 0.0), |a, x| (a.0 + (x.0 - mh) * (x.0 - mh), a.1 + (x.0 - mh) * (x.1 - mr)));
        let k = if sxx > 1e-12 { sxy / sxx } else { 0.0 };
        let lin = rh.iter().map(|x| (x.1 - (mr + k * (x.0 - mh))).abs()).fold(0.0, f64::max);
        let sph_pts: Vec<Vec3> = set.iter().map(|&v| t.wp[v as usize]).collect();
        let sphere = fit_sphere(&sph_pts).filter(|(c, r)| *r > 3.0 * tol && sph_pts.iter().all(|p| (p.distance(*c) - r).abs() <= 2.0 * tol));
        if let Some((c, r)) = sphere {
            if rmax - rmin > 2.0 * tol && lin > 2.0 * tol {
                gk.push(("sphere", c, Vec3::ZERO, r, 0.0, 0.0));
                continue;
            }
        }
        let kind = if rmax - rmin <= 2.0 * tol { "cylinder" } else if lin <= 2.0 * tol { "cone" } else { "revolution" };
        let ang = if kind == "cone" { k.atan().abs().to_degrees() } else { 0.0 };
        gk.push((kind, *mid, *ax, if kind == "cylinder" { mr } else { rmax }, if kind == "cone" { ang } else { 360.0 }, 0.0));
    }
    let p_cen_early = |q: usize| -> Vec3 { let mut sm = Vec3::ZERO; let mut w = 0.0; for &f in &p_tris[q] { let c = t.tv[f].iter().map(|&v| t.wp[v as usize]).fold(Vec3::ZERO, |a, b| a + b) * (1.0 / 3.0); sm += c * t.area[f]; w += t.area[f]; } sm * (1.0 / w.max(1e-30)) };
    for list in &comps {
        if list.len() >= 4 {
            let mut set: HashSet<u32> = HashSet::new();
            for &q in list { for &v in &p_verts[q] { set.insert(v); } }
            let pts: Vec<Vec3> = set.into_iter().map(|v| t.wp[v as usize]).collect();
            let tol_s = 2.0 * tol;
            let planes: Vec<(Vec3, Vec3, f64)> = list.iter().map(|&q| (p_nrm[q], p_cen_early(q), p_area[q])).collect();
            let cone = fit_cone(&planes).filter(|&(_, _, _, worst)| worst <= 2.0 * tol);
            let sph = if cone.is_some() { None } else {
                fit_sphere(&pts).filter(|(c, r)| {
                    *r > 3.0 * tol && pts.iter().all(|p| (p.distance(*c) - r).abs() <= tol_s)
                        && list.iter().all(|&q| { let d = p_cen_early(q) - *c; d.length() > 1e-9 && p_nrm[q].dot(d.normalized()).abs() > 0.978 })
                })
            };
            let g = gk.len() as i32;
            for &q in list { group[q] = g; }
            if let Some((apex, axis, half, worst)) = cone {
                // half angle between the axis and the surface line = 90 - angle between normal and axis
                gk.push(("cone", apex, axis, 0.0, 90.0 - half, worst / t.diag));
            } else if let Some((c, r)) = sph {
                let e = pts.iter().map(|p| (p.distance(c) - r).abs()).fold(0.0, f64::max);
                gk.push(("sphere", c, Vec3::ZERO, r, 0.0, e / t.diag));
            } else {
                gk.push(("freeform", Vec3::ZERO, Vec3::ZERO, 0.0, 0.0, 0.0));
            }
        }
    }
    // coplanar neighbours that region growing split apart are one plane
    let mut par: Vec<usize> = (0..np).collect();
    fn find(p: &mut Vec<usize>, mut x: usize) -> usize { while p[x] != x { p[x] = p[p[x]]; x = p[x]; } x }
    let p_cen: Vec<Vec3> = p_tris.iter().map(|l| { let mut s = Vec3::ZERO; let mut w = 0.0; for &f in l { let c = t.tv[f].iter().map(|&v| t.wp[v as usize]).fold(Vec3::ZERO, |a, b| a + b) * (1.0 / 3.0); s += c * t.area[f]; w += t.area[f]; } s * (1.0 / w.max(1e-30)) }).collect();
    for (&(a, b), _) in &links {
        if group[a] >= 0 || group[b] >= 0 { continue; }
        if p_nrm[a].dot(p_nrm[b]) >= (2.0f64).to_radians().cos() && (p_cen[b] - p_cen[a]).dot(p_nrm[a]).abs() <= tol {
            let (ra, rb) = (find(&mut par, a), find(&mut par, b));
            if ra != rb { par[ra] = rb; }
        }
    }

    // surface id per patch
    let mut surf_of_patch = vec![-1i32; np];
    let mut surfaces: Vec<Surface> = Vec::new();
    let mut gmap: HashMap<i32, i32> = HashMap::new();
    let mut pmap: HashMap<usize, i32> = HashMap::new();
    for q in 0..np {
        let sid = if group[q] >= 0 {
            *gmap.entry(group[q]).or_insert_with(|| {
                let k = &gk[group[q] as usize];
                surfaces.push(Surface { kind: k.0, center: [k.1.x, k.1.y, k.1.z], axis: [k.2.x, k.2.y, k.2.z], radius: k.3, sweep: k.4, err: k.5, band: -1, ..Default::default() });
                (surfaces.len() - 1) as i32
            })
        } else {
            let r = find(&mut par, q);
            *pmap.entry(r).or_insert_with(|| {
                let n = p_nrm[r];
                surfaces.push(Surface { kind: "plane", axis: [n.x, n.y, n.z], center: [p_cen[r].x, p_cen[r].y, p_cen[r].z], band: -1, ..Default::default() });
                (surfaces.len() - 1) as i32
            })
        };
        surf_of_patch[q] = sid;
    }
    // bands become surfaces too
    let mut band_surf: HashMap<i32, i32> = HashMap::new();
    for (i, b) in rep.bands.bands.iter().enumerate() {
        surfaces.push(Surface { kind: b.kind, radius: b.radius, band: i as i32, ..Default::default() });
        band_surf.insert(i as i32, (surfaces.len() - 1) as i32);
    }
    let mut tsurf = vec![-1i32; nf];
    for f in 0..nf {
        tsurf[f] = if band_of[f] >= 0 { band_surf[&band_of[f]] } else if patch[f] >= 0 { surf_of_patch[patch[f] as usize] } else { -1 };
    }
    // a chamfer is a flat strip with sharp edges to both sides: a chamfer triangle that meets a plain surface almost
    // tangentially (under 6 degrees) is really a piece of that surface that the detector swept in (a seam of the STL),
    // so it goes back to it (two passes: a short run of such triangles is peeled from the outside)
    {
        let cos_t = 6.0f64.to_radians().cos();
        for _pass in 0..2 {
            let mut moves: Vec<(usize, i32)> = Vec::new();
            for f in 0..nf {
                let s = tsurf[f];
                if s < 0 || surfaces[s as usize].band < 0 || surfaces[s as usize].kind != "chamfer" { continue; }
                let mut best: Option<(i32, f64)> = None;
                for i in 0..3 {
                    let g = t.adj[f][i];
                    if g < 0 { continue; }
                    let b = tsurf[g as usize];
                    if b < 0 || surfaces[b as usize].band >= 0 { continue; }
                    let c = t.nrm[f].dot(t.nrm[g as usize]);
                    if c >= cos_t && best.map_or(true, |(_, bc)| c > bc) { best = Some((b, c)); }
                }
                if let Some((b, _)) = best { moves.push((f, b)); }
            }
            if moves.is_empty() { break; }
            for (f, b) in moves { tsurf[f] = b; }
        }
        // the polygons that lost every triangle no longer belong to the band
        let mut keep = vec![false; m.sizes.len()];
        for f in 0..nf { let s = tsurf[f]; if s >= 0 && surfaces[s as usize].band >= 0 { keep[t.own[f] as usize] = true; } }
        for q in 0..keep.len() { if !keep[q] { rep.bands.face_band[q] = -1; } }
    }
    // tiny leftovers (slivers between surfaces, fragments of a fine cylinder) join the neighbour they touch the most
    {
        let total: f64 = t.area.iter().sum();
        let thr = prm.min_area_rel * total;
        for _pass in 0..4 {
            let mut sa = vec![0.0f64; surfaces.len()];
            for f in 0..nf { if tsurf[f] >= 0 { sa[tsurf[f] as usize] += t.area[f]; } }
            let mut share: HashMap<(i32, i32), f64> = HashMap::new();
            for f in 0..nf {
                for i in 0..3 {
                    let g = t.adj[f][i];
                    if g < 0 { continue; }
                    let (a, b) = (tsurf[f], tsurf[g as usize]);
                    if a < 0 || b < 0 || a == b { continue; }
                    *share.entry((a, b)).or_insert(0.0) += t.elen[f][i];
                }
            }
            let mut best: HashMap<i32, (i32, f64)> = HashMap::new();
            for (&(a, b), &l) in &share {
                if sa[a as usize] >= thr || surfaces[a as usize].band >= 0 { continue; }
                // never swallow a surface into a band; a bigger plain surface is the target
                if surfaces[b as usize].band >= 0 { continue; }
                let e = best.entry(a).or_insert((b, 0.0));
                if l > e.1 || (l == e.1 && b < e.0) { *e = (b, l); }
            }
            if best.is_empty() { break; }
            let mut to: Vec<i32> = (0..surfaces.len() as i32).collect();
            let mut keys: Vec<i32> = best.keys().copied().collect();
            keys.sort_by(|&x, &y| sa[x as usize].partial_cmp(&sa[y as usize]).unwrap_or(std::cmp::Ordering::Equal));
            for a in keys {
                let (b, _) = best[&a];
                let mut r = b;
                while to[r as usize] != r { r = to[r as usize]; }
                if r != a { to[a as usize] = r; }
            }
            for f in 0..nf { if tsurf[f] >= 0 { let mut r = tsurf[f]; while to[r as usize] != r { r = to[r as usize]; } tsurf[f] = r; } }
        }
        // drop the surfaces that became empty (their area is zero after the loop below, then they are removed in the renumbering)
    }
    // neighbouring spheres with the same centre and radius are one sphere (a pole cap cut off by fine fans)
    {
        let mut to: Vec<i32> = (0..surfaces.len() as i32).collect();
        for f in 0..nf {
            for i in 0..3 {
                let g = t.adj[f][i];
                if g < 0 { continue; }
                let (a, b) = (tsurf[f], tsurf[g as usize]);
                if a < 0 || b < 0 || a == b { continue; }
                let (sa, sb) = (&surfaces[a as usize], &surfaces[b as usize]);
                if sa.kind != "sphere" || sb.kind != "sphere" { continue; }
                let d = Vec3::new(sa.center[0] - sb.center[0], sa.center[1] - sb.center[1], sa.center[2] - sb.center[2]).length();
                if d <= 2.0 * tol && (sa.radius - sb.radius).abs() <= 2.0 * tol {
                    let (mut ra, mut rb) = (a, b);
                    while to[ra as usize] != ra { ra = to[ra as usize]; }
                    while to[rb as usize] != rb { rb = to[rb as usize]; }
                    if ra != rb { to[ra.max(rb) as usize] = ra.min(rb); }
                }
            }
        }
        for f in 0..nf { if tsurf[f] >= 0 { let mut r = tsurf[f]; while to[r as usize] != r { r = to[r as usize]; } tsurf[f] = r; } }
    }
    // slivers narrower than a band can be, and small curved fragments that join a free-form surface smoothly,
    // belong to the surface next to them (a thin ring cut off a revolution wall, a fold line across a free-form top)
    {
        let total: f64 = t.area.iter().sum();
        let min_w = 0.003 * t.diag;
        let sharp_cos = prm.sharp_deg.to_radians().cos();
        for _pass in 0..4 {
            let n = surfaces.len();
            let mut sa = vec![0.0f64; n];
            for f in 0..nf { if tsurf[f] >= 0 { sa[tsurf[f] as usize] += t.area[f]; } }
            let mut share: HashMap<(i32, i32), (f64, f64)> = HashMap::new();
            let mut perim = vec![0.0f64; n];
            for f in 0..nf {
                for i in 0..3 {
                    let g = t.adj[f][i];
                    if g < 0 || (g as usize) < f { continue; }
                    let (a, b) = (tsurf[f], tsurf[g as usize]);
                    if a < 0 || b < 0 || a == b { continue; }
                    let tang = if t.nrm[f].dot(t.nrm[g as usize]) >= sharp_cos { t.elen[f][i] } else { 0.0 };
                    for key in [(a, b), (b, a)] {
                        let e = share.entry(key).or_insert((0.0, 0.0));
                        e.0 += t.elen[f][i]; e.1 += tang;
                    }
                    perim[a as usize] += t.elen[f][i]; perim[b as usize] += t.elen[f][i];
                }
            }
            let mut to: Vec<i32> = (0..n as i32).collect();
            let mut changed = false;
            for s_id in 0..n {
                let sf = &surfaces[s_id];
                if sf.band >= 0 || sf.kind == "plane" || sa[s_id] <= 0.0 || perim[s_id] <= 0.0 { continue; }
                let width = 2.0 * sa[s_id] / perim[s_id];
                let sliver = width < min_w;
                let mut tang_total = 0.0;
                let mut best: Option<(i32, f64)> = None;       // best non-band neighbour by shared length
                let mut best_ff: Option<(i32, f64)> = None;    // best free-form neighbour by tangent length
                for (&(a, b), &(len, tang)) in &share {
                    if a as usize != s_id || surfaces[b as usize].band >= 0 { continue; }
                    tang_total += tang;
                    if best.map_or(true, |(_, l)| len > l) { best = Some((b, len)); }
                    if surfaces[b as usize].kind == "freeform" && best_ff.map_or(true, |(_, l)| tang > l) { best_ff = Some((b, tang)); }
                }
                let smooth = sa[s_id] < 0.03 * total && width < 0.01 * t.diag && tang_total >= 0.7 * perim[s_id] && best_ff.is_some();
                let target = if smooth { best_ff.map(|x| x.0) } else if sliver { best.map(|x| x.0) } else { None };
                if let Some(tg) = target {
                    let mut r = tg;
                    while to[r as usize] != r { r = to[r as usize]; }
                    if r as usize != s_id { to[s_id] = r; changed = true; }
                }
            }
            // free-form patches that touch smoothly are one free-form surface
            for (&(a, b), &(_, tang)) in &share {
                if a >= b { continue; }
                if surfaces[a as usize].kind != "freeform" || surfaces[b as usize].kind != "freeform" { continue; }
                if tang >= 0.2 * perim[a as usize].min(perim[b as usize]) {
                    let (mut ra, mut rb) = (a, b);
                    while to[ra as usize] != ra { ra = to[ra as usize]; }
                    while to[rb as usize] != rb { rb = to[rb as usize]; }
                    if ra != rb { to[ra.max(rb) as usize] = ra.min(rb); changed = true; }
                }
            }
            if !changed { break; }
            for f in 0..nf { if tsurf[f] >= 0 { let mut r = tsurf[f]; while to[r as usize] != r { r = to[r as usize]; } tsurf[f] = r; } }
        }
    }
    // planes: refine centre / normal over all of their triangles
    for (sid, s) in surfaces.iter_mut().enumerate() {
        let mut lo = Vec3::new(f64::MAX, f64::MAX, f64::MAX);
        let mut hi = Vec3::new(f64::MIN, f64::MIN, f64::MIN);
        let mut ps: HashSet<usize> = HashSet::new();
        let mut nsum = Vec3::ZERO;
        let mut csum = Vec3::ZERO;
        for f in 0..nf {
            if tsurf[f] != sid as i32 { continue; }
            s.triangles += 1;
            s.area += t.area[f];
            if patch[f] >= 0 { ps.insert(patch[f] as usize); }
            nsum += t.nrm[f] * t.area[f];
            csum += t.tv[f].iter().map(|&v| t.wp[v as usize]).fold(Vec3::ZERO, |a, b| a + b) * (t.area[f] / 3.0);
            for &v in &t.tv[f] { let p = t.wp[v as usize]; lo = Vec3::new(lo.x.min(p.x), lo.y.min(p.y), lo.z.min(p.z)); hi = Vec3::new(hi.x.max(p.x), hi.y.max(p.y), hi.z.max(p.z)); }
        }
        s.patches = ps.len().max(1);
        s.bbox = [lo.x, lo.y, lo.z, hi.x, hi.y, hi.z];
        if s.kind == "plane" && s.area > 0.0 {
            let n = nsum.normalized();
            let c = csum * (1.0 / s.area);
            s.axis = [n.x, n.y, n.z];
            s.center = [c.x, c.y, c.z];
        }
    }

    // ---------- 6. borders between surfaces
    let mut bmap: HashMap<(i32, i32), (f64, usize, f64)> = HashMap::new();
    for f in 0..nf {
        for i in 0..3 {
            let g = t.adj[f][i];
            if g < 0 { continue; }
            let g = g as usize;
            if g < f { continue; }
            let (a, b) = (tsurf[f], tsurf[g]);
            if a == b || a < 0 || b < 0 { continue; }
            let key = if a < b { (a, b) } else { (b, a) };
            let e = bmap.entry(key).or_insert((0.0, 0, 0.0));
            e.0 += t.elen[f][i];
            e.1 += 1;
            e.2 += angle(t.nrm[f], t.nrm[g]) * t.elen[f][i];
        }
    }
    let mut borders: Vec<Border> = bmap.iter().map(|(&(a, b), &(len, n, sa))| {
        let ang = sa / len.max(1e-12);
        let band = surfaces[a as usize].band >= 0 || surfaces[b as usize].band >= 0;
        Border { a, b, length: len, edges: n, angle: ang, kind: if band { "band" } else if ang >= prm.sharp_deg { "sharp" } else { "tangent" } }
    }).collect();
    borders.sort_by(|x, y| (x.a, x.b).cmp(&(y.a, y.b)));
    for b in &borders { surfaces[b.a as usize].perimeter += b.length; surfaces[b.b as usize].perimeter += b.length; }

    // ---------- 7. renumber by decreasing area (stable, bands included), then per polygon
    let mut ord: Vec<usize> = (0..surfaces.len()).filter(|&i| surfaces[i].triangles > 0).collect();
    ord.sort_by(|&a, &b| surfaces[b].area.partial_cmp(&surfaces[a].area).unwrap_or(std::cmp::Ordering::Equal).then(a.cmp(&b)));
    let mut newid = vec![0i32; surfaces.len()];
    for (n, &o) in ord.iter().enumerate() { newid[o] = n as i32; }
    let _ = &newid;
    rep.surfaces = ord.iter().map(|&o| surfaces[o].clone()).collect();
    for b in borders.iter_mut() { b.a = newid[b.a as usize]; b.b = newid[b.b as usize]; if b.a > b.b { std::mem::swap(&mut b.a, &mut b.b); } }
    rep.borders = borders;
    // polygon surface = the one covering most of its area
    let mut acc: Vec<HashMap<i32, f64>> = vec![HashMap::new(); m.sizes.len()];
    for f in 0..nf { if tsurf[f] >= 0 { *acc[t.own[f] as usize].entry(newid[tsurf[f] as usize]).or_insert(0.0) += t.area[f]; } }
    for (k, a) in acc.iter().enumerate() {
        let mut best = (-1i32, -1.0);
        for (&s, &w) in a { if w > best.1 || (w == best.1 && s < best.0) { best = (s, w); } }
        rep.face_surface[k] = best.0;
    }
    rep
}

#[cfg(test)]
mod tests {
    use super::*;

    fn prism(n: usize, r: f64, h: f64) -> PolyMesh {
        let mut p = Vec::new();
        for k in 0..n {
            let a = std::f64::consts::TAU * k as f64 / n as f64;
            p.push(Vec3::new(r * a.cos(), r * a.sin(), 0.0));
            p.push(Vec3::new(r * a.cos(), r * a.sin(), h));
        }
        let mut idx: Vec<i32> = Vec::new();
        let mut sizes: Vec<i32> = Vec::new();
        for k in 0..n {
            let k2 = (k + 1) % n;
            idx.extend_from_slice(&[(2 * k) as i32, (2 * k2) as i32, (2 * k2 + 1) as i32, (2 * k + 1) as i32]);
            sizes.push(4);
        }
        // caps as polygons
        idx.extend((0..n).rev().map(|k| (2 * k) as i32));
        sizes.push(n as i32);
        idx.extend((0..n).map(|k| (2 * k + 1) as i32));
        sizes.push(n as i32);
        PolyMesh { p, sizes, idx, ..Default::default() }
    }

    fn frustum(n: usize, r0: f64, r1: f64, h: f64) -> PolyMesh {
        let mut p = Vec::new();
        for k in 0..n {
            let a = std::f64::consts::TAU * k as f64 / n as f64;
            p.push(Vec3::new(r0 * a.cos(), r0 * a.sin(), 0.0));
            p.push(Vec3::new(r1 * a.cos(), r1 * a.sin(), h));
        }
        let mut idx: Vec<i32> = Vec::new();
        let mut sizes: Vec<i32> = Vec::new();
        for k in 0..n {
            let k2 = (k + 1) % n;
            idx.extend_from_slice(&[(2 * k) as i32, (2 * k2) as i32, (2 * k2 + 1) as i32, (2 * k + 1) as i32]);
            sizes.push(4);
        }
        idx.extend((0..n).rev().map(|k| (2 * k) as i32));
        sizes.push(n as i32);
        idx.extend((0..n).map(|k| (2 * k + 1) as i32));
        sizes.push(n as i32);
        PolyMesh { p, sizes, idx, ..Default::default() }
    }

    #[test]
    fn polygonal_frustum_is_a_cone() {
        let m = frustum(24, 8.0, 4.0, 6.0);
        let r = analyze(&m, &SurfParams::default());
        let kinds: Vec<_> = r.surfaces.iter().map(|s| s.kind).collect();
        assert_eq!(kinds.iter().filter(|k| **k == "cone").count(), 1, "{:?}", kinds);
        let c = r.surfaces.iter().find(|s| s.kind == "cone").unwrap();
        let expect = (4.0f64 / 6.0).atan().to_degrees();
        assert!((c.sweep - expect).abs() < 0.5, "half angle {} expected {}", c.sweep, expect);
    }

    #[test]
    fn uv_sphere_is_a_sphere() {
        let (nu, nv, r) = (24usize, 14usize, 6.0f64);
        let mut p = vec![Vec3::new(0.0, 0.0, r), Vec3::new(0.0, 0.0, -r)];
        for j in 1..nv {
            let th = std::f64::consts::PI * j as f64 / nv as f64;
            for i in 0..nu {
                let a = std::f64::consts::TAU * i as f64 / nu as f64;
                p.push(Vec3::new(r * th.sin() * a.cos(), r * th.sin() * a.sin(), r * th.cos()));
            }
        }
        let at = |j: usize, i: usize| (2 + (j - 1) * nu + i % nu) as i32;
        let (mut idx, mut sizes) = (Vec::new(), Vec::new());
        for i in 0..nu { idx.extend_from_slice(&[0, at(1, i), at(1, i + 1)]); sizes.push(3); }
        for j in 1..nv - 1 { for i in 0..nu {
            idx.extend_from_slice(&[at(j, i), at(j + 1, i), at(j + 1, i + 1)]); sizes.push(3);
            idx.extend_from_slice(&[at(j, i), at(j + 1, i + 1), at(j, i + 1)]); sizes.push(3);
        } }
        for i in 0..nu { idx.extend_from_slice(&[1, at(nv - 1, i + 1), at(nv - 1, i)]); sizes.push(3); }
        let m = PolyMesh { p, sizes, idx, ..Default::default() };
        let rep = analyze(&m, &SurfParams::default());
        let kinds: Vec<_> = rep.surfaces.iter().map(|s| s.kind).collect();
        assert_eq!(kinds.iter().filter(|k| **k == "sphere").count(), 1, "{:?}", kinds);
    }

    #[test]
    fn tilted_frustum_is_a_cone_too() {
        let mut m = frustum(24, 8.0, 4.0, 6.0);
        let (c, s) = (20.0f64.to_radians().cos(), 20.0f64.to_radians().sin());
        for p in m.p.iter_mut() { *p = Vec3::new(p.x, c * p.y - s * p.z, s * p.y + c * p.z); }
        let r = analyze(&m, &SurfParams::default());
        let kinds: Vec<_> = r.surfaces.iter().map(|s| s.kind).collect();
        assert_eq!(kinds.iter().filter(|k| **k == "cone").count(), 1, "{:?}", kinds);
        let cone = r.surfaces.iter().find(|s| s.kind == "cone").unwrap();
        let expect = (4.0f64 / 6.0).atan().to_degrees();
        assert!((cone.sweep - expect).abs() < 0.5, "half angle {} expected {}", cone.sweep, expect);
    }

    #[test]
    fn polygonal_cylinder_is_one_cylinder_and_two_planes() {
        let m = prism(24, 5.0, 10.0);
        let r = analyze(&m, &SurfParams::default());
        let cyl: Vec<_> = r.surfaces.iter().filter(|s| s.kind == "cylinder").collect();
        let planes = r.surfaces.iter().filter(|s| s.kind == "plane").count();
        assert_eq!(cyl.len(), 1, "{:?}", r.surfaces.iter().map(|s| s.kind).collect::<Vec<_>>());
        assert_eq!(planes, 2);
        assert!((cyl[0].radius - 5.0).abs() < 0.01);
        assert!(r.borders.iter().all(|b| b.kind == "sharp"));
    }
}
