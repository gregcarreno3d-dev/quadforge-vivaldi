#!/usr/bin/env python3
"""Build the Forge engine for every platform using Zig as a cross compiler.

    python3 tools/build.py            # all targets into engine/bin/<platform>/
    python3 tools/build.py linux      # only one

Needs:  pip install ziglang   (no other toolchain; Zig bundles clang and libc++).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(__file__).resolve().parent / "main.cpp"
TARGETS = {
    "linux":       ("x86_64-linux-gnu",   "forge"),
    "windows":     ("x86_64-windows-gnu", "forge.exe"),
    "macos-arm64": ("aarch64-macos",      "forge"),
    "macos-x64":   ("x86_64-macos",       "forge"),
}


def build(name: str, outdir: Path) -> Path:
    triple, exe = TARGETS[name]
    dest = outdir / name
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / exe
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O2", "-DNDEBUG", "-s", "-target", triple,
           str(SRC), "-o", str(out)]
    if name == "linux" or name.startswith("macos"):
        cmd.append("-pthread")
    print(">>", name, flush=True)
    subprocess.run(cmd, check=True)
    for junk in dest.glob("*.pdb"):
        junk.unlink()
    return out


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(TARGETS)
    outdir = ROOT / "bin"
    for name in wanted:
        path = build(name, outdir)
        print("   ", path, path.stat().st_size // 1024, "KB")
