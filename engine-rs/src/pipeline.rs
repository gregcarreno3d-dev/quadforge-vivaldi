//! The remesh pipeline (the order of the stages, the choice of route and the fallbacks).
//!
//!   read -> repair -> [close borders with caps] -> [size field for thin parts] -> isotropic triangle remesh ->
//!     grid route:  field -> integer-grid parametrization -> extraction (up to 3 passes to hit the count) ->
//!                  [drop cap quads, weld] -> checks            (falls back to the pairing route when poor)
//!     pair route:  cross field -> triangle pairing -> subdivision -> relax
//!
//! Work areas: `choose_route` decisions live in `run_inner`; the grid passes are in `grid_route`; the pairing
//! route is `pair_route`.
use crate::cli::Options;
use crate::io::fmesh::{self, PolyMesh};
use crate::math::Vec3;
use crate::mesh::bvh::{SegmentIndex, SurfaceIndex};
use crate::stages::borderweld::{drop_cap_quads, weld_short_edges};
use crate::stages::capping::{cap_borders, stitch_slits, CapInfo};
use crate::stages::extract::{ExtractParams, ExtractStats, Extractor};
use crate::stages::metrics::metrics_json;
use crate::stages::quad::{QuadParams, QuadStats, Quadifier};
use crate::stages::remesh::{RemeshParams, RemeshStats, Remesher};
use crate::stages::repair::{self, TriSoup};
use crate::stages::sizefield::{curvature_size_field, gap_size_field, thickness_size_field, SizeFieldParams, SizeFieldStats};
use crate::util::protocol::{json_safe, say};
use std::collections::{HashMap, HashSet};
use crate::math::geom::closest_point_on_segment;
use std::sync::Arc;

#[derive(Debug, PartialEq)]
pub enum Outcome { Done, Retry, Failed(String) }

fn env_f64(name: &str) -> Option<f64> { std::env::var(name).ok().and_then(|s| s.trim().parse().ok()) }
fn env_set(name: &str) -> bool { std::env::var_os(name).is_some() }
fn dbg() -> bool { env_set("FORGE_DBG") }

fn soup_area(soup: &TriSoup) -> f64 {
    soup.f.iter().map(|t| 0.5 * (soup.p[t[1] as usize] - soup.p[t[0] as usize]).cross(soup.p[t[2] as usize] - soup.p[t[0] as usize]).length()).sum()
}

fn has_open_edges(soup: &TriSoup) -> bool {
    let mut cnt: HashMap<u64, i32> = HashMap::new();
    for t in &soup.f {
        for k in 0..3 {
            let (a, b) = (t[k] as u32 as u64, t[(k + 1) % 3] as u32 as u64);
            *cnt.entry((a.min(b) << 32) | a.max(b)).or_insert(0) += 1;
        }
    }
    cnt.values().any(|&c| c == 1)
}

fn open_edge_count(p: &PolyMesh) -> i32 {
    let mut ec: HashMap<u64, i32> = HashMap::new();
    let mut at = 0usize;
    for &sz in &p.sizes {
        let sz = sz as usize;
        for k in 0..sz {
            let (a, b) = (p.idx[at + k] as u32 as u64, p.idx[at + (k + 1) % sz] as u32 as u64);
            *ec.entry((a.min(b) << 32) | a.max(b)).or_insert(0) += 1;
        }
        at += sz;
    }
    ec.values().filter(|&&c| c == 1).count() as i32
}

fn progress_box() -> crate::stages::remesh::ProgressFn { Box::new(|p, m| say("P", &format!("{p} {m}"))) }

fn remesh_params(o: &Options, target: i32) -> RemeshParams {
    RemeshParams {
        target_faces: target, iterations: o.iterations, threads: o.threads, project: o.project, corner_angle_deg: o.corner_angle_deg,
        check: o.check, progress: Some(progress_box()), ..RemeshParams::default()
    }
}

