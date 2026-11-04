"""UV transfer: a UV sphere (one seam) is remeshed and the UVs must follow the surface."""
import math, sys
import numpy as np
sys.path.insert(0, __import__("os").path.dirname(__file__))
import blender_matrix_test as M
import bpy

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_uv_sphere_add(segments=96, ring_count=48, radius=1.0)
obj = bpy.context.active_object
src_loops = len(obj.data.loops)
assert len(obj.data.uv_layers) == 1

def run(output, transfer=True, sym=False):
    s = M.pipeline.RemeshSettings(); s.backend = "FORGE"; s.output = output; s.target_quads = 4000
    s.hide_original = False; s.transfer_uvs = transfer; s.use_sharp_angle = False; s.sym_x = sym
    r = M.pipeline.run(bpy.context, obj, s)
    return r.levels[0].object

fails = 0
for output in ("QUADS", "TRIS"):
    res = run(output)
    me = res.data
    assert len(me.uv_layers) == 1, "UV map missing on the result"
    uv = np.empty(len(me.loops) * 2, np.float32); me.uv_layers[0].uv.foreach_get("vector", uv); uv = uv.reshape(-1, 2)
    co = np.empty(len(me.vertices) * 3, np.float32); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    lv = np.empty(len(me.loops), np.int32); me.loops.foreach_get("vertex_index", lv)
    p = co[lv]
    # Blender's sphere: u = atan2(y, x)/(2 pi) + 0.5 wraps around; v = latitude
    exp_v = np.arcsin(np.clip(p[:, 2] / np.linalg.norm(p, axis=1), -1, 1)) / math.pi + 0.5
    away_from_poles = np.abs(p[:, 2]) < 0.9
    dv = np.abs(uv[:, 1] - exp_v)[away_from_poles]
    start = np.empty(len(me.polygons), np.int32); total = np.empty(len(me.polygons), np.int32)
    me.polygons.foreach_get("loop_start", start); me.polygons.foreach_get("loop_total", total)
    span = np.array([np.ptp(uv[a:a + t, 0]) for a, t in zip(start, total)])
    jump = (span > 0.5).mean()
    print(f"{output}: v error median {np.median(dv):.4f} p99 {np.percentile(dv, 99):.4f}; faces spanning the seam {100*jump:.2f}%")
    ok = np.percentile(dv, 99) < 0.03 and jump < 0.03
    print("PASS" if ok else "FAIL", f"uv transfer {output}")
    fails += 0 if ok else 1
res = run("QUADS", sym=True)
me = res.data
uv = np.empty(len(me.loops) * 2, np.float32); me.uv_layers[0].uv.foreach_get("vector", uv); uv = uv.reshape(-1, 2)
start = np.empty(len(me.polygons), np.int32); total = np.empty(len(me.polygons), np.int32)
me.polygons.foreach_get("loop_start", start); me.polygons.foreach_get("loop_total", total)
span = np.array([np.ptp(uv[a:a + t, 0]) for a, t in zip(start, total)])
ok = len(me.uv_layers) == 1 and (span > 0.5).mean() < 0.03 and np.isfinite(uv).all()
print("PASS" if ok else "FAIL", "uv transfer with symmetry X"); fails += 0 if ok else 1
res = run("QUADS", transfer=False)
assert len(res.data.uv_layers) == 0, "switch off must not copy"
print("PASS uv transfer can be switched off")
sys.exit(1 if fails else 0)
