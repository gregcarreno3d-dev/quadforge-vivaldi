"""Surface buttons and the target list: values are set, the button stays marked, a manual change unmarks it."""
import math
import pathlib
import sys

import bpy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
import quadforge  # noqa: E402

fails = 0
def check(ok, name):
    global fails
    print("PASS" if ok else "FAIL", name)
    fails += 0 if ok else 1

quadforge.register()
s = bpy.context.scene.quadforge
check(s.surface_type == "NONE", "starts with no button marked")

check(bpy.ops.quadforge.set_surface(surface="ORGANIC") == {"FINISHED"}, "organic button runs")
check(s.surface_type == "ORGANIC" and not s.use_sharp_angle and s.curvature_detail == "MEDIUM", "organic: hard edges off, Detail on Curves Medium")
check(bpy.ops.quadforge.set_surface(surface="HARD") == {"FINISHED"}, "hard surface button runs")
check(s.surface_type == "HARD" and s.use_sharp_angle and abs(math.degrees(s.sharp_angle) - 30.0) < 0.01
      and s.curvature_detail == "OFF" and s.snap_features and s.keep_right_angles, "hard surface: edges from 30 degrees, Detail off, snapping on")
check(bpy.ops.quadforge.set_surface(surface="NOPE") == {"CANCELLED"}, "unknown surface is refused")

s.sym_x = True; s.output_type = "TRIS"; s.backend = "FORGE"; s.use_materials = True
bpy.ops.quadforge.set_surface(surface="ORGANIC")
s.backend = "QUADRIFLOW"
check(s.engine == "FORGE" and s.to_settings().backend == "FORGE", "an old scene that stored QuadriFlow still runs FORGE")
s.backend = "FORGE"
check(s.sym_x and s.output_type == "TRIS" and s.backend == "FORGE" and s.use_materials, "buttons leave symmetry, zones, output and engine alone")

check(s.surface_type == "ORGANIC", "button still marked after the profile wrote its values")
s.curvature_detail = "HIGH"
check(s.surface_type == "NONE", "changing a value by hand unmarks the button")
bpy.ops.quadforge.set_surface(surface="HARD")
s.sharp_angle = math.radians(40)
check(s.surface_type == "NONE", "changing the angle by hand unmarks it too")

from quadforge.presets import TARGET_CHOICES, format_target
check(TARGET_CHOICES[0] == 2000 and TARGET_CHOICES[-1] == 500000 and list(TARGET_CHOICES) == sorted(TARGET_CHOICES), "target list runs from 2K to 500K in order")
for value in (2000, 20000, 500000):
    bpy.ops.quadforge.set_target(value=value)
    check(s.target_quads == value, f"target {format_target(value)} sets the count")
check(format_target(120000) == "120K" and format_target(1500) == "1,500", "labels")
quadforge.unregister()
sys.exit(1 if fails else 0)