pub fn run(o: &Options, no_adaptive: bool) -> Outcome {
    match run_inner(o, no_adaptive) { Ok(x) => x, Err(e) => Outcome::Failed(e) }
}

/// Keep only long chains of marked edges (short ones are mostly noise and would only drag vertices around).
fn long_feature_chains(soup: &TriSoup, requested: i32) -> (Vec<[i32; 2]>, Vec<[Vec3; 2]>) {
    let rho0 = (soup_area(soup) / (requested as f64 / 4.0).max(1.0)).sqrt();
    let mut parent: Vec<usize> = (0..soup.p.len()).collect();
    fn find(p: &mut Vec<usize>, mut x: usize) -> usize { while p[x] != x { p[x] = p[p[x]]; x = p[x]; } x }
    for e in &soup.features { let (a, b) = (find(&mut parent, e[0] as usize), find(&mut parent, e[1] as usize)); parent[a] = b; }
    let mut chain: HashMap<usize, f64> = HashMap::new();
    for e in &soup.features {
        let r = find(&mut parent, e[0] as usize);
        *chain.entry(r).or_insert(0.0) += soup.p[e[0] as usize].distance(soup.p[e[1] as usize]);
    }
    let mut keep: Vec<bool> = soup.features.iter().map(|e| { let r = find(&mut parent, e[0] as usize); chain[&r] >= 6.0 * rho0 }).collect();
    // Two marked lines closer than about half a cell (the two edges of a narrow chamfer) cannot both be rows of the
    // lattice: they land on neighbouring rows a fraction of a cell apart and make zero-width quads. Find marked
    // edges that sit near another marked edge which is far away along the chain (or in another chain), group them
    // into runs, and keep only the longer run of each close pair; the lattice then runs through the narrow band.
    let ratio = env_f64("FORGE_THINFEAT").unwrap_or(0.75);
    if ratio > 0.0 {
        let n = soup.features.len();
        let thr = ratio * rho0;
        let cell = (2.0 * thr).max(1e-12);
        let key = |p: Vec3| ((p.x / cell).floor() as i64, (p.y / cell).floor() as i64, (p.z / cell).floor() as i64);
        let mid: Vec<Vec3> = soup.features.iter().map(|e| (soup.p[e[0] as usize] + soup.p[e[1] as usize]) * 0.5).collect();
        let len: Vec<f64> = soup.features.iter().map(|e| soup.p[e[0] as usize].distance(soup.p[e[1] as usize])).collect();
        let mut grid: HashMap<(i64, i64, i64), Vec<usize>> = HashMap::new();
        for i in 0..n { if keep[i] { grid.entry(key(mid[i])).or_default().push(i); } }
        // vertex -> incident kept edges, to measure how far apart two edges are along the chain
        let mut inc: HashMap<i32, Vec<usize>> = HashMap::new();
        for i in 0..n { if keep[i] { for v in soup.features[i] { inc.entry(v).or_default().push(i); } } }
        let hop_limit = 6;
        let near_along = |i: usize| -> Vec<usize> {
            let mut seen: HashSet<usize> = HashSet::new();
            seen.insert(i);
            let mut frontier = vec![i];
            for _ in 0..hop_limit {
                let mut next = Vec::new();
                for &e in &frontier { for v in soup.features[e] { for &g in &inc[&v] { if seen.insert(g) { next.push(g); } } } }
                frontier = next;
            }
            seen.into_iter().collect()
        };
        let mut partner: Vec<Option<usize>> = vec![None; n]; // edge -> a close edge on a different run
        for i in 0..n {
            if !keep[i] { continue; }
            let close_along: HashSet<usize> = near_along(i).into_iter().collect();
            let (kx, ky, kz) = key(mid[i]);
            let mut best = (f64::INFINITY, None);
            for dx in -1..=1 { for dy in -1..=1 { for dz in -1..=1 {
                let Some(list) = grid.get(&(kx + dx, ky + dy, kz + dz)) else { continue };
                for &j in list {
                    if close_along.contains(&j) { continue; }
                    let f = soup.features[j];
                    let (q, _) = closest_point_on_segment(mid[i], soup.p[f[0] as usize], soup.p[f[1] as usize]);
                    let d = q.distance(mid[i]);
                    if d < thr && d < best.0 { best = (d, Some(j)); }
                }
            } } }
            partner[i] = best.1;
        }
        // runs: connected groups of edges that have a partner
        let mut par2: Vec<usize> = (0..n).collect();
        fn root(p: &mut Vec<usize>, mut x: usize) -> usize { while p[x] != x { p[x] = p[p[x]]; x = p[x]; } x }
        for i in 0..n {
            if partner[i].is_none() { continue; }
            for v in soup.features[i] { for &g in &inc[&v] { if partner[g].is_some() { let (a, b) = (root(&mut par2, i), root(&mut par2, g)); par2[a] = b; } } }
        }
        let mut run_len: HashMap<usize, f64> = HashMap::new();
        for i in 0..n { if partner[i].is_some() { let r = root(&mut par2, i); *run_len.entry(r).or_insert(0.0) += len[i]; } }
        let mut dropped_run: HashSet<usize> = HashSet::new();
        let mut order: Vec<usize> = run_len.keys().copied().collect();
        order.sort_by(|a, b| run_len[a].partial_cmp(&run_len[b]).unwrap().then(a.cmp(b))); // shortest first
        let mut pairs: HashMap<usize, HashSet<usize>> = HashMap::new();
        for i in 0..n { if let Some(j) = partner[i] { let (a, b) = (root(&mut par2, i), if partner[j].is_some() { root(&mut par2, j) } else { usize::MAX }); if b != usize::MAX && a != b { pairs.entry(a).or_default().insert(b); } else if b == usize::MAX { pairs.entry(a).or_default().insert(usize::MAX); } } }
        for r in order {
            // drop this run if it faces a longer run that is still kept, or an unmarked-partner (shouldn't happen)
            let faces_longer = pairs.get(&r).map_or(false, |s| s.iter().any(|&o| o != usize::MAX && !dropped_run.contains(&o) && (run_len[&o] > run_len[&r] || (run_len[&o] == run_len[&r] && o > r))));
            if faces_longer { dropped_run.insert(r); }
        }
        let mut dropped = 0;
        for i in 0..n {
            if partner[i].is_some() && dropped_run.contains(&root(&mut par2, i)) { keep[i] = false; dropped += 1; }
        }
        if dbg() { eprintln!("thin feature pairs: {} close edges in {} runs, dropped {dropped} of {n} marked edges (distance < {thr:.4})", partner.iter().flatten().count(), run_len.len()); }
    }
    let (mut feats, mut segs) = (Vec::new(), Vec::new());
    for (i, e) in soup.features.iter().enumerate() {
        if keep[i] { feats.push(*e); segs.push([soup.p[e[0] as usize], soup.p[e[1] as usize]]); }
    }
    (feats, segs)
}

