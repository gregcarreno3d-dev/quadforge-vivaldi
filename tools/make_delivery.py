"""Builds dist/quadforge-vivaldi-<version>-delivery.zip: the add-on, the GitHub upload folders (in batches of
fewer than 100 files), the release files and a Spanish step-by-step guide. Run after tools/package.py."""
import hashlib
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
version = re.search(r'^version = "([^"]+)"', (ROOT / "quadforge/blender_manifest.toml").read_text(), re.M).group(1)
OUT = DIST / f"delivery_{version}"
SKIP_DIRS = {"__pycache__", "out", "dev"}
SKIP_SUFFIX = {".pyc", ".zip"}


def copy_tree(src: Path, dst: Path) -> int:
    count = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if path.is_dir() or any(part in SKIP_DIRS for part in rel.parts) or path.suffix in SKIP_SUFFIX:
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


if OUT.exists():
    shutil.rmtree(OUT)
up = OUT / "GITHUB_UPLOAD"
counts = {}

for name in ("README.md", ".gitignore", "LICENSE"):
    copy_file(ROOT / name, up / "1_archivos_de_la_raiz" / name)
counts["1"] = 3
counts["2"] = copy_tree(ROOT / "engine/src", up / "2_carpeta_engine" / "engine" / "src")
for extra in ("engine/CMakeLists.txt", "engine/README.md"):
    if (ROOT / extra).is_file():
        copy_file(ROOT / extra, up / "2_carpeta_engine" / extra); counts["2"] += 1
counts["3"] = copy_tree(ROOT / "tests", up / "3_tests_y_tools" / "tests") + copy_tree(ROOT / "tools", up / "3_tests_y_tools" / "tools")

quad = ROOT / "quadforge"
part1 = up / "4a_addon_parte1" / "quadforge"
part2 = up / "4b_addon_parte2" / "quadforge"
n1 = n2 = 0
for path in sorted(quad.iterdir()):
    first = path.name
    target_root = part2 if first in {"bin", "devtools", "engine_src", "tests"} else part1
    if path.is_dir():
        c = copy_tree(path, target_root / first)
    elif path.suffix not in SKIP_SUFFIX:
        copy_file(path, target_root / first); c = 1
    else:
        c = 0
    if target_root is part1: n1 += c
    else: n2 += c
counts["4a"], counts["4b"] = n1, n2

copy_file(ROOT / ".github/workflows/ci.yml", up / "5_CI_opcional" / "ci.yml")

rel = up / "6_RELEASE"
zips = sorted(DIST.glob(f"quadforge-vivaldi-{version}-*-*.zip"))
platform_zips = [z for z in zips if "all-platforms" not in z.name]
for z in platform_zips:
    copy_file(z, rel / z.name)
log = (quad / "CHANGELOG.md").read_text()
sections = re.split(r"^## ", log, flags=re.M)
latest = [s for s in sections if s.startswith(version)]
notes = f"""QuadForge Vivaldi {version}

FORGE is QuadForge's own C++ remeshing engine: quads or triangles, 2K to 500K faces, with one-click Hard Surface / Soft Surface (Organic) setups.

- Keeps hard edges, symmetry, UV maps, Face Sets and materials
- Isolate zones by material, Face Set, mask or vertex group
- Paintable density through a vertex group
- English and Spanish interface
- Blender 4.2 or newer (developed on Blender 5.2)

Install: Edit > Preferences > Get Extensions > Install from Disk, and choose the zip for your system:
quadforge-vivaldi-{version}-windows-x64.zip, -linux-x64.zip, -macos-arm64.zip (Apple Silicon), -macos-x64.zip (Intel Mac).

Status: validated on Linux. The Windows and macOS engines are built but have had little testing; please open an issue if something fails.
"""
(rel / "RELEASE_NOTES.txt").write_text(notes)
(rel / "TAG_Y_TITULO.txt").write_text(f"Tag:   v{version}\nTitulo: QuadForge Vivaldi {version}\n")
sums = "\n".join(f"{hashlib.sha256(z.read_bytes()).hexdigest()}  {z.name}" for z in platform_zips)
(rel / "SHA256SUMS.txt").write_text(sums + "\n")

addon = OUT / "ADDON_PARA_BLENDER"
for z in zips:
    if "all-platforms" in z.name:
        copy_file(z, addon / z.name)

guide = (ROOT / "tools/delivery_guide_es.md").read_text().replace("{VERSION}", version)
for key, value in counts.items():
    guide = guide.replace("{N" + key + "}", str(value))
(OUT / "LEEME_PRIMERO.md").write_text(guide)

target = DIST / f"quadforge-vivaldi-{version}-delivery.zip"
if target.exists():
    target.unlink()
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(OUT.rglob("*")):
        if path.is_file():
            zf.write(path, path.relative_to(OUT).as_posix())
print(target, counts)
