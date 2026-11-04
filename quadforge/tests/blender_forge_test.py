"""FORGE integration tests inside Blender (works with the `bpy` module or `blender -b --python`).

    python tests/blender_forge_test.py [path/to/models_folder]

Runs the real add-on and the real engine on the STL models found in the folder (default: ./models next to
this file, if present) plus generated meshes. Prints a table of quality numbers for every run.
"""
import pathlib
import sys
import time
import traceback

import bpy  # must come first
import bmesh
import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
import quadforge  # noqa: E402
from quadforge.core import geometry as geo  # noqa: E402
from quadforge.core import mesh_io as mio  # noqa: E402
from quadforge.core import pipeline  # noqa: E402

MODELS = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent / "models"
FAILS = []


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not hasattr(bpy.types.Scene, "quadforge"):
        quadforge.register()


def import_stl(path):
    before = set(bpy.data.objects)
    bpy.ops.wm.stl_import(filepath=str(path))
    obj = (set(bpy.data.objects) - before).pop()
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    return obj


def sphere(n=64):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=n, v_segments=n // 2, radius=1.0)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new("S")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("S", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def stats_of(obj):
    arrays = mio.read_mesh_arrays(obj.data)
    st = geo.mesh_stats(arrays)
    valence = geo.vertex_valence(arrays)
    interior = valence > 0
    return st, arrays


def run(obj, **kw):
    s = pipeline.RemeshSettings()
    for k, v in kw.items():
        setattr(s, k, v)
    t = time.perf_counter()
    r = pipeline.run(bpy.context, obj, s)
    return r, time.perf_counter() - t


def check(name, fn):
    try:
        fn()
        print(f"PASS  {name}")
    except Exception as e:  # noqa: BLE001
        FAILS.append(name)
        print(f"FAIL  {name}: {e!r}")
        traceback.print_exc()


def row(label, r, secs):
    st = r.levels[0].stats
    poles = st["poles_3"] + st["poles_5plus"]
    per1k = 1000.0 * poles / max(1, st["faces"])
    print(f"      {label:34s} faces {st['faces']:6d} quads {st['quads']:6d} tris {st['tris']:6d} "
          f"poles {poles:4d} ({per1k:5.1f}/1k) border {st['border_edges']} nonman {st['non_manifold_edges']} "
          f"flipped {st['inconsistent_edges']} {secs:5.2f}s")


def valid(r):
    st = r.levels[0].stats
    assert st["non_manifold_edges"] == 0, st
    assert st["inconsistent_edges"] == 0, st


def t_tris_and_quads():
    for out in ("QUADS", "TRIS"):
        reset()
        o = sphere()
        r, secs = run(o, target_quads=1500, output=out, hide_original=False)
        valid(r)
        st = r.levels[0].stats
        if out == "TRIS":
            assert st["tris"] == st["faces"], st
            assert r.object.name.endswith("_tris"), r.object.name
        else:
            assert st["quads"] > 0.95 * st["faces"], st
            assert r.object.name.endswith("_quads"), r.object.name
        row(f"sphere {out}", r, secs)


def t_density_group():
    reset()
    o = sphere(96)
    vg = o.vertex_groups.new(name="Detail")
    left = [v.index for v in o.data.vertices if v.co.x > 0.3]
    vg.add(left, 1.0, "REPLACE")
    r, secs = run(o, target_quads=3000, output="TRIS", density_group="Detail", density_ratio=3.0, hide_original=False)
    valid(r)
    arrays = mio.read_mesh_arrays(r.object.data)
    co = arrays.co
    lens = np.linalg.norm(co[arrays.edges[:, 0]] - co[arrays.edges[:, 1]], axis=1)
    mid = 0.5 * (co[arrays.edges[:, 0]] + co[arrays.edges[:, 1]])
    fine, coarse = lens[mid[:, 0] > 0.6].mean(), lens[mid[:, 0] < -0.3].mean()
    print(f"      painted side edge {fine:.4f}, other side {coarse:.4f}, ratio {coarse / fine:.2f}")
    assert coarse / fine > 1.8, (fine, coarse)


def t_missing_group_warns():
    reset()
    o = sphere()
    r, _ = run(o, target_quads=800, density_group="Nope", hide_original=False)
    assert any("does not exist" in w for w in r.warnings), r.warnings


def t_symmetry_forge():
    for out in ("QUADS", "TRIS"):
        reset()
        o = sphere(64)
        r, secs = run(o, target_quads=1200, output=out, sym_x=True, hide_original=False)
        valid(r)
        st = r.levels[0].stats
        assert st["border_edges"] == 0, st
        co = mio.read_vertex_coordinates(r.object.data)
        assert abs(co[:, 0].max() + co[:, 0].min()) < 1e-3
        row(f"sphere sym X {out}", r, secs)


