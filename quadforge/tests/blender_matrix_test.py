"""Add-on matrix: many shapes x settings through the real add-on and engine (bpy module or blender -b --python).

    python tests/blender_matrix_test.py [--quick]

A run passes when the pipeline finishes, the result is closed/manifold (when the input is), the face count is within
20% of the target, there is no 'pairing method' / 'open edges' / 'not symmetric' warning for a symmetric input, and
symmetric results mirror exactly.
"""
import math
import pathlib
import sys
import time

import bpy
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "devtools" / "engine_tests"))
sys.path.insert(0, str(ROOT / "devtools"))
import quadforge  # noqa: E402
from quadforge.core import geometry as geo  # noqa: E402
from quadforge.core import mesh_io as mio  # noqa: E402
from quadforge.core import pipeline  # noqa: E402
import t07_robustness as T  # noqa: E402
import t08_hard_cases as H  # noqa: E402

QUICK = "--quick" in sys.argv
FAIL = []


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if not hasattr(bpy.types.Scene, "quadforge"):
        quadforge.register()


def make(name, co, tris, rotate=0.0, scale=1.0, loc=(0, 0, 0), materials=False, group=False):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(np.asarray(co, float).tolist(), [], np.asarray(tris).tolist())
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    obj.rotation_euler = (0.3 * rotate, 0.0, rotate)
    obj.scale = (scale,) * 3
    obj.location = loc
    if materials:
        for i in range(2):
            mesh.materials.append(bpy.data.materials.new(f"M{i}"))
        cen = np.asarray(co)[np.asarray(tris)].mean(1)
        idx = (cen[:, 2] > np.median(cen[:, 2])).astype(np.int32)
        mesh.polygons.foreach_set("material_index", idx)
    if group:
        vg = obj.vertex_groups.new(name="Dense")
        z = np.asarray(co)[:, 2]
        w = (z - z.min()) / max(1e-9, np.ptp(z))
        for i in range(len(co)):
            vg.add([i], float(w[i]), "REPLACE")
    return obj


SHAPES = {
    "block-holes": (lambda: T.block_holes(0), (0, 1, 2)),
    "double-torus": (lambda: T.double_torus(0), (0,)),
    "chamfer-cyl": (lambda: T.chamfer_cyl(0), (0, 1)),
    "gear-like": (lambda: T.gear_like(0), ()),
    "creature-2": (lambda: T.creature(2), ()),
    "donut-arms": (lambda: T.donut_arms(0), ()),
    "lumpy-1": (lambda: T.lumpy(1), ()),
    "thin-plate": (lambda: T.thin_plate(0), ()),
    "three-balls": (H.three_balls, ()),
    "open-cap": (H.open_cap, ()),
}

# (label, shape, settings, object kwargs)
RUNS = []


def run_add(label, shape, target=3000, obj=None, **settings):
    RUNS.append((label, shape, target, obj or {}, settings))


for s in SHAPES:
    run_add("default quads", s)
    run_add("triangles", s, output="TRIS")
    run_add("hard edges 30", s, use_sharp_angle=True, sharp_angle_deg=30.0)
    run_add("rotated+scaled object", s, obj=dict(rotate=0.7, scale=2.5, loc=(3, -2, 1)))
for s, axes in SHAPES.items():
    axes = SHAPES[s][1]
    if 0 in axes:
        run_add("sym X", s, sym_x=True)
        run_add("sym X rotated object", s, obj=dict(rotate=1.1, scale=0.4, loc=(5, 5, 5)), sym_x=True)
        run_add("sym X + hard edges", s, sym_x=True, use_sharp_angle=True, sharp_angle_deg=30.0)
        run_add("sym X triangles", s, sym_x=True, output="TRIS")
    if 1 in axes:
        run_add("sym X+Y", s, sym_x=True, sym_y=True)
    if 2 in axes:
        run_add("sym X+Y+Z", s, sym_x=True, sym_y=True, sym_z=True)