struct Grid {
    result: PolyMesh,
    stats: ExtractStats,
    density_note: String,
}

fn run_inner(o: &Options, no_adaptive: bool) -> Result<Outcome, String> {
    let mut adapt_ratio = o.adapt_ratio;
    let mut adapt_min = o.adapt_min;
    let mut curv_angle = o.curv_angle;
    if let Some(v) = env_f64("FORGE_ADAPT_RATIO") { adapt_ratio = v; }
    if let Some(v) = env_f64("FORGE_ADAPT_MIN") { adapt_min = v; }
    if let Some(v) = env_f64("FORGE_CURV") { curv_angle = v; }
    let adaptive = o.adaptive && !no_adaptive;
    if o.target_faces < 1000 { curv_angle = 0.0; } // too few faces for creases to matter
    let requested = o.target_faces;

    say("P", "2 Reading");
    let input = fmesh::read(&o.input)?;
    say("P", "6 Checking the mesh");
    let mut rep = repair::RepairReport::default();
    let mut soup = repair::repair_mesh(&input, &mut rep);
    say("R", &rep.to_json(soup.f.len()));

    let quad_mode = o.mode == "quad";
    let stitched = stitch_slits(&mut soup);
    if stitched > 0 && dbg() { eprintln!("removed {stitched} flat sliver triangles"); }
    // The global lattice needs a surface without borders: a closed loop of fixed length cannot hold a whole number
    // of cells. Those meshes use the pairing route, unless the borders can be capped.
    let feature_share = if soup.f.is_empty() { 0.0 } else { soup.features.len() as f64 / (1.5 * soup.f.len() as f64) };
    let mut has_border = has_open_edges(&soup);
    let align_mode = o.use_param && o.align_feat && !env_set("FORGE_NOPARAM");
    let mut fallback_reason = String::new();
    let mut density_note = String::new();
    let mut cap_info = CapInfo::default();
    let mut orig_soup: Option<TriSoup> = None;
    let mut capped = false;
    let mut closed_holes = 0;
    let mut cap_area_frac = 0.0;
    let auto_or_extract = o.quad_method == "auto" || o.quad_method == "extract";
    if quad_mode && has_border && align_mode && auto_or_extract && !env_set("FORGE_NOCAP") {
        // open borders: close them with caps, align the grid to the border lines, drop the caps afterwards
        let rho0 = (soup_area(&soup) / (requested as f64 / 4.0).max(1.0)).sqrt();
        let mut trial = soup.clone();
        orig_soup = Some(soup.clone());
        if cap_borders(&mut trial, 6.0 * rho0, &mut cap_info) && cap_info.loops > 0 && cap_info.removable == 0 {
            // only small holes: close them (the result is a closed surface, the holes are filled)
            soup = trial;
            has_border = false;
            closed_holes = cap_info.loops;
            if dbg() { eprintln!("closed {} small holes", cap_info.loops); }
        } else if cap_info.loops > 0 && cap_info.removable > 0 {
            soup = trial;
            has_border = false;
            capped = true;
            let (mut a_all, mut a_cap) = (0.0, 0.0);
            for (i, t) in soup.f.iter().enumerate() {
                let a = 0.5 * (soup.p[t[1] as usize] - soup.p[t[0] as usize]).cross(soup.p[t[2] as usize] - soup.p[t[0] as usize]).length();
                a_all += a;
                if cap_info.removable_face[i] { a_cap += a; }
            }
            cap_area_frac = if a_all > 0.0 { a_cap / a_all } else { 0.0 };
            if dbg() { eprintln!("capped {} border loops ({} removable)", cap_info.loops, cap_info.removable); }
        }
    }
    let extract = quad_mode
        && (o.quad_method == "extract"
            || (o.quad_method == "auto" && (input.density.is_empty() || align_mode) && !has_border && (align_mode || feature_share < 0.01)));
    if quad_mode && !extract {
        fallback_reason = if o.quad_method == "pair" { "pairing method requested" } else if has_border { "open borders that could not be closed" }
            else if !align_mode { "grid alignment disabled" } else { "mesh not suited to the grid method" }.to_string();
    }
    // thin parts get smaller quads: the density (edge length multiplier) is lowered there
    let mut adapt_applied = false;
    if extract && adaptive && !env_set("FORGE_NOADAPT") {
        let sp = SizeFieldParams { rho: (soup_area(&soup) / requested.max(1) as f64).sqrt(), ratio: adapt_ratio, min_scale: adapt_min, threads: o.threads, ..SizeFieldParams::default() };
        let mut sfs = SizeFieldStats::default();
        let mut s = thickness_size_field(&soup, &sp, &mut sfs);
        if curv_angle > 0.0 {
            let mut cs = SizeFieldStats::default();
            let mut spc = sp.clone();
            spc.min_scale = env_f64("FORGE_CURV_MIN").unwrap_or(0.3);
            let sc = curvature_size_field(&soup, &spc, curv_angle, &mut cs);
            if cs.refined > 0 {
                if s.len() == sc.len() { for v in 0..s.len() { s[v] = s[v].min(sc[v]); } }
                sfs.refined = sfs.refined.max(cs.refined);
                sfs.min_seen = sfs.min_seen.min(cs.min_seen);
            }
            if let Some(gap) = env_f64("FORGE_GAP") {
                let mut spg = sp.clone();
                spg.ratio = gap;
                spg.min_scale = 0.3;
                let mut gs = SizeFieldStats::default();
                let sg = gap_size_field(&soup, &spg, &mut gs);
                if gs.refined > 0 && s.len() == sg.len() {
                    for v in 0..s.len() { s[v] = s[v].min(sg[v]); }
                    sfs.refined = sfs.refined.max(gs.refined);
                    sfs.min_seen = sfs.min_seen.min(gs.min_seen);
                }
            }
        }
        if sfs.refined > 0 {
            adapt_applied = true;
            if soup.density.is_empty() { soup.density = vec![1.0; soup.p.len()]; }
            for v in 0..s.len() { soup.density[v] *= s[v]; }
        }
        if dbg() { eprintln!("adaptive: refined {} of {} vertices, smallest scale {:.2}", sfs.refined, s.len(), sfs.min_seen); }
    }
    let mut free_soup = soup.clone();
    free_soup.features.clear();
    let mut feat_soup = free_soup.clone(); // only the long chains of marked edges
    let (chain_feats, chain_segs) = long_feature_chains(&soup, requested);
    feat_soup.features = chain_feats;
    let marked_lines = if chain_segs.is_empty() { SegmentIndex::default() } else { SegmentIndex::new(chain_segs) };

    let pair_target = (requested / 2).max(4); // every pair of triangles becomes 4 quads
    let extract_target = ((requested as f64 * o.ex_density / if o.ex_subdiv { 4.0 } else { 1.0 }) as i32).max(16);
    let first_src: &TriSoup = if extract { if align_mode { &feat_soup } else { &free_soup } } else { &soup };
    let mut rem = Remesher::new(first_src, remesh_params(o, if quad_mode { if extract { extract_target } else { pair_target } } else { requested }));
    let mut result = PolyMesh::default();
    let mut st = RemeshStats::default();
    if !rem.run(&mut result, &mut st) {
        return Err(if st.error.is_empty() { "remesh failed".into() } else { st.error.clone() });
    }
    let mut qs = QuadStats::default();
    let mut used_method = if quad_mode { if extract { "extract" } else { "pair" } } else { "tri" };
    let mut rem2: Option<Remesher> = None;
    let pair_src: &TriSoup = if capped { orig_soup.as_ref().unwrap() } else { &soup };
    let mut st2 = RemeshStats::default();

    if extract {
        let grid = grid_route(o, &rem, &soup, &free_soup, &feat_soup, &marked_lines, &input, requested, capped, &cap_info, cap_area_frac,
                              curv_angle, align_mode, &mut fallback_reason)?;
        match grid {
            Some(g) => {
                result = g.result;
                density_note = g.density_note;
                let es = &g.stats;
                qs.field_seconds = es.field_seconds;
                qs.match_seconds = es.position_seconds;
                qs.relax_seconds = es.graph_seconds;
                qs.irregular_before = es.tris;
                qs.irregular_after = es.other;
                qs.pairs = es.cells;
                qs.leftover = es.dropped_loops;
                if dbg() {
                    eprintln!("extract: cells {} edges {} quads {} tris {} other {} dropped {} filled {} resid {:.3}",
                        es.cells, es.edges, es.quads, es.tris, es.other, es.dropped_loops, es.filled, es.residual);
                }
            }
            None => {
                // the thin-part refinement is the usual reason the grid fails: try again without it before giving up
                if adapt_applied && !no_adaptive { return Ok(Outcome::Retry); }
                used_method = "pair";
                if fallback_reason.is_empty() { fallback_reason = "no usable grid result (open edges or count off after 3 passes)".into(); }
                let mut r2 = Remesher::new(pair_src, remesh_params(o, pair_target));
                if !r2.run(&mut result, &mut st2) {
                    return Err(if st2.error.is_empty() { "remesh failed".into() } else { st2.error.clone() });
                }
                rem2 = Some(r2);
                pair_route(o, rem2.as_ref().unwrap(), &mut result, &mut qs)?;
            }
        }
    } else if quad_mode {
        pair_route(o, &rem, &mut result, &mut qs)?;
    }
    fmesh::write(&o.output, &result)?;
    if o.stats {
        let (reference, stats_src) = match &rem2 { Some(r) => (&r.reference, &st2), None => (&rem.reference, &st) };
        let mut js = metrics_json(&result, Some(reference));
        js.pop();
        js.push_str(&format!(
            ",\"method\":\"{}\",\"closed_holes\":{},\"removed_slivers\":{},\"density_note\":\"{}\",\"fallback\":\"{}\",\"seconds\":{:.6},\
\"edge_length\":{:.6},\"rounds\":{},\"pairs\":{},\"leftover_tris\":{},\"field_s\":{:.6},\"match_s\":{:.6},\"relax_s\":{:.6},\
\"irregular_before\":{},\"irregular_after\":{}}}",
            used_method, closed_holes, stitched, density_note, json_safe(&fallback_reason), stats_src.seconds, stats_src.edge_length,
            stats_src.rounds, qs.pairs, qs.leftover, qs.field_seconds, qs.match_seconds, qs.relax_seconds, qs.irregular_before, qs.irregular_after
        ));
        say("S", &js);
    }
    Ok(Outcome::Done)
}

