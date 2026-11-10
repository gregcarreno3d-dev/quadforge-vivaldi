#!/usr/bin/env python3
"""Build the distributable zips of the add-on.

    python tools/package.py            # all-platforms zip + one zip per platform, into dist/

* ``*-all-platforms.zip``: every engine inside; use it for "Install from Disk" on any computer.
* ``*-<platform>.zip``: only that platform's engine and a manifest whose ``platforms`` lists it; this is the
  shape the Blender Extensions platform expects when an extension ships executables.
Executable bits are kept in the zip. __pycache__ is never included.
"""
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "quadforge"
DIST = ROOT / "dist"
PLATFORMS = {            # Blender platform id -> engine folder
    "windows-x64": "windows",
    "linux-x64": "linux",
    "macos-arm64": "macos-arm64",
    "macos-x64": "macos-x64",
}


def manifest_for(platform_ids):
    text = (ADDON / "blender_manifest.toml").read_text(encoding="utf-8")
    listing = ", ".join(f'"{p}"' for p in platform_ids)
    return re.sub(r"^platforms\s*=.*$", f"platforms = [{listing}]", text, flags=re.M)


def version():
    return re.search(r'^version\s*=\s*"([^"]+)"', (ADDON / "blender_manifest.toml").read_text(), re.M).group(1)


def build(name, platform_ids):
    keep = {PLATFORMS[p] for p in platform_ids}
    out = DIST / name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(ADDON.rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(ADDON)
            if rel.parts[0] == "bin" and rel.parts[1] not in keep:
                continue
            if rel.parts[0] == "tests" and rel.parts[1:2] == ("models",):
                continue
            info = zipfile.ZipInfo.from_file(f, rel.as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            data = manifest_for(platform_ids).encode() if rel.as_posix() == "blender_manifest.toml" else f.read_bytes()
            z.writestr(info, data)
        # the Rust engine's source travels with the add-on (GPL): sources only, no build output
        rs = ROOT / "engine-rs"
        for f in sorted(rs.rglob("*")):
            rel = f.relative_to(rs)
            if not f.is_file() or rel.parts[0] in ("target", "bin") or "__pycache__" in f.parts:
                continue
            info = zipfile.ZipInfo.from_file(f, ("engine_src_rs/" + rel.as_posix()))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, f.read_bytes())
    print(f"{out.name:55s} {out.stat().st_size // 1024:6d} KB")


if __name__ == "__main__":
    DIST.mkdir(exist_ok=True)
    for old in DIST.glob("*.zip"):
        old.unlink()
    v = version()
    build(f"quadforge-vivaldi-{v}-all-platforms.zip", list(PLATFORMS))
    for platform in PLATFORMS:
        build(f"quadforge-vivaldi-{v}-{platform}.zip", [platform])