def t_symmetry_cut_after_forge():
    """Closed model + QUADS: the whole model is remeshed with the planes as guide lines and cut afterwards."""
    for axes in ((True, False, False), (True, True, False), (True, True, True)):
        reset()
        o = sphere(96)
        r, secs = run(o, target_quads=3000, output="QUADS", sym_x=axes[0], sym_y=axes[1], sym_z=axes[2],
                      hide_original=False)
        valid(r)
        st = r.levels[0].stats
        assert st["border_edges"] == 0 and st["non_manifold_edges"] == 0, st
        assert not any("not symmetric" in w or "open edges" in w for w in r.warnings), r.warnings
        co = mio.read_vertex_coordinates(r.object.data)
        for axis, on in enumerate(axes):
            if on:
                assert abs(co[:, axis].max() + co[:, axis].min()) < 1e-3
        assert r.levels[0].hard_edge_count == 0      # the guide lines are not hard edges
        row(f"sphere sym {''.join('XYZ'[i] for i in range(3) if axes[i])} QUADS", r, secs)


def t_levels_forge():
    reset()
    o = sphere(96)
    r, secs = run(o, target_quads=4000, extra_levels="2K, 1K", hide_original=False)
    faces = [lv.stats["faces"] for lv in r.levels]
    print(f"      levels {faces}")
    assert faces[0] > faces[1] > faces[2]
    for lv in r.levels:
        assert lv.stats["non_manifold_edges"] == 0


def t_quadriflow_still_works():
    reset()
    o = sphere()
    r, secs = run(o, target_quads=800, backend="QUADRIFLOW", hide_original=False)
    valid(r)
    row("sphere QuadriFlow", r, secs)


def t_cancel_stops_engine_and_cleans_up():
    import subprocess
    reset()
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=8, radius=1.0)       # 327K triangles: a few seconds of engine time
    mesh = bpy.data.meshes.new("Heavy")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Heavy", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    objects_before, meshes_before = len(bpy.data.objects), len(bpy.data.meshes)
    s = pipeline.RemeshSettings(target_quads=40000)
    seen = []
    gen = pipeline.run_steps(bpy.context, obj, s, lambda p, m: seen.append(m))
    ticks = 0
    while not any("Remeshing" in m or "Pairing" in m or "Smoothing" in m for m in seen) or ticks < 20:
        next(gen)
        ticks += 1
    time.sleep(0.3)
    running = subprocess.run(["pgrep", "-f", "quadforge_.*in.fmesh"], capture_output=True, text=True).stdout.split()
    assert running, "engine should be running at this point"
    gen.close()                                   # what pressing Esc does
    time.sleep(0.3)
    left = subprocess.run(["pgrep", "-f", "quadforge_.*in.fmesh"], capture_output=True, text=True).stdout.split()
    if left:
        info = subprocess.run(["ps", "-o", "pid,stat,cmd", "-p", ",".join(left)], capture_output=True, text=True).stdout
        print(info)
    assert not left, f"engine still running: {left}"
    assert len(bpy.data.objects) == objects_before, "objects left behind"
    assert len(bpy.data.meshes) == meshes_before, [m.name for m in bpy.data.meshes]
    print(f"      cancelled after {ticks} ticks; engine stopped; no leftovers")


def t_operator():
    reset()
    o = sphere()
    bpy.context.scene.quadforge.target_quads = 900
    bpy.context.scene.quadforge.output_type = "TRIS"
    res = bpy.ops.quadforge.remesh("EXEC_DEFAULT")
    assert "FINISHED" in res, res
    assert bpy.context.scene.quadforge.last_has_report
    assert bpy.context.scene.quadforge.last_tris == bpy.context.scene.quadforge.last_faces


def t_models():
    files = sorted(MODELS.glob("*.stl")) if MODELS.is_dir() else []
    if not files:
        print("      (no STL models found, skipped)")
        return
    for f in files:
        for out, target in (("QUADS", 4000), ("TRIS", 4000)):
            reset()
            o = import_stl(f)
            n = len(o.data.polygons)
            r, secs = run(o, target_quads=target, output=out, hide_original=False,
                          sym_x=False, sharp_angle_deg=35.0)
            valid(r)
            row(f"{f.stem[:18]} ({n // 1000}K) {out}", r, secs)
            assert abs(r.levels[0].stats["faces"] - target) / target < 0.15


if __name__ == "__main__":
    reset()
    for fn in (t_tris_and_quads, t_density_group, t_missing_group_warns, t_symmetry_forge, t_symmetry_cut_after_forge, t_levels_forge,
               t_quadriflow_still_works, t_cancel_stops_engine_and_cleans_up, t_operator, t_models):
        check(fn.__name__, fn)
    print("\nALL PASSED" if not FAILS else f"\nFAILED: {FAILS}")
    sys.exit(1 if FAILS else 0)
