"""Subdivision Match: after a Subdivision Surface modifier the result must lie closer to the source than without it.
Run: python run_in_bpy.py blender_subdiv_test.py (bpy module)."""
import sys
from pathlib import Path

import bpy
import bmesh
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import quadforge  # noqa: E402

quadforge.register()
fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name, detail)
    if not ok:
        fails.append(name)


def make_blob():
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=6, radius=1.0)
    for v in bm.verts:   # lumpy: not a plain sphere, so the test is not satisfied by a trivial fit
        p = v.co.copy()
        v.co = p * (1.0 + 0.18 * np.sin(3 * p.x) * np.cos(2 * p.y) + 0.1 * p.z)
    mesh = bpy.data.meshes.new("blob")
    bm.to_mesh(mesh)
    obj = bpy.data.objects.new("blob", mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def run(obj, match):
    bpy.context.view_layer.objects.active = obj
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    s = bpy.context.scene.quadforge
    s.target_quads = 1500
    s.subdivision_match = match
    bpy.ops.quadforge.remesh()
    res = [o for o in bpy.data.objects if o.name.startswith("blob_quads")][-1]
    return res


def subdivided_deviation(res, source):
    mod = res.modifiers.new("s", "SUBSURF")
    mod.levels = 2
    mod.render_levels = 2
    dg = bpy.context.evaluated_depsgraph_get()
    ev = res.evaluated_get(dg)
    mesh = ev.to_mesh()
    from mathutils.bvhtree import BVHTree
    tree = BVHTree.FromObject(source, dg)
    d = np.array([np.linalg.norm(np.array(tree.find_nearest(v.co)[0]) - np.array(v.co)) for v in mesh.vertices])
    ev.to_mesh_clear()
    res.modifiers.remove(mod)
    diag = np.linalg.norm(np.ptp(np.array([v.co for v in source.data.vertices]), axis=0))
    return 100 * d.mean() / diag, 100 * d.max() / diag


def min_jacobian(res):
    sys.path.insert(0, "/home/claude/quadforge-vivaldi/tools")
    import quality_metrics as qm
    from quadforge.core import mesh_io as mio
    a = mio.read_mesh_arrays(res.data)
    j, _ = qm.scaled_jacobian(a.co, np.diff(a.poly_offsets), a.loop_verts)
    return float(j.mean()), float(np.percentile(j, 1))


src = make_blob()
plain = run(src, False)
plain_mean, plain_max = subdivided_deviation(plain, src)
plain_j = min_jacobian(plain)
bpy.data.objects.remove(plain)
matched = run(src, True)
match_mean, match_max = subdivided_deviation(matched, src)
match_j = min_jacobian(matched)
print(f"subdivided deviation, % of size: plain mean {plain_mean:.3f} max {plain_max:.3f} | match mean {match_mean:.3f} max {match_max:.3f}")
print(f"scaled Jacobian mean/p1: plain {plain_j} | match {match_j}")
check("closer to the source after subdividing", match_mean < 0.6 * plain_mean, f"{match_mean:.3f} vs {plain_mean:.3f}")
check("worst point does not get worse", match_max <= plain_max * 1.05)
check("quad quality kept", match_j[0] > plain_j[0] - 0.02 and match_j[1] > plain_j[1] - 0.08)
check("still all quads", all(len(p.vertices) == 4 for p in matched.data.polygons))

# with symmetry the result must stay symmetric and the points on the plane must stay on it
bpy.data.objects.remove(matched)
bm = bmesh.new()
bmesh.ops.create_icosphere(bm, subdivisions=6, radius=1.0)
for v in bm.verts:
    p = v.co.copy()
    v.co = p * (1.0 + 0.2 * np.cos(2 * p.x) * np.cos(2 * p.y) + 0.1 * p.z)
mesh = bpy.data.meshes.new("blob")
bm.to_mesh(mesh)
bpy.data.objects.remove(src)
src = bpy.data.objects.new("blob", mesh)
bpy.context.scene.collection.objects.link(src)
bpy.context.scene.quadforge.sym_x = True
sym = run(src, True)
co = np.array([v.co[:] for v in sym.data.vertices])
from scipy.spatial import cKDTree
mirror = co * np.array([-1, 1, 1])
dist, _ = cKDTree(co).query(mirror)
size = np.linalg.norm(np.ptp(co, axis=0))
check("symmetry kept after the fit", dist.max() < 2e-5 * size, f"max asymmetry {100 * dist.max() / size:.4f} % of size")
print("ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
