"""Rounded (soft) bevels and thin chamfer strips on a hard-surface part: the remesh must keep their profile."""
import sys
import numpy as np
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import blender_matrix_test as M
import bmesh
from mathutils.bvhtree import BVHTree
bpy = M.bpy
fails = []


def check(ok, text):
    print(("PASS " if ok else "FAIL ") + text)
    if not ok:
        fails.append(text)


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cylinder_add(radius=10, depth=2.5, vertices=128)
    obj = bpy.context.active_object
    for angle in (0, 90, 180, 270):
        x = 6.0 * np.cos(np.radians(angle)); y = 6.0 * np.sin(np.radians(angle))
        bpy.ops.mesh.primitive_cylinder_add(radius=2.0, depth=6, vertices=64, location=(x, y, 0))
        hole = bpy.context.active_object
        mod = obj.modifiers.new("b", "BOOLEAN"); mod.object = hole; mod.operation = "DIFFERENCE"
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier="b"); bpy.data.objects.remove(hole)
    bev = obj.modifiers.new("bv", "BEVEL"); bev.width = 0.35; bev.segments = 4; bev.limit_method = "ANGLE"
    bpy.ops.object.modifier_apply(modifier="bv")
    bm = bmesh.new(); bm.from_mesh(obj.data); bmesh.ops.triangulate(bm, faces=bm.faces[:]); bm.to_mesh(obj.data); bm.free()
    return obj


def tree(obj):
    bm = bmesh.new(); bm.from_mesh(obj.data); bmesh.ops.triangulate(bm, faces=bm.faces[:])
    t = BVHTree.FromBMesh(bm); bm.free(); return t


def deviation(src, res):
    ts = tree(src); tr = tree(res)
    pts = [v.co.copy() for v in src.data.vertices] + [p.center.copy() for p in src.data.polygons]
    d = np.array([(p - tr.find_nearest(p)[0]).length for p in pts]) / 20.0 * 100   # percent of the diameter
    return d


def remesh(src, soft):
    s = M.pipeline.RemeshSettings(); s.backend = "FORGE"; s.output = "QUADS"; s.target_quads = 20000; s.hide_original = False
    s.use_sharp_angle = True; s.sharp_angle_deg = 30.0; s.curvature_detail = "OFF"; s.soft_bevels = soft
    r = M.pipeline.run(bpy.context, src, s)
    return r.levels[0].object, r.levels[0].stats


src = build()
with_soft, st = remesh(src, True)
d_on = deviation(src, with_soft)
check(st["non_manifold_edges"] == 0 and st["border_edges"] == 0, "closed manifold result")
check(st["quads"] / st["faces"] > 0.9, "mostly quads")
check(abs(st["faces"] - 20000) / 20000 < 0.2, "face count near the target (%d)" % st["faces"])
without, _ = remesh(src, False)
d_off = deviation(src, without)
p_on, p_off = np.percentile(d_on, 99), np.percentile(d_off, 99)
print("p99 deviation (percent of diameter): soft bevels on %.3f, off %.3f" % (p_on, p_off))
check(p_on < 0.6, "profile of the rounded bevels kept (p99 %.3f percent of the diameter)" % p_on)
check(p_on <= p_off * 1.05, "soft bevel handling is not worse than without it")
print("ALL PASSED" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