for s in ("block-holes", "creature-2", "lumpy-1"):
    run_add("levels 4K,2K,1K", s, target=4000, extra_levels="2K, 1K")
    run_add("zones by material", s, obj=dict(materials=True), use_materials=True)
    run_add("curvature detail medium", s, curvature_detail="MEDIUM")
    run_add("density vertex group", s, obj=dict(group=True), density_group="Dense", density_strength=2.0)
if QUICK:
    RUNS[:] = RUNS[::3]


def check(label, shape, target, objkw, settings):
    reset()
    co, tris = SHAPES[shape][0]()
    open_in = bool(__import__("harness").independent_checks(co, np.full(len(tris), 3), np.asarray(tris).reshape(-1))["boundary_edges"])
    obj = make(shape, co, tris, **objkw)
    s = pipeline.RemeshSettings()
    s.output = "QUADS"
    s.target_quads = target
    s.hide_original = False
    s.use_sharp_angle = False
    for k, v in settings.items():
        setattr(s, k, v)
    t0 = time.perf_counter()
    r = pipeline.run(bpy.context, obj, s)
    secs = time.perf_counter() - t0
    problems = []
    st = r.levels[-1].stats if len(r.levels) == 1 else r.levels[0].stats
    sym_axes = [a for a, on in enumerate((s.sym_x, s.sym_y, s.sym_z)) if on]
    if st["non_manifold_edges"]:
        problems.append(f"non-manifold {st['non_manifold_edges']}")
    if st["border_edges"] and not open_in:
        problems.append(f"open edges {st['border_edges']}")
    if st["inconsistent_edges"]:
        problems.append(f"flipped faces {st['inconsistent_edges']}")
    expect = target if len(r.levels) == 1 else max(lv.target_quads for lv in r.levels)
    big = r.levels[0]
    got = st["faces"]
    if abs(got - big.target_quads) / big.target_quads > 0.20:
        problems.append(f"count {got} vs {big.target_quads}")
    if s.output == "QUADS" and st["quads"] / max(1, st["faces"]) < 0.97:
        problems.append(f"quads {st['quads'] / st['faces']:.0%}")
    for w in r.warnings:
        if "pairing method" in w or "open edges" in w or "non-manifold" in w:
            problems.append("warning: " + w[:70])
        if "not symmetric" in w and shape in ("block-holes", "double-torus", "chamfer-cyl", "gear-like", "thin-plate"):
            problems.append("warning: " + w[:70])
    if sym_axes:
        co_r = mio.read_mesh_arrays(r.levels[0].object.data).co
        from scipy.spatial import cKDTree
        tree = cKDTree(co_r)
        for a in sym_axes:
            m = co_r.copy()
            m[:, a] *= -1
            d, _ = tree.query(m)
            if d.max() > 1e-4 * max(1.0, np.ptp(co_r, axis=0).max()):
                problems.append(f"mirror error axis {a}: {d.max():.2e}")
    pk = 1000 * (st["poles_3"] + st["poles_5plus"]) / max(1, st["faces"])
    return problems, got, pk, secs, [w[:60] for w in r.warnings]


def main():
    ok = 0
    for label, shape, target, objkw, settings in RUNS:
        try:
            problems, got, pk, secs, warns = check(label, shape, target, objkw, settings)
        except Exception as e:  # noqa: BLE001
            problems, got, pk, secs, warns = ["exception " + repr(e)[:120]], 0, 0, 0, []
        good = not problems
        ok += good
        if not good:
            FAIL.append((shape, label))
        print(f"{'ok  ' if good else 'FAIL'} {shape:13s} {label:24s} faces {got:6d} poles/1k {pk:6.1f} {secs:5.1f}s {'; '.join(problems)}", flush=True)
    print(f"\nSUCCESS {ok}/{len(RUNS)} = {100.0 * ok / len(RUNS):.1f}%")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
