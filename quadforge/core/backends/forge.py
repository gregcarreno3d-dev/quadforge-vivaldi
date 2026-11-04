"""FORGE: QuadForge's own remeshing engine (C++, no dependencies, outputs quads or triangles).

The engine is a small command line program that runs as a separate process. Blender
never loads its code, so a bug in the engine cannot crash Blender, and a run can be
stopped by ending the process. This file never imports ``bpy`` at module level: the
mesh object passed in is only touched through the plain Blender mesh API.
"""
from __future__ import annotations

import dataclasses
import json
import os
import platform
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from .. import forge_io
from .. import mesh_io as mio
from .base import Backend, BackendError, RemeshRequest

DENSITY_ATTRIBUTE = "quadforge_density"      # float per vertex: edge length multiplier (1 = normal, <1 = finer)
ENGINE_TIMEOUT_SECONDS = 3 * 60 * 60         # a safety net against a hung process, not a normal limit
_ADDON_ROOT = Path(__file__).resolve().parents[2]


def platform_folder() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos-arm64" if machine in ("arm64", "aarch64") else "macos-x64"
    return "linux"


def find_engine(root: Path | None = None) -> Path:
    """Path of the engine executable for this computer, made executable if needed."""
    root = root or _ADDON_ROOT
    name = "forge.exe" if sys.platform.startswith("win") else "forge"
    folder = platform_folder()
    path = root / "bin" / folder / name
    if not path.is_file():
        raise BackendError(
            f"The FORGE engine for this system ('{folder}') is not inside the add-on. "
            f"Expected: {path}. Choose the QuadriFlow engine instead.")
    if not sys.platform.startswith("win") and not os.access(path, os.X_OK):
        # Blender's zip installer does not keep the executable bit.
        try:
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            copy = Path(tempfile.mkdtemp(prefix="quadforge_bin_")) / name
            shutil.copyfile(path, copy)
            copy.chmod(0o755)
            path = copy
    if sys.platform == "darwin":
        # A zip downloaded in a browser may carry the quarantine flag; clearing it lets the engine start.
        try:
            subprocess.run(["xattr", "-d", "com.apple.quarantine", str(path)],
                           capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    return path


CURVATURE_RETRY_FRACTION = 0.009     # irregular vertices above this share of all vertices trigger the second run


def _irregular_vertices(info: dict) -> int:
    """Vertices that are not regular (valence 4) according to the engine statistics."""
    try:
        return int(info.get("verts", 0)) - int(info.get("regular_vertices", 0))
    except (TypeError, ValueError):
        return 0


def build_command(engine: Path, src: Path, dst: Path, request: RemeshRequest) -> list:
    command = [str(engine), "remesh", "--in", str(src), "--out", str(dst),
               "--faces", str(int(request.target_faces)),
               "--mode", "quad" if request.output == "QUADS" else "tri", "--stats"]
    if request.fast_seams:
        command += ["--no-retry"]
    if request.curv_angle > 0 and request.output == "QUADS":
        command += ["--curv-adapt", f"{request.curv_angle:.3f}"]
    if request.threads > 0:
        command += ["--threads", str(int(request.threads))]
    return command


def parse_engine_line(line: str):
    """('P', (percent, text)) | ('ERR', text) | ('S', json text) | ('R', json text) | ('OK', '') | None."""
    tag, _, rest = line.strip().partition(" ")
    if tag == "P":
        number, _, text = rest.partition(" ")
        try:
            return "P", (int(number), text)
        except ValueError:
            return None
    if tag in ("ERR", "S", "R", "OK"):
        return tag, rest
    return None


def run_engine_steps(command: list, progress=None):
    """Generator: run the engine without blocking and yield while it works.

    Returns (stats_json, report_json) through ``StopIteration.value``. If the generator is closed early the
    process is killed. Raises BackendError when the engine fails.
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, creationflags=flags)
    except OSError as error:
        raise BackendError(f"The FORGE engine could not start: {error}") from error
    lines: "queue.Queue" = queue.Queue()
    stderr_chunks: list = []

    def pump_stdout():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    def pump_stderr():
        for line in process.stderr:
            stderr_chunks.append(line)
            if os.environ.get("FORGE_DBG_ECHO"): sys.stderr.write(line if isinstance(line,str) else line.decode(errors="replace"))

    for target in (pump_stdout, pump_stderr):
        threading.Thread(target=target, daemon=True).start()

    stats = report = error_text = None
    started = time.monotonic()
    finished = False
    try:
        while not finished:
            try:
                line = lines.get(timeout=0.02)
            except queue.Empty:
                line = ""
            if line is None:
                finished = True
            elif line:
                parsed = parse_engine_line(line)
                if parsed is not None:
                    tag, value = parsed
                    if tag == "P" and progress is not None:
                        progress(*value)
                    elif tag == "ERR":
                        error_text = value
                    elif tag == "S":
                        stats = value
                    elif tag == "R":
                        report = value
            if time.monotonic() - started > ENGINE_TIMEOUT_SECONDS:
                raise BackendError("The FORGE engine took too long and was stopped.")
            if not finished:
                yield
        process.wait(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    if process.returncode != 0 or error_text:
        detail = error_text or "".join(stderr_chunks).strip()[-400:] or f"exit code {process.returncode}"
        if process.returncode is not None and process.returncode < 0:
            detail = f"the process crashed (signal {-process.returncode})"
        raise BackendError(f"FORGE engine failed: {detail}")
    return stats, report


def run_engine(command: list, progress=None) -> tuple:
    """Blocking version of ``run_engine_steps``."""
    steps = run_engine_steps(command, progress)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return done.value


def write_result_into_mesh(mesh, co: np.ndarray, sizes: np.ndarray, idx: np.ndarray) -> None:
    """Replace the geometry of ``mesh`` with the engine result using bulk array calls."""
    nv, npoly, nl = len(co), len(sizes), len(idx)
    starts = np.zeros(npoly, dtype=np.int32)
    if npoly:
        np.cumsum(sizes[:-1], out=starts[1:])
    mesh.clear_geometry()
    try:
        mesh.vertices.add(nv)
        mesh.loops.add(nl)
        mesh.polygons.add(npoly)
        mesh.vertices.foreach_set("co", np.ascontiguousarray(co, dtype=np.float32).reshape(-1))
        mesh.loops.foreach_set("vertex_index", np.ascontiguousarray(idx, dtype=np.int32))
        mesh.polygons.foreach_set("loop_start", starts)
        mesh.update(calc_edges=True)
        if len(mesh.polygons) != npoly or len(mesh.vertices) != nv:
            raise RuntimeError("size mismatch")
    except (RuntimeError, TypeError, AttributeError):
        mesh.clear_geometry()   # fall back to the slower, version-proof route
        polys, at = [], 0
        for s in sizes.tolist():
            polys.append(idx[at:at + s].tolist())
            at += s
        mesh.from_pydata(co.tolist(), [], polys)
        mesh.update()


class ForgeBackend(Backend):
    """The QuadForge engine: field-guided quads or isotropic triangles."""

    id = "FORGE"
    label = "FORGE (QuadForge engine)"

    def remesh(self, context, obj, request: RemeshRequest) -> None:
        steps = self.remesh_steps(context, obj, request)
        try:
            while True:
                next(steps)
        except StopIteration:
            return

    def remesh_steps(self, context, obj, request: RemeshRequest):
        mesh = obj.data
        engine = find_engine()
        arrays = mio.read_mesh_arrays(mesh)
        if arrays.n_polys == 0:
            raise BackendError("The mesh has no faces.")
        sizes = np.diff(arrays.poly_offsets).astype(np.int32)
        sharp = mio.read_edge_flag(mesh, "use_edge_sharp") if request.preserve_sharp else None
        features = arrays.edges[sharp] if sharp is not None and sharp.any() else None
        density = mio.read_attribute(mesh, DENSITY_ATTRIBUTE, arrays.n_verts)

        work = Path(tempfile.mkdtemp(prefix="quadforge_"))
        try:
            src, dst = work / "in.fmesh", work / "out.fmesh"
            forge_io.write_fmesh(src, arrays.co, sizes, arrays.loop_verts.astype(np.int32),
                                 features, density, request.guides)

            engine_stats, _report = yield from run_engine_steps(build_command(engine, src, dst, request), request.progress)
            try:
                request.engine_info.update(json.loads(engine_stats) if engine_stats else {})
            except ValueError:
                request.engine_info = {}
            if request.progress is not None:
                request.progress(100, "Reading the result")
            if not dst.is_file():
                raise BackendError("The FORGE engine produced no output.")
            co, out_sizes, out_idx, _features, _density = forge_io.read_fmesh(dst)

            # Detail on Curves on a mechanical shape can add many pole pairs. When the result is clearly irregular,
            # run again without it and keep whichever has fewer irregular vertices.
            first_irregular = _irregular_vertices(request.engine_info)
            verts = int(request.engine_info.get("verts", 0) or 0)
            if (request.curv_angle > 0 and request.output == "QUADS" and verts >= 5000
                    and first_irregular > CURVATURE_RETRY_FRACTION * verts):
                if request.progress is not None:
                    request.progress(60, "Checking the result without Detail on Curves")
                plain = dataclasses.replace(request, curv_angle=0.0, engine_info={})
                dst2 = work / "out_plain.fmesh"
                plain_stats, _ = yield from run_engine_steps(build_command(engine, src, dst2, plain), request.progress)
                try:
                    plain_info = json.loads(plain_stats) if plain_stats else {}
                except ValueError:
                    plain_info = {}
                plain_irregular = _irregular_vertices(plain_info)
                if dst2.is_file() and plain_info and plain_irregular < 0.6 * first_irregular:
                    co, out_sizes, out_idx, _features, _density = forge_io.read_fmesh(dst2)
                    request.engine_info.clear()
                    request.engine_info.update(plain_info)
                    request.engine_info["curv_dropped"] = [first_irregular, plain_irregular]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if len(out_sizes) == 0:
            raise BackendError("The FORGE engine returned an empty mesh.")
        write_result_into_mesh(mesh, co, out_sizes, out_idx)
