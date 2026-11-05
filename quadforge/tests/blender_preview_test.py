"""Quick Preview: makes a small tagged copy, never touches the original, is replaced by the next preview and
removed by the full Remesh. Run: python run_in_bpy.py blender_preview_test.py (bpy module)."""
import sys
import time
from pathlib import Path

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


def previews():
    return [o for o in bpy.data.objects if o.get("quadforge_preview_of")]


bm = bmesh.new()
bmesh.ops.create_icosphere(bm, subdivisions=6, radius=1.0)      # 20K triangles
mesh = bpy.data.meshes.new("src")
bm.to_mesh(mesh)
obj = bpy.data.objects.new("src", mesh)
bpy.context.scene.collection.objects.link(obj)
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
settings = bpy.context.scene.quadforge
settings.target_quads = 12000
settings.sym_x = True
before = len(mesh.polygons)

t = time.time()
bpy.ops.quadforge.preview()
seconds = time.time() - t
found = previews()
check("one preview made", len(found) == 1)
check("preview is small", found and len(found[0].data.polygons) <= 3300, str(found and len(found[0].data.polygons)))
check("preview is quads", found and all(len(p.vertices) == 4 for p in found[0].data.polygons))
check("preview is quick", seconds < 5.0, f"{seconds:.1f} s")
check("original untouched and visible", len(mesh.polygons) == before and not obj.hide_get() and not obj.hide_viewport)
check("original stays active", bpy.context.active_object is obj)
check("preview has no preview button of its own", not quadforge.operators.QUADFORGE_OT_preview.poll(
    type("C", (), {"active_object": found[0]})()))

bpy.ops.quadforge.preview()
check("next preview replaces the old one", len(previews()) == 1)

settings.target_quads = 2000          # below the preview size: the preview is the target size
bpy.ops.quadforge.preview()
check("small target keeps target size", previews() and len(previews()[0].data.polygons) <= 2300)

settings.target_quads = 6000
bpy.ops.quadforge.remesh()            # blocking path
check("full remesh removes previews", len(previews()) == 0)
check("full remesh made its result", any(o.name.startswith("src_quads") for o in bpy.data.objects))
print("ALL PASSED" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
