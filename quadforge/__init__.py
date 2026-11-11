"""QuadForge: quad remesher with a target count, level ladder, hard edges, zones and exact symmetry.

Map of the package (read in this order to understand it):

    docs/           the technical study, decisions, benchmark protocol and references
    texts.py        every sentence the user reads (English)
    translations.py Spanish version of those sentences
    presets.py      one-click starting points (Prop, Character, Ladder 20K to 4K ...)
    planning.py     pure rules: which levels to build, which levels to build
    guidance.py     rules that produce the live advice (pure Python)
    properties.py   the settings stored in the scene
    operators.py    the buttons (Remesh, Apply Preset, Reset)
    ui.py           the sidebar panel
    core/           the engine room, no UI code:
        geometry.py     NumPy mesh analysis (normals, angles, boundaries, relaxation, stats)
        mesh_io.py      fast Blender <-> NumPy transfer
        symmetry.py     cut, mirror, weld the seam and fix the face orientation
        features.py     smart hard-edge detection (chains, quad-size filter, right angles)
        detail.py       relax badly shaped quads and project them back
        profiling.py    time per stage and memory of the Blender process
        backends/       remesh engines behind one interface (FORGE, the own engine)
        pipeline.py     the remesh recipe: stages from "read mesh" to "result objects"
"""
import bpy

from . import translations
from .operators import (
    QUADFORGE_OT_apply_preset,
    QUADFORGE_OT_clear_guides,
    QUADFORGE_OT_detect_bands,
    QUADFORGE_OT_mark_guides,
    QUADFORGE_OT_preview,
    QUADFORGE_OT_refresh_zones,
    QUADFORGE_OT_remesh,
    QUADFORGE_OT_remesh_modal,
    QUADFORGE_OT_reset_settings,
    QUADFORGE_OT_set_surface,
    QUADFORGE_OT_set_target,
)
from .properties import QuadForgeProperties, QuadForgeZoneFactor
from .ui import (
    QUADFORGE_MT_target,
    QUADFORGE_PT_advanced,
    QUADFORGE_PT_bands,
    QUADFORGE_PT_density,
    QUADFORGE_PT_guides,
    QUADFORGE_PT_hard_edges,
    QUADFORGE_PT_how_to_use,
    QUADFORGE_PT_last_result,
    QUADFORGE_PT_levels,
    QUADFORGE_PT_main,
    QUADFORGE_PT_symmetry,
    QUADFORGE_PT_zones,
)

# Parent panel first: Blender needs it registered before its sub-panels.
_CLASSES = (
    QuadForgeZoneFactor,
    QuadForgeProperties,
    QUADFORGE_OT_remesh,
    QUADFORGE_OT_remesh_modal,
    QUADFORGE_OT_preview,
    QUADFORGE_OT_refresh_zones,
    QUADFORGE_OT_detect_bands,
    QUADFORGE_OT_apply_preset,
    QUADFORGE_OT_reset_settings,
    QUADFORGE_OT_set_surface,
    QUADFORGE_OT_set_target,
    QUADFORGE_OT_mark_guides,
    QUADFORGE_OT_clear_guides,
    QUADFORGE_MT_target,
    QUADFORGE_PT_main,
    QUADFORGE_PT_bands,
    QUADFORGE_PT_hard_edges,
    QUADFORGE_PT_guides,
    QUADFORGE_PT_zones,
    QUADFORGE_PT_symmetry,
    QUADFORGE_PT_levels,
    QUADFORGE_PT_density,
    QUADFORGE_PT_last_result,
    QUADFORGE_PT_how_to_use,
    QUADFORGE_PT_advanced,
)


def register():
    for blender_class in _CLASSES:
        bpy.utils.register_class(blender_class)
    bpy.types.Scene.quadforge = bpy.props.PointerProperty(type=QuadForgeProperties)
    translations.register_translations()


def unregister():
    translations.unregister_translations()
    del bpy.types.Scene.quadforge
    for blender_class in reversed(_CLASSES):
        bpy.utils.unregister_class(blender_class)
