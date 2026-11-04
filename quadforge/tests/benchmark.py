"""Benchmark: measures time, memory, detail preservation, symmetry and topology quality.

Without numbers, "optimised" cannot be proven. This script produces them, in a format
that can be pasted into docs/BENCHMARK.md and compared between machines and versions.

Run it inside Blender, from the folder that contains the ``quadforge`` folder.

  Your own sculpt (open the .blend, give the object name):
    blender yourfile.blend -b --python quadforge/tests/benchmark.py -- --object Dragon \\
        --targets 20000,15000,10000,5000,4000 --sym X --polish 3 --out dragon.json

  A synthetic mesh (no file needed), 1.3 million triangles:
    blender --factory-startup -b --python quadforge/tests/benchmark.py -- --synthetic-subdivisions 9

Options (all after the ``--``):
  --object NAME                 object to remesh (default: a synthetic blob)
  --synthetic-subdivisions N    icosphere subdivisions of the synthetic blob (8 = 328K, 9 = 1.3M, 10 = 5.2M tris)
  --targets A,B,C               quad targets, largest first (default 20000,10000,5000,4000)
  --sym X|Y|Z|XY|XZ|YZ|XYZ     symmetry axes (default none)
  --no-proxy                    do NOT use the working copy (to measure what it saves)
  --no-snap                     do NOT snap back onto the original sculpt
  --polish N                    polish passes (default 3)
  --out FILE.json               where to write the results (default quadforge_benchmark.json)
"""
import argparse
import json
import math
import os
import pathlib
import platform
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
import quadforge  # noqa: E402
from quadforge import planning  # noqa: E402
from quadforge.core import geometry as geo  # noqa: E402
from quadforge.core import mesh_io as mio  # noqa: E402
from quadforge.core import pipeline  # noqa: E402

SAMPLE_VERTICES = 5000      # vertices used to measure distance and symmetry (keeps the measuring cheap)


def parse_arguments():
    after_separator = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description="QuadForge benchmark")
    parser.add_argument("--object", default=None)
    parser.add_argument("--synthetic-subdivisions", type=int, default=9)
    parser.add_argument("--targets", default="20000,10000,5000,4000")
    parser.add_argument("--sym", default="")
    parser.add_argument("--no-proxy", action="store_true")
    parser.add_argument("--no-snap", action="store_true")
    parser.add_argument("--polish", type=int, default=3)
    parser.add_argument("--out", default="quadforge_benchmark.json")
    return parser.parse_args(after_separator)


def total_memory_mb():
    """Installed RAM in MB (best effort)."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/meminfo") as meminfo:
                return int(meminfo.readline().split()[1]) / 1024.0
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / (1024.0 * 1024.0)
    except Exception:  # noqa: BLE001
        pass
    return None


def environment_report():
    return {
        "blender": bpy.app.version_string,
        "python": platform.python_version(),
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_threads": os.cpu_count(),
        "ram_mb": total_memory_mb(),
    }


def make_synthetic_blob(subdivisions):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)
    for vertex in bm.verts:
        vertex.co.x *= 1.3
        vertex.co.z *= 0.8 + 0.15 * math.cos(3.0 * vertex.co.y)
    mesh = bpy.data.meshes.new("BenchmarkBlob")
    bm.to_mesh(mesh)
    bm.free()
    blob = bpy.data.objects.new("BenchmarkBlob", mesh)
    bpy.context.scene.collection.objects.link(blob)
    return blob


def detail_error(original, result_object):
    """Distance of sampled result vertices to the ORIGINAL surface, relative to its size."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    surface = BVHTree.FromObject(original.evaluated_get(depsgraph), depsgraph)
    coordinates = mio.read_vertex_coordinates(result_object.data).astype(np.float64)
    corners = np.array([tuple(c) for c in original.bound_box])
    diagonal = float(np.linalg.norm(corners.max(axis=0) - corners.min(axis=0)))
    chosen = np.random.default_rng(1).choice(len(coordinates), size=min(SAMPLE_VERTICES, len(coordinates)), replace=False)
    distances = np.array([(surface.find_nearest(Vector(coordinates[i]))[3] or 0.0) for i in chosen]) / diagonal
    return {"mean": float(distances.mean()), "p95": float(np.percentile(distances, 95)), "max": float(distances.max())}


