"""Chamfer / bevel detection: runs the engine's ``bands`` command and reads its report.

Also guesses the unit of CAD models (STL files carry no unit; they are almost always millimetres).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import forge_io, mesh_io
from .backends import forge as backend


class BandError(RuntimeError):
    pass


def guess_mm_per_unit(scene, size_max: float) -> tuple[float, str]:
    """(millimetres per Blender unit, how it was decided).

    Uses the scene unit settings when they are explicit; otherwise a CAD-style size heuristic:
    a part whose largest side is 2..2000 units with the scene still at 1 unit = 1 m is read as millimetres.
    """
    units = scene.unit_settings
    if units.system == "NONE":
        return 1.0, "scene has no unit system (1 unit = 1 mm assumed)"
    scale = float(units.scale_length)
    if abs(scale - 1.0) > 1e-9:
        return scale * 1000.0, f"scene unit scale {scale:g}"
    if 2.0 <= size_max <= 2000.0:
        return 1.0, "scene is at 1 unit = 1 m but the part is CAD-sized: read as millimetres"
    return 1000.0, "scene at 1 unit = 1 m"


def detect(obj, max_width_units: float = 0.0, sharp: float = 25.0):
    """Run the detector on a mesh object. Returns (face_band ndarray, bands list, report dict)."""
    mesh = obj.data
    arrays = mesh_io.read_mesh_arrays(mesh)
    scale = np.array(obj.matrix_world.to_scale(), dtype=np.float32)
    co = arrays.co * scale
    sizes = np.diff(arrays.poly_offsets).astype(np.int32)
    idx = arrays.loop_verts.astype(np.int32)
    engine = backend.find_engine()
    with tempfile.TemporaryDirectory(prefix="quadforge_bands_") as tmp:
        src, out = Path(tmp) / "in.fmesh", Path(tmp) / "bands.json"
        forge_io.write_fmesh(src, co, sizes, idx)
        cmd = [str(engine), "bands", "--in", str(src), "--json", str(out), "--sharp", f"{sharp:g}"]
        if max_width_units > 0:
            cmd += ["--max-width", f"{max_width_units:.6g}"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
        done = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
        if done.returncode != 0 or not out.is_file():
            raise BandError((done.stdout + done.stderr).strip() or "the engine did not answer")
        report = json.loads(out.read_text())
    return np.array(report["face_band"], dtype=np.int32), report["bands"], report
