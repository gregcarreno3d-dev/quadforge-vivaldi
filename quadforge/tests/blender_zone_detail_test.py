"""Detail by Zone: a per-zone detail multiplier changes the face density of that zone only.
Run: python run_in_bpy.py blender_zone_detail_test.py (bpy module)."""
import sys
from pathlib import Path

import numpy as np
import bpy
import bmesh

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import quadforge  # noqa: E402

quadforge.register()
fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name, detail)
    if not ok:
        fails.append(name)


def make(name="zsrc"):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=6, radius=1.0)
    mesh = bpy.data.meshes.new(name)
    for color in ("Top", "Bottom"):
        mesh.materials.append(bpy.data.materials.new(f"{name}_{color}"))
    for face in bm.faces:
        face.material_index = 0 if face.calc_center_median().z >= 0 else 1
    bm.to_mesh(mesh)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def counts(result_obj):
    mesh = result_obj.data
    z = np.array([p.center.z for p in mesh.polygons])
    return int((z >= 0).sum()), int((z < 0).sum())


obj = make()
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
s = bpy.context.scene.quadforge
s.target_quads = 4000
s.use_materials = True

bpy.ops.quadforge.remesh()
base = [o for o in bpy.data.objects if o.name.startswith("zsrc_quads")][0]
top0, bottom0 = counts(base)
print("baseline top/bottom", top0, bottom0)
check("baseline is balanced", 0.7 < top0 / bottom0 < 1.4)
bpy.data.objects.remove(base)

bpy.context.view_layer.objects.active = obj
bpy.ops.quadforge.refresh_zones()
names = [i.name for i in s.zone_factors]
check("two zones listed", len(names) == 2, str(names))
s.zone_factors[0].factor = 2.0
check("factors reach the settings", len(s.to_settings().zone_factors) == 1)

bpy.context.view_layer.objects.active = obj
bpy.ops.quadforge.remesh()
res = [o for o in bpy.data.objects if o.name.startswith("zsrc_quads")][0]
top, bottom = counts(res)
fine, coarse = (top, bottom) if top > bottom else (bottom, top)
ratio = fine / max(1, coarse)
print("warnings:", s.last_warnings)
print("with factor 2: top/bottom", top, bottom, "ratio", round(ratio, 2))
check("fine zone has clearly more faces", ratio > 1.4, f"ratio {ratio:.2f}")
check("clean quad flow kept (no pairing fallback)", not any("pairing" in w for w in s.last_warnings), str(s.last_warnings))
bm = bmesh.new(); bm.from_mesh(res.data)
check("result is closed", not any(e.is_boundary for e in bm.edges))
check("result is quads", all(len(f.verts) == 4 for f in bm.faces), f"{len(bm.faces)} faces")
bm.free()
mats = {p.material_index for p in res.data.polygons}
check("both zones kept", mats == {0, 1})
print("ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