def symmetry_error(result_object, axes):
    """Worst distance from a mirrored sample vertex to the nearest real vertex (relative to the diagonal)."""
    if not axes:
        return None
    coordinates = mio.read_vertex_coordinates(result_object.data).astype(np.float64)
    tree = KDTree(len(coordinates))
    for index, point in enumerate(coordinates):
        tree.insert(Vector(point), index)
    tree.balance()
    diagonal = geo.bbox_diagonal(coordinates)
    chosen = np.random.default_rng(2).choice(len(coordinates), size=min(SAMPLE_VERTICES, len(coordinates)), replace=False)
    errors = {}
    for axis in axes:
        worst = 0.0
        for index in chosen:
            mirrored = coordinates[index].copy()
            mirrored[axis] = -mirrored[axis]
            worst = max(worst, tree.find(Vector(mirrored))[2])
        errors["XYZ"[axis]] = worst / diagonal
    return errors


def topology_quality(result_object):
    arrays = mio.read_mesh_arrays(result_object.data)
    stats = geo.mesh_stats(arrays)
    lengths = np.linalg.norm(arrays.co[arrays.edges[:, 0]] - arrays.co[arrays.edges[:, 1]], axis=1)
    stats["quad_percent"] = round(100.0 * stats["quads"] / max(1, stats["faces"]), 2)
    stats["edge_length_variation"] = round(float(lengths.std() / lengths.mean()), 4)
    return stats


def main():
    arguments = parse_arguments()
    quadforge.register()

    original = bpy.data.objects[arguments.object] if arguments.object else make_synthetic_blob(arguments.synthetic_subdivisions)
    bpy.context.view_layer.objects.active = original
    original.select_set(True)

    targets = [int(value) for value in arguments.targets.split(",") if value.strip()]
    settings = pipeline.RemeshSettings(
        target_quads=targets[0],
        extra_levels=",".join(str(value) for value in targets[1:]),
        use_proxy=not arguments.no_proxy,
        use_reproject=not arguments.no_snap,
        polish_passes=arguments.polish,
        sym_x="X" in arguments.sym.upper(), sym_y="Y" in arguments.sym.upper(), sym_z="Z" in arguments.sym.upper(),
    )
    axes = [axis for axis, letter in enumerate("XYZ") if letter in arguments.sym.upper()]

    print(f"Source: {original.name}, {len(original.data.polygons):,} faces. Targets: {targets}. Symmetry: {arguments.sym or 'none'}")
    result = pipeline.run(bpy.context, original, settings)

    levels = []
    for level in result.levels:
        levels.append({
            "target": level.target_quads,
            "seconds": round(level.seconds, 2),
            "topology": topology_quality(level.object),
            "distance_to_original": detail_error(original, level.object),
            "symmetry_error": symmetry_error(level.object, axes),
        })

    report = {
        "environment": environment_report(),
        "source": {"object": original.name, "faces": result.source_faces},
        "settings": {"targets": targets, "symmetry": arguments.sym or "none", "working_copy": not arguments.no_proxy,
                     "working_copy_faces_used": result.proxy_faces_used, "snap_to_original": not arguments.no_snap,
                     "polish_passes": arguments.polish},
        "profile": result.profile,
        "levels": levels,
        "warnings": result.warnings,
    }
    pathlib.Path(arguments.out).write_text(json.dumps(report, indent=2))

    print("\n=== STAGES ===")
    for stage in result.profile["stages"]:
        print(f"  {stage['name']:<38} {stage['seconds']:>8.2f} s   memory after: {stage['memory_after_mb']} MB")
    print(f"  TOTAL {result.profile['total_seconds']} s | peak memory {result.profile['peak_memory_mb']} MB")
    print("\n=== LEVELS (paste into docs/BENCHMARK.md) ===")
    print("| target | faces | quads % | poles | edge variation | mean dist. to sculpt | max dist. | symmetry error |")
    print("|---|---|---|---|---|---|---|---|")
    for level in levels:
        topology = level["topology"]
        sym_text = "-" if level["symmetry_error"] is None else max(level["symmetry_error"].values())
        print(f"| {planning.format_target(level['target'])} | {topology['faces']} | {topology['quad_percent']} | "
              f"{topology['poles_3'] + topology['poles_5plus']} | {topology['edge_length_variation']} | "
              f"{level['distance_to_original']['mean']:.2e} | {level['distance_to_original']['max']:.2e} | {sym_text} |")
    print(f"\nWarnings: {result.warnings or 'none'}")
    print(f"Full results written to {arguments.out}")
    quadforge.unregister()


if __name__ == "__main__":
    main()