fn pair_route(o: &Options, rem: &Remesher, result: &mut PolyMesh, qs: &mut QuadStats) -> Result<(), String> {
    let qp = QuadParams {
        threads: o.threads, project: o.project, progress: Some(progress_box()), relax_iterations: o.relax_iters, field_align: o.field_align,
        use_field: o.use_field, pole_weight: o.pole_weight, pole_sweeps: o.pole_sweeps, cycle_pairs: o.cycle_pairs, ..QuadParams::default()
    };
    let mut quad = Quadifier::new(&rem.mesh, &rem.reference, &rem.feature_lines, rem.base_length, qp);
    let mut quads = PolyMesh::default();
    quad.run(&mut quads, qs)?;
    *result = quads;
    Ok(())
}

/// The grid route. Ok(None): no usable result (the caller falls back); `fallback_reason` says why when known.
#[allow(clippy::too_many_arguments)]
fn grid_route(o: &Options, rem: &Remesher, soup: &TriSoup, free_soup: &TriSoup, feat_soup: &TriSoup, marked_lines: &SegmentIndex,
              input: &PolyMesh, requested: i32, capped: bool, cap_info: &CapInfo, cap_area_frac: f64, curv_angle: f64,
              align_mode: bool, fallback_reason: &mut String) -> Result<Option<Grid>, String> {
    let m = &rem.mesh;
    let mut area = 0.0;
    for f in 0..m.num_face_slots() {
        if !m.falive[f as usize] { continue; }
        let mut sc = 1.0;
        if !m.vdens.is_empty() {
            sc = (m.vdens[m.v[3 * f as usize] as usize] as f64 + m.vdens[m.v[3 * f as usize + 1] as usize] as f64 + m.vdens[m.v[3 * f as usize + 2] as usize] as f64) / 3.0;
        }
        area += 0.5 * m.face_normal_unnormalized(f).length() / (sc * sc);
    }
    let divisor = if o.ex_subdiv { requested as f64 / 4.0 } else { requested as f64 };
    let use_param = o.use_param && !env_set("FORGE_NOPARAM");
    let guide_idx = if input.guides.is_empty() { SegmentIndex::default() } else { SegmentIndex::new(input.guides.clone()) };
    let mut ep = ExtractParams {
        // the first pass tends to give ~3% too few quads
        rho: 0.98 * (area * (1.0 - cap_area_frac) / divisor).sqrt(),
        subdivide: o.ex_subdiv, iterations: o.ex_iters, threads: o.threads, param_time_limit: o.time_limit, use_param,
        align_features: use_param && align_mode && !feat_soup.features.is_empty(),
        progress: Some(Arc::new(|p, m| say("P", &format!("{p} {m}")))), ..ExtractParams::default()
    };
    if !input.guides.is_empty() {
        ep.guides = Some(&guide_idx);
        ep.guide_strength = env_f64("FORGE_GUIDE").unwrap_or(8.0);
        ep.guide_radius = env_f64("FORGE_GUIDER").unwrap_or(4.0);
    }
    if let Some(v) = env_f64("FORGE_POLEMOVES") { ep.pole_moves = v; }
    if curv_angle > 0.0 {
        ep.curv_align = env_f64("FORGE_CALIGN").unwrap_or(4.0);
        ep.curv_gate = env_f64("FORGE_CGATE").unwrap_or(0.2);
    }
    // the field is solved on a coarser mesh of the same surface when the working mesh is large
    let mut coarse: Option<Remesher> = None;
    if m.alive_faces > 12000 {
        let mut cp = remesh_params(o, (m.alive_faces / 4).max(2000));
        cp.progress = None;
        let mut c = Remesher::new(if ep.align_features { feat_soup } else { free_soup }, cp);
        let mut tmp = PolyMesh::default();
        let mut cst = RemeshStats::default();
        if c.run(&mut tmp, &mut cst) { coarse = Some(c); }
    }
    let mut ex = Extractor::new(m, &rem.reference, marked_lines, ep);
    if let Some(c) = &coarse { ex.set_field_mesh(&c.mesh); }
    let cap_idx = if capped { SurfaceIndex::new(&soup.p, &soup.f) } else { SurfaceIndex::default() };

    let mut best = PolyMesh::default();
    let mut best_stats = ExtractStats::default();
    let mut best_err = 1e300f64;
    let mut any = false;
    // (cell size, quad count) of the passes that closed; the count jumps with the grid, so after three passes a
    // result that is still off by more than 6% gets up to two more tries bracketed by the best neighbours.
    let mut tried: Vec<(f64, f64)> = Vec::new();
    let tol = if requested >= 30000 { 0.06 } else { 0.04 };
    let mut pass = 0;
    while pass < 5 {
        if pass >= 3 && (!any || best_err <= 0.06) { break; }
        pass += 1;
        let pass = pass - 1;
        let mut es = ExtractStats::default();
        let mut quads = PolyMesh::default();
        if let Err(e) = ex.run(&mut quads, &mut es) {
            *fallback_reason = format!("grid solve failed: {}", if e.is_empty() { "no detail" } else { &e });
            break;
        }
        if capped {
            quads = drop_cap_quads(&quads, marked_lines, &cap_idx, cap_info, ex.prm.rho);
            quads = weld_short_edges(quads, marked_lines, ex.prm.rho);
        }
        // the input is closed, so every open edge in the result is a defect: do not accept it
        let open = open_edge_count(&quads);
        if open > 0 && !capped {
            if dbg() { eprintln!("extract: result has {open} open edges, rejected"); }
            let r = ex.prm.rho * 0.97; // a slightly different grid often closes
            ex.set_rho(r);
            continue;
        }
        let ratio = quads.sizes.len() as f64 / requested as f64;
        let count = quads.sizes.len() as f64;
        if dbg() { eprintln!("pass {pass}: {} quads for {requested} requested (ratio {ratio:.3})", quads.sizes.len()); }
        let err = (ratio - 1.0).abs();
        if err < best_err { best_err = err; best = quads; best_stats = es; any = true; }
        if err < tol { break; }
        tried.push((ex.prm.rho, count));
        let r = next_rho(ex.prm.rho, ratio, &tried, requested as f64);
        ex.set_rho(r);
    }
    if any && !capped && best_err > 0.25 && !fallback_reason.is_empty() {
        // a later pass failed outright and the one that closed is far from the count: do not hand that result over
        // (the caller retries without the thickness refinement, or uses the pairing path)
        if dbg() { eprintln!("grid failed and the closed pass is off by {:.0}%, discarding it", 100.0 * best_err); }
        any = false;
    }
    if any && capped && best_err > 0.25 {
        // the capped surface did not give a usable result at this size: use the pairing path instead
        if dbg() { eprintln!("capped extraction off by {:.0}%, falling back", 100.0 * best_err); }
        any = false;
        *fallback_reason = "open-border result too far from the requested count".into();
    }
    let mut density_note = String::new();
    if any && !soup.density.is_empty() {
        // does the grid follow the requested size variation? (a smooth shape with few singularities cannot always
        // do it, and then the pairing path is the honest answer)
        let dsi = SurfaceIndex::new(&soup.p, &soup.f);
        let mut se: Vec<(f64, f64)> = Vec::new(); // (wanted size, achieved edge)
        let mut at = 0usize;
        for &sz in &best.sizes {
            let sz = sz as usize;
            let (mut c, mut el) = (Vec3::ZERO, 0.0);
            for k in 0..sz {
                let a = best.p[best.idx[at + k] as usize];
                let b = best.p[best.idx[at + (k + 1) % sz] as usize];
                c += a;
                el += a.distance(b);
            }
            c = c / sz as f64;
            el /= sz as f64;
            at += sz;
            let h = dsi.closest_any(c);
            if h.tri < 0 { continue; }
            let t = soup.f[h.tri as usize];
            let s = (1.0 - h.u - h.v) * soup.density[t[0] as usize] as f64 + h.u * soup.density[t[1] as usize] as f64 + h.v * soup.density[t[2] as usize] as f64;
            se.push((s, el));
        }
        if se.len() > 100 {
            se.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let n30 = se.len() * 3 / 10;
            let med = |b0: usize, e0: usize, second: bool| {
                let mut v: Vec<f64> = se[b0..e0].iter().map(|x| if second { x.1 } else { x.0 }).collect();
                v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
                v[v.len() / 2]
            };
            let wanted = med(se.len() - n30, se.len(), false) / med(0, n30, false);
            let got = med(se.len() - n30, se.len(), true) / med(0, n30, true);
            if dbg() { eprintln!("density follow: wanted ratio {wanted:.2} got {got:.2}"); }
            if wanted > 1.05 && got < 1.0 + 0.5 * (wanted - 1.0) { density_note = "partial".into(); }
        }
    }
    if any { Ok(Some(Grid { result: best, stats: best_stats, density_note })) } else { Ok(None) }
}

/// Next cell size: the plain area rule, or an interpolation between the closest passes on each side of the target.
fn next_rho(rho: f64, ratio: f64, tried: &[(f64, f64)], target: f64) -> f64 {
    let below = tried.iter().filter(|t| t.1 < target).max_by(|a, b| a.1.partial_cmp(&b.1).unwrap()); // closest count under
    let above = tried.iter().filter(|t| t.1 > target).min_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
    if let (Some(lo), Some(hi)) = (below, above) {
        // lo has the larger cell size, hi the smaller; the count is about proportional to 1/rho^2
        let t = ((target - lo.1) / (hi.1 - lo.1).max(1e-9)).clamp(0.15, 0.85);
        let r = lo.0 + (hi.0 - lo.0) * t;
        if r.is_finite() && r > 0.0 { return r; }
    }
    rho * ratio.sqrt()
}
