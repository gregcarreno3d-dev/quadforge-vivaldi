//! Command line: argument parsing and the orchestration of the stages.
//!
//!   forge remesh --in a.fmesh --out b.fmesh --faces 20000 [--mode tri|quad] [--threads N] ...
//!
//! Work areas: `Options::parse` (flags) · `run_remesh` (the pipeline order) · stdout protocol in `util::protocol`.
use crate::pipeline;
use crate::util::parallel::default_threads;
use crate::util::protocol::say;

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
    pub no_retry: bool,
    pub with_retry: bool,
    pub use_param: bool,
    pub align_feat: bool,
    pub adaptive: bool,
    pub time_limit: f64,
    pub adapt_ratio: f64,
    pub adapt_min: f64,
    pub curv_angle: f64,
    pub ex_subdiv: bool,
    pub ex_iters: i32,
    pub ex_density: f64,
}

impl Options {
    pub fn parse(args: &[String]) -> Result<Options, String> {
        let mut o = Options {
            input: String::new(), output: String::new(), mode: "tri".into(), target_faces: 0, threads: default_threads(),
            project: true, corner_angle_deg: 35.0, iterations: 8, stats: false, check: false, relax_iters: 30,
            field_align: 0.5, use_field: true, pole_weight: 10.0, pole_sweeps: 150, cycle_pairs: 5, quad_method: "auto".into(),
            no_retry: false, with_retry: false, use_param: true, align_feat: true, adaptive: true, time_limit: 900.0,
            adapt_ratio: 1.3, adapt_min: 0.5, curv_angle: 0.0, ex_subdiv: true, ex_iters: 60, ex_density: 6.0,
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
                "--no-retry" => o.no_retry = true,
                "--retry" => o.with_retry = true,
                "--no-param" => o.use_param = false,
                "--no-align" => o.align_feat = false,
                "--no-adaptive" => o.adaptive = false,
                "--ex-subdiv" => o.ex_subdiv = next().parse::<i32>().unwrap_or(1) != 0,
                "--ex-iters" => o.ex_iters = next().parse().unwrap_or(60),
                "--ex-density" => o.ex_density = next().parse().unwrap_or(6.0),
                "--time-limit" => o.time_limit = next().parse().unwrap_or(900.0),
                "--adaptive-ratio" => o.adapt_ratio = next().parse().unwrap_or(1.3),
                "--adaptive-min" => o.adapt_min = next().parse().unwrap_or(0.5),
                "--curv-adapt" => o.curv_angle = next().parse().unwrap_or(0.0),
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
    // The grid route may ask for a second run without the thin-part refinement (exit code 3 in the C++ engine).
    let mut rc = pipeline::run(&o, false);
    if rc == pipeline::Outcome::Retry {
        if std::env::var_os("FORGE_DBG").is_some() { eprintln!("grid method failed with thickness refinement: running again without it"); }
        rc = pipeline::run(&o, true);
    }
    match rc {
        pipeline::Outcome::Done => { say("OK", ""); 0 }
        pipeline::Outcome::Failed(e) => { say("ERR", &e); 2 }
        pipeline::Outcome::Retry => { say("ERR", "grid retry failed"); 2 }
    }
}
