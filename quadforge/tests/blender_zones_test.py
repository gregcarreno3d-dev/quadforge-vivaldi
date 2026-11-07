"""Zones: materials / Face Sets / mask survive the remesh, and zones that vanish are reported."""
import sys
import numpy as np
sys.path.insert(0, __import__("os").path.dirname(__file__))
import blender_matrix_test as M
import bpy

fails = 0
def check(ok, name):
    global fails
    print("PASS" if ok else "FAIL", name)
    fails += 0 if ok else 1

def make_sphere():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=1.0)
    obj = bpy.context.active_object
    me = obj.data
    for i in range(3):
        me.materials.append(bpy.data.materials.new(f"zone{i}"))
    P = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", P); P = P.reshape(-1, 3)
    cen = np.array([P[list(p.vertices)].mean(0) for p in me.polygons])
    mat = np.zeros(len(cen), np.int32)
    mat[cen[:, 2] > 0.35] = 1                                  # a cap
    mat[np.argsort(np.linalg.norm(cen - np.array([1, 0, 0]), axis=1))[:3]] = 2   # a speck of 3 faces, far below one face at 3000
    me.polygons.foreach_set("material_index", mat)
    fs = me.attributes.new(".sculpt_face_set", "INT", "FACE"); fs.data.foreach_set("value", (mat + 1).astype(np.int32))
    mk = me.attributes.new(".sculpt_mask", "FLOAT", "POINT"); mk.data.foreach_set("value", np.clip(P[:, 2], 0, 1).astype(np.float32))
    me.update()
    return obj, mat

def run(obj, output="QUADS", **kw):
    s = M.pipeline.RemeshSettings(); s.output = output; s.target_quads = kw.pop('target', 3000)
    s.hide_original = False; s.use_sharp_angle = False; s.use_materials = True
    for k, v in kw.items():
        setattr(s, k, v)
    r = M.pipeline.run(bpy.context, obj, s)
    return r.levels[0].object.data, r.warnings

for output in ("QUADS", "TRIS"):
    obj, mat = make_sphere()
    me, warnings = run(obj, output, target=600 if output == "QUADS" else 150)
    fs = np.empty(len(me.polygons), np.int32); me.attributes[".sculpt_face_set"].data.foreach_get("value", fs)
    mi = np.empty(len(me.polygons), np.int32); me.polygons.foreach_get("material_index", mi)
    check(np.array_equal(fs, mi + 1), f"{output}: Face Sets copied and agree with materials")
    check(set(np.unique(fs).tolist()) >= {1, 2}, f"{output}: the cap is its own Face Set")
    mask = np.empty(len(me.vertices), np.float32); me.attributes[".sculpt_mask"].data.foreach_get("value", mask)
    P = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", P); P = P.reshape(-1, 3)
    err = np.abs(mask - np.clip(P[:, 2], 0, 1))
    check(np.percentile(err, 95) < 0.05, f"{output}: mask copied (p95 error {np.percentile(err, 95):.3f})")
    lost = [w for w in warnings if "no faces" in w]
    if output == "QUADS":
        # the speck is tiny: depending on thread timing it keeps one face or vanishes. Either way nothing may be silent.
        kept = bool((mi == 2).any())
        check((len(lost) == 1 and "zone2" in lost[0]) or (not lost and kept),
              "QUADS: the speck is either kept or reported by name")
    else:
        check(not lost and (mi == 2).any(), "TRIS: the speck keeps its faces and nothing is reported")

obj, mat = make_sphere()
me, warnings = run(obj, transfer_zones=False)
check(me.attributes.get(".sculpt_face_set") is None and me.attributes.get(".sculpt_mask") is None, "switch off: nothing copied")

obj, mat = make_sphere()
obj.data.polygons.foreach_set("material_index", np.where(mat == 2, 0, mat).astype(np.int32)); obj.data.update()
me, warnings = run(obj)
check(not any("no faces" in w or "has no faces" in w for w in warnings), "no report when every zone survives")

obj, mat = make_sphere()
me, warnings = run(obj, sym_x=True)
check(me.attributes.get(".sculpt_face_set") is not None and len(me.polygons) > 0, "symmetry X: Face Sets copied, no crash")
sys.exit(1 if fails else 0)
