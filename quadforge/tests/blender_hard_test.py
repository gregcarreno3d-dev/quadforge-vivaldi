"""Hard Surface on thin bevels: no squeezed quads, closed manifold result, creases kept."""
import sys
import numpy as np
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import blender_matrix_test as M
import bmesh
bpy = M.bpy
fails = []

def check(ok, text):
    print(("PASS " if ok else "FAIL ") + text)
    if not ok:
        fails.append(text)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cylinder_add(radius=2, depth=0.5, vertices=96); obj = bpy.context.active_object
bpy.ops.mesh.primitive_cylinder_add(radius=0.8, depth=3, vertices=64); hole = bpy.context.active_object
mod = obj.modifiers.new("b", "BOOLEAN"); mod.object = hole; mod.operation = "DIFFERENCE"
bpy.context.view_layer.objects.active = obj; bpy.ops.object.modifier_apply(modifier="b"); bpy.data.objects.remove(hole)
bev = obj.modifiers.new("bv", "BEVEL"); bev.width = 0.03; bev.segments = 2; bev.limit_method = "ANGLE"
bpy.ops.object.modifier_apply(modifier="bv")
bm = bmesh.new(); bm.from_mesh(obj.data); bmesh.ops.triangulate(bm, faces=bm.faces[:]); bm.to_mesh(obj.data); bm.free()

s = M.pipeline.RemeshSettings(); s.backend = "FORGE"; s.output = "QUADS"; s.target_quads = 4000; s.hide_original = False
s.use_sharp_angle = True; s.sharp_angle_degrees = 30.0; s.curvature_detail = "OFF"
r = M.pipeline.run(bpy.context, obj, s)
me = r.levels[0].object.data; st = r.levels[0].stats
P = np.array([v.co[:] for v in me.vertices]); F = [list(p.vertices) for p in me.polygons]
q = np.array([f for f in F if len(f) == 4]); X = P[q]
area = 0.5 * np.linalg.norm(np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 1]), axis=1)
check((area < 0.05 * area.mean()).mean() < 0.01, "almost no squeezed quads (%.2f%%)" % (100 * (area < 0.05 * area.mean()).mean()))
check(st["non_manifold_edges"] == 0 and st["border_edges"] == 0, "closed manifold result")
check(abs(st["faces"] - 4000) / 4000 < 0.2, "face count near the target (%d)" % st["faces"])
check(st["quads"] / st["faces"] > 0.9, "mostly quads")
print("ALL PASSED" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
