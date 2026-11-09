#!/usr/bin/env python3
"""Build the Rust FORGE engine (engine-rs/) natively and place it where the tests expect it.

    python3 tools/build_rust.py                 # builds for this computer into engine-rs/bin/<platform>/

Needs only `cargo` (https://rustup.rs). The engine has no dependencies. Cross compiling is not done here: GitHub
Actions builds on each operating system's own runner (see .github/workflows/ci.yml, job "engine-rs").
"""
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "macos-x64"
    return "linux"


if __name__ == "__main__":
    subprocess.run(["cargo", "build", "--release", "--manifest-path", str(ROOT / "engine-rs" / "Cargo.toml")], check=True)
    exe = "forge.exe" if sys.platform.startswith("win") else "forge"
    built = ROOT / "engine-rs" / "target" / "release" / exe
    dest = ROOT / "engine-rs" / "bin" / platform_name()
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(built, dest / exe)
    (dest / exe).chmod(0o755)
    print(dest / exe, (dest / exe).stat().st_size // 1024, "KB")
