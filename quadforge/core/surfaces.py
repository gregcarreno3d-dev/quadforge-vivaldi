"""Whole-model surface analysis (CAD-style meshes): runs the engine's ``surfaces`` command and reads its report.

Every face of the mesh gets a surface id and a surface kind (plane, cylinder, sphere, cone, revolution, freeform,
chamfer, bevel). The borders between surfaces are listed with their kind (sharp, tangent, band).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import forge_io, geometry as geo
from .backends import forge as backend

# integer code stored in the face attribute ``qf_surface_kind``
KIND_CODES = {"none": 0, "plane": 1, "cylinder": 2, "sphere": 3, "cone": 4, "revolution": 5, "freeform": 6,
              "chamfer": 7, "bevel": 8}
KIND_NAMES = {code: name for name, code in KIND_CODES.items()}
BORDER_CODES = {"none": 0, "sharp": 1, "tangent": 2, "band": 3}

# viewport colours (RGBA, linear) by kind
KIND_COLORS = {
    0: (0.05, 0.05, 0.05, 1.0),
    1: (0.62, 0.66, 0.74, 1.0),     # plane
    2: (0.28, 0.72, 0.36, 1.0),     # cylinder
    3: (0.58, 0.32, 0.85, 1.0),     # sphere
    4: (0.90, 0.80, 0.16, 1.0),     # cone
    5: (0.14, 0.70, 0.70, 1.0),     # revolution
    6: (0.90, 0.30, 0.62, 1.0),     # freeform
    7: (1.00, 0.42, 0.05, 1.0),     # chamfer
    8: (0.10, 0.45, 1.00, 1.0),     # bevel
}


class SurfaceError(RuntimeError):
    pass


def analyze_arrays(co: np.ndarray, sizes: np.ndarray, idx: np.ndarray, max_width_units: float = 0.0, sharp: float = 25.0):
    """Run the engine on raw arrays. Returns ``(face_surface ndarray, report dict)``."""
    engine = backend.find_engine()
    with tempfile.TemporaryDirectory(prefix="quadforge_surf_") as tmp:
        src, out = Path(tmp) / "in.fmesh", Path(tmp) / "surfaces.json"
        forge_io.write_fmesh(src, co, sizes, idx)
        cmd = [str(engine), "surfaces", "--in", str(src), "--json", str(out), "--sharp", f"{sharp:g}"]
        if max_width_units > 0:
            cmd += ["--max-width", f"{max_width_units:.6g}"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
        done = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
        if done.returncode != 0 or not out.is_file():
            raise SurfaceError((done.stdout + done.stderr).strip() or "the engine did not answer (older engine without surface analysis?)")
        report = json.loads(out.read_text())
    return np.array(report["face_surface"], dtype=np.int32), report


def analyze(obj, max_width_units: float = 0.0, sharp: float = 25.0):
    """Analyse a mesh object (object scale applied). Returns ``(face_surface, report)``."""
    from . import mesh_io
    arrays = mesh_io.read_mesh_arrays(obj.data)
    scale = np.array(obj.matrix_world.to_scale(), dtype=np.float32)
    sizes = np.diff(arrays.poly_offsets).astype(np.int32)
    return analyze_arrays(arrays.co * scale, sizes, arrays.loop_verts.astype(np.int32), max_width_units, sharp)


def face_kind_codes(face_surface: np.ndarray, surfaces: list) -> np.ndarray:
    """Kind code of every face (0 where no surface)."""
    codes = np.array([0] + [KIND_CODES.get(s["kind"], 6) for s in surfaces], dtype=np.int32)
    return codes[face_surface + 1]


def border_edge_codes(arrays, face_surface: np.ndarray, borders: list) -> np.ndarray:
    """Per edge: 0 = inside a surface, 1 sharp, 2 tangent, 3 band border."""
    first, second, counts = geo.edge_face_pairs(arrays)
    codes = np.zeros(arrays.n_edges, dtype=np.int32)
    kind_of = {(b["a"], b["b"]): BORDER_CODES.get(b["kind"], 1) for b in borders}
    two = np.flatnonzero(counts == 2)
    if not two.size:
        return codes
    sa, sb = face_surface[first[two]], face_surface[second[two]]
    diff = (sa != sb) & (sa >= 0) & (sb >= 0)
    for e, a, b in zip(two[diff], sa[diff], sb[diff]):
        key = (int(a), int(b)) if a < b else (int(b), int(a))
        codes[e] = kind_of.get(key, 1)
    return codes
