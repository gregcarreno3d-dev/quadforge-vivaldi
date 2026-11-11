// Several helpers are kept for the stages still to come (and for tools); do not warn about them.
#![allow(dead_code)]

//! FORGE command line (Rust port, work in progress).
mod cli;
mod io;
mod math;
mod mesh;
mod pipeline;
mod stages;
mod util;

use util::protocol::say;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    // Development helper: read, repair, build the corner table, check it, write the triangles back.
    if args.len() >= 4 && args[1] == "repair" {
        let run = || -> Result<(), String> {
            let input = io::fmesh::read(&args[2])?;
            let mut rep = stages::repair::RepairReport::default();
            let soup = stages::repair::repair_mesh(&input, &mut rep);
            say("R", &rep.to_json(soup.f.len()));
            let cm = mesh::cmesh::CMesh::build(&soup, 35.0)?;
            let err = cm.check();
            if !err.is_empty() { return Err(err); }
            io::fmesh::write(&args[3], &cm.export_triangles())
        };
        match run() {
            Ok(()) => say("OK", ""),
            Err(e) => { say("ERR", &e); std::process::exit(2); }
        }
        return;
    }
    // Chamfer / bevel detector: forge bands --in FILE [--json OUT] [--flat DEG] [--narrow R]
    if args.len() >= 2 && args[1] == "bands" {
        let mut prm = stages::bands::BandParams::default();
        let (mut input, mut json) = (String::new(), String::new());
        let mut i = 2;
        while i < args.len() {
            let v = args.get(i + 1).cloned().unwrap_or_default();
            match args[i].as_str() {
                "--in" => input = v,
                "--json" => json = v,
                "--sharp" => prm.sharp_deg = v.parse().unwrap_or(prm.sharp_deg),
                "--curved" => prm.curved_factor = v.parse().unwrap_or(prm.curved_factor),
                "--narrow" => prm.narrow_ratio = v.parse().unwrap_or(prm.narrow_ratio),
                "--max-width" => prm.max_width = v.parse().unwrap_or(0.0),
                "--link" => prm.link_deg = v.parse().unwrap_or(prm.link_deg),
                "--elong" => prm.min_elong = v.parse().unwrap_or(prm.min_elong),
                _ => { i += 1; continue; }
            }
            i += 2;
        }
        match io::fmesh::read(&input) {
            Ok(m) => {
                let rep = stages::bands::detect(&m, &prm);
                if !json.is_empty() { let _ = std::fs::write(&json, rep.to_json(true)); }
                say("B", &rep.to_json(false));
                say("OK", "");
            }
            Err(e) => { say("ERR", &e); std::process::exit(2); }
        }
        return;
    }
    if args.len() >= 2 && args[1] == "remesh" {
        std::process::exit(cli::run_remesh(&args));
    }
    println!("{}", cli::USAGE);
    std::process::exit(1);
}
