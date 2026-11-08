//! Quality metrics of a polygon mesh, reported as one-line JSON (tests and the add-on read it).
use crate::io::fmesh::PolyMesh;
use crate::math::{clamp, INF, PI};
use crate::mesh::bvh::SurfaceIndex;
use crate::stages::repair::edge_key;

/// C `%.6g` formatting.
pub fn g6(x: f64) -> String {
    if x == 0.0 { return "0".into(); }
    if !x.is_finite() { return if x.is_nan() { "nan".into() } else if x > 0.0 { "inf".into() } else { "-inf".into() }; }
    let s = format!("{:.5e}", x); // d.ddddde[-]X
    let (mant, exp) = s.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    if exp < -4 || exp >= 6 {
        let mant = if mant.contains('.') { mant.trim_end_matches('0').trim_end_matches('.') } else { mant };
        format!("{}e{}{:02}", mant, if exp < 0 { '-' } else { '+' }, exp.abs())
    } else {
        let decimals = (5 - exp).max(0) as usize;
        let f = format!("{:.*}", decimals, x);
        if f.contains('.') { f.trim_end_matches('0').trim_end_matches('.').to_string() } else { f }
    }
}

pub fn metrics_json(m: &PolyMesh, reference: Option<&SurfaceIndex>) -> String {
    let n_v = m.p.len();
    let (mut tris, mut quads, mut other) = (0, 0, 0);
    let mut edges: Vec<u64> = Vec::new();
    let (mut ang_min, mut ang_sum, mut ang_count, mut bad_angles) = (180.0f64, 0.0f64, 0i64, 0i64);
    let mut at = 0usize;
    for &s in &m.sizes {
        let s = s as usize;
        let v = &m.idx[at..at + s];
        at += s;
        match s { 3 => tris += 1, 4 => quads += 1, _ => other += 1 }
        for k in 0..s {
            let (a, b, c) = (v[k], v[(k + 1) % s], v[(k + s - 1) % s]);
            edges.push(edge_key(a, b));
            let e1 = (m.p[b as usize] - m.p[a as usize]).normalized();
            let e2 = (m.p[c as usize] - m.p[a as usize]).normalized();
            let ang = clamp(e1.dot(e2), -1.0, 1.0).acos() * 180.0 / PI;
            ang_min = ang_min.min(ang);
            ang_sum += ang;
            ang_count += 1;
            if s == 3 && (ang < 20.0 || ang > 130.0) { bad_angles += 1; }
            if s == 4 && (ang < 45.0 || ang > 135.0) { bad_angles += 1; }
        }
    }
    edges.sort_unstable();
    let (mut boundary, mut non_manifold, mut unique_edges) = (0, 0, 0);
    let mut valence = vec![0i32; n_v];
    let (mut len_min, mut len_max, mut len_sum) = (INF, 0.0f64, 0.0f64);
    let mut i = 0;
    while i < edges.len() {
        let mut j = i;
        while j < edges.len() && edges[j] == edges[i] { j += 1; }
        let n = j - i;
        unique_edges += 1;
        if n == 1 { boundary += 1; } else if n > 2 { non_manifold += 1; }
        let a = (edges[i] >> 32) as usize;
        let b = (edges[i] & 0xffff_ffff) as usize;
        valence[a] += 1;
        valence[b] += 1;
        let l = m.p[a].distance(m.p[b]);
        len_min = len_min.min(l);
        len_max = len_max.max(l);
        len_sum += l;
        i = j;
    }
    let mut hist = [0i32; 10];
    for &v in &valence { hist[(v as usize).min(9)] += 1; }
    let regular = if tris > 0 && quads == 0 { hist[6] } else { hist[4] };
    let (mut d_mean, mut d_max) = (0.0f64, 0.0f64);
    if let Some(r) = reference {
        if !r.is_empty() {
            let mut sum = 0.0;
            for p in &m.p {
                let d = r.closest_any(*p).d2.sqrt();
                sum += d;
                d_max = d_max.max(d);
            }
            d_mean = if n_v > 0 { sum / n_v as f64 } else { 0.0 };
        }
    }
    let ue = unique_edges as f64;
    format!(
        "{{\"verts\":{},\"faces\":{},\"tris\":{},\"quads\":{},\"other\":{},\"edges\":{},\"boundary_edges\":{},\"nonmanifold_edges\":{},\
\"edge_len_min\":{},\"edge_len_mean\":{},\"edge_len_max\":{},\"angle_min\":{:.3},\"angle_mean\":{:.3},\"bad_angle_corners\":{},\
\"valence\":[{}],\"regular_vertices\":{},\"dist_mean\":{},\"dist_max\":{}}}",
        n_v, m.sizes.len(), tris, quads, other, unique_edges, boundary, non_manifold,
        g6(if unique_edges > 0 { len_min } else { 0.0 }), g6(if unique_edges > 0 { len_sum / ue } else { 0.0 }), g6(len_max),
        ang_min, if ang_count > 0 { ang_sum / ang_count as f64 } else { 0.0 }, bad_angles,
        hist.iter().map(|x| x.to_string()).collect::<Vec<_>>().join(","), regular, g6(d_mean), g6(d_max)
    )
}
