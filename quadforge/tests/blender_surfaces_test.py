"""Surface analysis: attributes, colours, selection operators and the remesh with Use Surface Analysis on and off."""
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
import quadforge
try:
    quadforge.register()
except Exception:   # already registered by the shared module
    pass
bpy.ops.mesh.primitive_cylinder_add(radius=20, depth=5, vertices=120); obj = bpy.context.active_object
bpy.ops.mesh.primitive_cylinder_add(radius=3, depth=12, vertices=48, location=(8, 0, 0)); hole = bpy.context.active_object
mod = obj.modifiers.new("b", "BOOLEAN"); mod.object = hole; mod.operation = "DIFFERENCE"
bpy.context.view_layer.objects.active = obj; bpy.ops.object.modifier_apply(modifier="b"); bpy.data.objects.remove(hole)
bev = obj.modifiers.new("bv", "BEVEL"); bev.width = 0.8; bev.segments = 1; bev.limit_method = "ANGLE"
bpy.ops.object.modifier_apply(modifier="bv")
bm = bmesh.new(); bm.from_mesh(obj.data); bmesh.ops.triangulate(bm, faces=bm.faces[:]); bm.to_mesh(obj.data); bm.free()
bpy.context.view_layer.objects.active = obj
obj.select_set(True)

r = bpy.ops.quadforge.analyze_surfaces()
check(r == {"FINISHED"}, "Analyze Surfaces runs")
me = obj.data
for name in ("qf_surface", "qf_surface_kind", "qf_border", "qf_band"):
    check(me.attributes.get(name) is not None, f"attribute {name} written")
kinds = np.zeros(len(me.polygons), dtype=np.int32)
me.attributes["qf_surface_kind"].data.foreach_get("value", kinds)
from quadforge.core import surfaces as surf
present = {surf.KIND_NAMES[k] for k in set(kinds.tolist())}
print("kinds present:", present)
check({"plane", "cylinder", "chamfer"} <= present, "planes, cylinders and chamfers found on a bevelled plate with a hole")
check("QuadForge Surfaces" in bpy.data.texts, "text table written")
check("QuadForge Surfaces" in me.color_attributes, "colour layer written")
bpy.context.scene.quadforge.surface_view = "SURFACE"
check(bpy.context.scene.quadforge.surface_view == "SURFACE", "colour mode can be switched")
check(bpy.ops.quadforge.select_surface_kind() == {"FINISHED"}, "Select Faces by kind")
bpy.ops.object.mode_set(mode="OBJECT")
check(bpy.ops.quadforge.select_borders(kind="ALL") == {"FINISHED"}, "Select Borders")
bpy.ops.object.mode_set(mode="OBJECT")

results = {}
for flag in (False, True):
    s = M.pipeline.RemeshSettings(); s.output = "QUADS"; s.target_quads = 4000; s.hide_original = False
    s.use_sharp_angle = True; s.sharp_angle_degrees = 30.0; s.curvature_detail = "OFF"; s.use_surface_analysis = flag
    res = M.pipeline.run(bpy.context, obj, s)
    st = res.levels[0].stats
    results[flag] = st
    print("surface analysis", flag, "faces", st["faces"], "quads", st["quads"], "poles", st.get("poles"), "hard", res.levels[0].hard_edge_count)
    check(st["non_manifold_edges"] == 0 and st["border_edges"] == 0, f"closed manifold result (analysis {flag})")
    check(st["quads"] / st["faces"] > 0.9, f"mostly quads (analysis {flag})")
print("ALL PASSED" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
