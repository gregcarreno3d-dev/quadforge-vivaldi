//! FORGE command line (Rust port, work in progress).
mod cli;
mod io;
mod math;
mod mesh;
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
    if args.len() >= 2 && args[1] == "remesh" {
        std::process::exit(cli::run_remesh(&args));
    }
    println!("{}", cli::USAGE);
    std::process::exit(1);
}
