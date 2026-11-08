//! Command line: argument parsing and the orchestration of the stages.
//!
//!   forge remesh --in a.fmesh --out b.fmesh --faces 20000 [--mode tri|quad] [--threads N] ...
//!
//! Work areas: `Options::parse` (flags) · `run_remesh` (the pipeline order) · stdout protocol in `util::protocol`.
use crate::io::fmesh;
use crate::mesh::bvh::SegmentIndex;
use crate::stages::metrics::metrics_json;
use crate::stages::quad::{QuadParams, QuadStats, Quadifier};
use crate::stages::remesh::{RemeshParams, RemeshStats, Remesher};
use crate::stages::{capping, repair};
use crate::util::parallel::default_threads;
use crate::util::protocol::{json_safe, say};

pub const USAGE: &str = "usage: forge remesh --in FILE --out FILE --faces N [--mode tri|quad] [--threads N]\n\
              [--no-project] [--no-retry] [--corner-angle DEG] [--iterations N] [--stats] [--check]";

pub struct Options {
    pub input: String,
    pub output: String,
    pub mode: String,
    pub target_faces: i32,
    pub threads: usize,
    pub project: bool,
    pub corner_angle_deg: f64,
    pub iterations: i32,
    pub stats: bool,
    pub check: bool,
    pub relax_iters: i32,
    pub field_align: f64,
    pub use_field: bool,
    pub pole_weight: f64,
    pub pole_sweeps: i32,
    pub cycle_pairs: i32,
    pub quad_method: String,
}

impl Options {
    pub fn parse(args: &[String]) -> Result<Options, String> {
        let mut o = Options {
            input: String::new(), output: String::new(), mode: "tri".into(), target_faces: 0, threads: default_threads(),
            project: true, corner_angle_deg: 35.0, iterations: 8, stats: false, check: false, relax_iters: 30,
            field_align: 0.5, use_field: true, pole_weight: 10.0, pole_sweeps: 150, cycle_pairs: 5, quad_method: "auto".into(),
        };
        let mut i = 2;
        while i < args.len() {
            let a = args[i].as_str();
            let mut next = || -> String { i += 1; args.get(i).cloned().unwrap_or_default() };
            match a {
                "--in" => o.input = next(),
                "--out" => o.output = next(),
                "--faces" => o.target_faces = next().parse().unwrap_or(0),
                "--mode" => o.mode = next(),
                "--threads" => o.threads = next().parse::<usize>().unwrap_or(1).max(1),
                "--no-project" => o.project = false,
                "--corner-angle" => o.corner_angle_deg = next().parse().unwrap_or(35.0),
                "--iterations" => o.iterations = next().parse::<i32>().unwrap_or(1).max(1),
                "--stats" => o.stats = true,
                "--check" => o.check = true,
                "--relax" => o.relax_iters = next().parse().unwrap_or(30),
                "--field-align" => o.field_align = next().parse().unwrap_or(0.5),
                "--no-field" => o.use_field = false,
                "--pole-weight" => o.pole_weight = next().parse().unwrap_or(10.0),
                "--pole-sweeps" => o.pole_sweeps = next().parse().unwrap_or(150),
                "--cycle-pairs" => o.cycle_pairs = next().parse().unwrap_or(5),
                "--quad-method" => o.quad_method = next(),
                // accepted for compatibility with the C++ engine; the grid route is not ported yet
                "--no-retry" | "--retry" | "--no-param" | "--no-align" | "--no-adaptive" => {}
                "--ex-subdiv" | "--ex-iters" | "--ex-density" | "--time-limit" | "--adaptive-ratio" | "--adaptive-min" | "--curv-adapt" => { next(); }
                _ => return Err(USAGE.into()),
            }
            i += 1;
        }
        if o.input.is_empty() || o.output.is_empty() || o.target_faces < 4 { return Err(USAGE.into()); }
        Ok(o)
    }
}

/// Returns the process exit code.
pub fn run_remesh(args: &[String]) -> i32 {
    let o = match Options::parse(args) {
        Ok(o) => o,
        Err(u) => { println!("{u}"); return 1; }
    };
    match remesh(&o) {
        Ok(()) => { say("OK", ""); 0 }
        Err(e) => { say("ERR", &e); 2 }
    }
}

fn remesh(o: &Options) -> Result<(), String> {
    let progress = || -> crate::stages::remesh::ProgressFn { Box::new(|p, m| say("P", &format!("{p} {m}"))) };
    say("P", "2 Reading");
    let input = fmesh::read(&o.input)?;
    say("P", "6 Checking the mesh");
    let mut rep = repair::RepairReport::default();
    let mut soup = repair::repair_mesh(&input, &mut rep);
    say("R", &rep.to_json(soup.f.len()));
    let quad_mode = o.mode == "quad";
    let stitched = capping::stitch_slits(&mut soup);
    if stitched > 0 && std::env::var_os("FORGE_DBG").is_some() { eprintln!("removed {stitched} flat sliver triangles"); }
    let requested = o.target_faces;
    // Grid route (integer-grid parametrization): not ported yet, so quad mode always uses the pairing route.
    let fallback_reason = if !quad_mode { String::new() } else { "grid method not available in this build".to_string() };
    let used_method = if quad_mode { "pair" } else { "tri" };
    let pair_target = (requested / 2).max(4); // every pair of triangles becomes 4 quads
    let prm = RemeshParams {
        target_faces: if quad_mode { pair_target } else { requested }, iterations: o.iterations, threads: o.threads,
        project: o.project, corner_angle_deg: o.corner_angle_deg, check: o.check, progress: Some(progress()), ..RemeshParams::default()
    };
    let mut rem = Remesher::new(&soup, prm);
    let mut result = fmesh::PolyMesh::default();
    let mut st = RemeshStats::default();
    if !rem.run(&mut result, &mut st) {
        return Err(if st.error.is_empty() { "remesh failed".into() } else { st.error.clone() });
    }
    let mut qs = QuadStats::default();
    if quad_mode {
        let qp = QuadParams {
            threads: o.threads, project: o.project, progress: Some(progress()), relax_iterations: o.relax_iters,
            field_align: o.field_align, use_field: o.use_field, pole_weight: o.pole_weight, pole_sweeps: o.pole_sweeps,
            cycle_pairs: o.cycle_pairs, ..QuadParams::default()
        };
        let mut quad = Quadifier::new(&rem.mesh, &rem.reference, &rem.feature_lines, rem.base_length, qp);
        let mut quads = fmesh::PolyMesh::default();
        quad.run(&mut quads, &mut qs)?;
        result = quads;
    }
    fmesh::write(&o.output, &result)?;
    if o.stats {
        let mut js = metrics_json(&result, Some(&rem.reference));
        js.pop(); // closing brace
        js.push_str(&format!(
            ",\"method\":\"{}\",\"closed_holes\":0,\"removed_slivers\":{},\"density_note\":\"\",\"fallback\":\"{}\",\"seconds\":{:.6},\
\"edge_length\":{:.6},\"rounds\":{},\"pairs\":{},\"leftover_tris\":{},\"field_s\":{:.6},\"match_s\":{:.6},\"relax_s\":{:.6},\
\"irregular_before\":{},\"irregular_after\":{}}}",
            used_method, stitched, json_safe(&fallback_reason), st.seconds, st.edge_length, st.rounds, qs.pairs, qs.leftover,
            qs.field_seconds, qs.match_seconds, qs.relax_seconds, qs.irregular_before, qs.irregular_after
        ));
        say("S", &js);
    }
    let _ = SegmentIndex::default;
    Ok(())
}
