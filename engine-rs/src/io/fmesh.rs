//! Binary mesh exchange format (.fmesh), little endian.
//!
//! ```text
//! "FMSH"  u32 version (1)  u32 nV nPoly nIdx nFeat flags
//! f32 positions[3*nV]   i32 polygon sizes[nPoly]   i32 polygon indices[nIdx]
//! i32 feature edges[2*nFeat]      vertex pairs that must stay as lines
//! f32 density[nV]                 if flags & 1 (edge length multiplier)
//! f32 field[3*nV]                 if flags & 2 (unused, reserved)
//! u32 nGuide, f32 guides[6*nGuide] if flags & 4 (flow guide segments)
//! ```
use crate::math::Vec3;
use std::fs;

#[derive(Clone, Debug, Default)]
pub struct PolyMesh {
    pub p: Vec<Vec3>,
    pub sizes: Vec<i32>,
    pub idx: Vec<i32>,
    pub features: Vec<[i32; 2]>,
    /// Empty when not given.
    pub density: Vec<f32>,
    /// Flow guide segments (3D end points): soft hints for the quad rows.
    pub guides: Vec<[Vec3; 2]>,
}

struct Reader<'a> { data: &'a [u8], at: usize }

impl<'a> Reader<'a> {
    fn take(&mut self, n: usize, what: &str) -> Result<&'a [u8], String> {
        if self.at + n > self.data.len() { return Err(format!("truncated file while reading {what}")); }
        let s = &self.data[self.at..self.at + n];
        self.at += n;
        Ok(s)
    }
    fn u32s(&mut self, n: usize, what: &str) -> Result<Vec<u32>, String> {
        Ok(self.take(n * 4, what)?.chunks_exact(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect())
    }
    fn i32s(&mut self, n: usize, what: &str) -> Result<Vec<i32>, String> {
        Ok(self.take(n * 4, what)?.chunks_exact(4).map(|c| i32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect())
    }
    fn f32s(&mut self, n: usize, what: &str) -> Result<Vec<f32>, String> {
        Ok(self.take(n * 4, what)?.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect())
    }
}

pub fn read(path: &str) -> Result<PolyMesh, String> {
    let data = fs::read(path).map_err(|_| format!("cannot open input file: {path}"))?;
    let mut r = Reader { data: &data, at: 0 };
    if r.take(4, "magic").map_or(true, |m| m != b"FMSH") { return Err("not an FMSH file".into()); }
    let hdr = r.u32s(6, "header").map_err(|_| "truncated header".to_string())?;
    if hdr[0] != 1 { return Err("unsupported FMSH version".into()); }
    let (n_v, n_poly, n_idx, n_feat, flags) = (hdr[1] as usize, hdr[2] as usize, hdr[3] as usize, hdr[4] as usize, hdr[5]);
    let pos = r.f32s(n_v * 3, "positions")?;
    let mut m = PolyMesh::default();
    m.p = pos.chunks_exact(3).map(|c| Vec3::new(c[0] as f64, c[1] as f64, c[2] as f64)).collect();
    m.sizes = r.i32s(n_poly, "polygon sizes")?;
    m.idx = r.i32s(n_idx, "polygon indices")?;
    let feat = r.i32s(n_feat * 2, "feature edges")?;
    m.features = feat.chunks_exact(2).map(|c| [c[0], c[1]]).collect();
    if flags & 1 != 0 { m.density = r.f32s(n_v, "density")?; }
    if flags & 2 != 0 { r.f32s(n_v * 3, "field")?; }
    if flags & 4 != 0 {
        let ng = r.u32s(1, "guides")?[0] as usize;
        let g = r.f32s(ng * 6, "guides")?;
        m.guides = g.chunks_exact(6)
            .map(|c| [Vec3::new(c[0] as f64, c[1] as f64, c[2] as f64), Vec3::new(c[3] as f64, c[4] as f64, c[5] as f64)])
            .collect();
    }
    let mut total = 0usize;
    for &s in &m.sizes {
        if s < 3 { return Err("polygon with fewer than 3 vertices".into()); }
        total += s as usize;
    }
    if total != n_idx { return Err("polygon sizes do not match index count".into()); }
    if m.idx.iter().any(|&i| i < 0 || i as usize >= n_v) { return Err("vertex index out of range".into()); }
    if m.features.iter().any(|e| e.iter().any(|&i| i < 0 || i as usize >= n_v)) {
        return Err("feature edge index out of range".into());
    }
    Ok(m)
}

pub fn write(path: &str, m: &PolyMesh) -> Result<(), String> {
    let flags: u32 = if m.density.is_empty() { 0 } else { 1 };
    let mut out: Vec<u8> = Vec::with_capacity(28 + m.p.len() * 16 + m.idx.len() * 4);
    out.extend_from_slice(b"FMSH");
    for v in [1u32, m.p.len() as u32, m.sizes.len() as u32, m.idx.len() as u32, m.features.len() as u32, flags] {
        out.extend_from_slice(&v.to_le_bytes());
    }
    for p in &m.p { for c in [p.x, p.y, p.z] { out.extend_from_slice(&(c as f32).to_le_bytes()); } }
    for v in &m.sizes { out.extend_from_slice(&v.to_le_bytes()); }
    for v in &m.idx { out.extend_from_slice(&v.to_le_bytes()); }
    for e in &m.features { for v in e { out.extend_from_slice(&v.to_le_bytes()); } }
    if flags & 1 != 0 { for d in &m.density { out.extend_from_slice(&d.to_le_bytes()); } }
    fs::write(path, out).map_err(|_| format!("cannot open output file: {path}"))
}
