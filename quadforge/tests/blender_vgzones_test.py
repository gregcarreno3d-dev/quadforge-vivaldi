"""Zones from vertex groups: separate edge loops between groups, names in warnings, graceful when absent."""
import sys
import numpy as np
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import blender_matrix_test as M
bpy = M.bpy
fails = []

def check(ok, text):
    print(("PASS " if ok else "FAIL ") + text)
    if not ok:
        fails.append(text)

def sphere():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=64, ring_count=32, radius=1)
    obj = bpy.context.active_object
    bpy.ops.object.shade_smooth()
    return obj

def run(obj, **kw):
    s = M.pipeline.RemeshSettings(); s.output = "QUADS"; s.target_quads = 1500; s.hide_original = False
    s.use_sharp_angle = False
    for k, v in kw.items():
        setattr(s, k, v)
    r = M.pipeline.run(bpy.context, obj, s)
    return r.levels[0].object, r.warnings

# two groups: upper and lower half by a wavy plane, so the border is not a straight grid line
obj = sphere()
ga = obj.vertex_groups.new(name="Top"); gb = obj.vertex_groups.new(name="Bottom")
for v in obj.data.vertices:
    top = v.co.z > 0.15 * np.sin(4 * v.co.x)
    (ga if top else gb).add([v.index], 1.0, "REPLACE")
res, w = run(obj, use_vertex_groups=True)
me = res.data
P = np.array([v.co[:] for v in me.vertices])
# edges whose both vertices sit near the wavy border form a loop: count faces straddling the border
f = [list(p.vertices) for p in me.polygons]
side = np.array([P[i, 2] > 0.15 * np.sin(4 * P[i, 0]) for i in range(len(P))])
mixed = sum(1 for p in f if 0 < side[p].sum() < len(p))
# the same split made with materials uses the long-tested zone path: both must behave alike
obj_m = sphere()
bpy.data.materials.new("A"); bpy.data.materials.new("B")
obj_m.data.materials.append(bpy.data.materials["A"]); obj_m.data.materials.append(bpy.data.materials["B"])
cz = np.array([p.center for p in obj_m.data.polygons])
obj_m.data.polygons.foreach_set("material_index", (cz[:, 2] <= 0.15 * np.sin(4 * cz[:, 0])).astype(np.int32)); obj_m.data.update()
res2, _ = run(obj_m, use_materials=True)
P2 = np.array([v.co[:] for v in res2.data.vertices]); side2 = np.array([P2[i, 2] > 0.15 * np.sin(4 * P2[i, 0]) for i in range(len(P2))])
mixed2 = sum(1 for p in res2.data.polygons if 0 < side2[list(p.vertices)].sum() < len(p.vertices))
check(abs(mixed - mixed2) <= 0.35 * max(mixed2, 1) + 6, "border behaves like the material zones (%d vs %d straddling faces)" % (mixed, mixed2))
check(not any("Vertex groups were requested" in x for x in w), "no complaint when groups have weights")

# no vertex groups at all: warns, does not crash
obj3 = sphere()
res3, w3 = run(obj3, use_vertex_groups=True)
check(len(res3.data.polygons) > 0 and any("none has weights" in x or "no vertex groups" in x for x in w3), "no groups: warning, still remeshes")
print("ALL PASSED" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
