"""The sidebar panel (View3D > N key > QuadForge).

Layout, from top to bottom:
    QuadForge .............. live advice, presets, target, the Remesh button
      Hard Edges ........... optional, collapsed
      Isolate Zones ........ optional, collapsed
      Symmetry ............. optional, collapsed
      Extra Levels ......... optional, collapsed (build 15K, 10K, 5K ... in one go)
      Speed and Detail ..... optional, collapsed (working copy, polish)
      Last Result .......... appears after the first run
      How to Use ........... step-by-step guide, collapsed
      Advanced ............. collapsed

A beginner only needs the first panel: pick a preset, press the button.
"""
import math
import re
import textwrap

import bpy
from bpy.app.translations import pgettext_iface as translate

from . import guidance, operators, planning, texts
from .presets import HEAVY_TARGET, PRESETS, SURFACE_PROFILES, TARGET_CHOICES, format_target

# Icon used for each level of advice.
LEVEL_ICONS = {
    guidance.ERROR: "CANCEL",
    guidance.WARNING: "ERROR",
    guidance.INFO: "INFO",
    guidance.OK: "CHECKMARK",
}


# --------------------------------------------------------------------------- drawing helpers

def _characters_per_line(context) -> int:
    """Blender labels never wrap, so estimate how many characters fit in the panel width."""
    region_width = context.region.width if context.region else 320
    ui_scale = context.preferences.view.ui_scale
    usable_width = region_width - 60 * ui_scale          # icon and margins
    return max(16, int(usable_width / (7.0 * ui_scale)))


def draw_wrapped_text(layout, context, text: str, *, icon: str = "NONE", alert: bool = False) -> None:
    """Draw an already translated paragraph, wrapped to the panel width."""
    lines = textwrap.wrap(text, width=_characters_per_line(context)) or [""]
    column = layout.column(align=True)
    column.alert = alert
    column.scale_y = 0.85
    for index, line in enumerate(lines):
        if icon == "NONE":
            column.label(text=line, translate=False)
        else:
            column.label(text=line, icon=icon if index == 0 else "BLANK1", translate=False)


def say(template: str, **values) -> str:
    """Translate a text from texts.py and fill in its {placeholders}."""
    text = translate(template)
    return text.format(**values) if values else text


def _level_plan(settings) -> planning.LevelPlan:
    return planning.parse_level_targets(settings.target_quads, settings.extra_levels)


def _guidance_options(settings) -> guidance.GuidanceOptions:
    plan = _level_plan(settings)
    forge = settings.backend == "FORGE"
    return guidance.GuidanceOptions(
        target_quads=settings.target_quads,
        symmetry_axes=tuple(axis for axis, enabled in enumerate((settings.sym_x, settings.sym_y, settings.sym_z)) if enabled),
        symmetry_plane_at_origin=settings.sym_center == "ORIGIN",
        use_materials=settings.use_materials,
        use_face_sets=settings.use_face_sets,
        use_mask=settings.use_mask,
        use_vertex_groups=settings.use_vertex_groups,
        use_proxy=settings.use_proxy and not forge,
        proxy_is_auto=settings.proxy_auto,
        proxy_faces=planning.choose_proxy_faces(plan.largest, settings.proxy_auto, settings.proxy_faces),
        ignored_levels=plan.ignored,
        output_kind="tris" if forge and settings.output_type == "TRIS" else "quads",
        handles_heavy_meshes=forge,
    )


def _describe_active_options(settings) -> str:
    """One line that tells the user what is switched on, even when the panels are collapsed."""
    parts = []
    if settings.use_sharp_angle:
        parts.append(say(texts.LABEL_OPTION_HARD_EDGES, angle=f"{math.degrees(settings.sharp_angle):.0f}"))
    zones = [name for name, enabled in (
        (translate("materials"), settings.use_materials),
        (translate("face sets"), settings.use_face_sets),
        (translate("mask"), settings.use_mask),
        (translate("vertex groups"), settings.use_vertex_groups)) if enabled]
    if zones:
        parts.append(say(texts.LABEL_OPTION_ZONES, zones=", ".join(zones)))
    axes = "".join(letter for letter, enabled in zip("XYZ", (settings.sym_x, settings.sym_y, settings.sym_z)) if enabled)
    if axes:
        parts.append(say(texts.LABEL_OPTION_SYMMETRY, axes=axes))
    plan = _level_plan(settings)
    if plan.has_extra_levels:
        parts.append(say(texts.LABEL_OPTION_LEVELS,
                         levels=", ".join(planning.format_target(t) for t in plan.targets)))
    return say(texts.LABEL_ACTIVE_OPTIONS, options=", ".join(parts) if parts else translate(texts.LABEL_NO_OPTIONS))


def _read_version() -> str:
    """Add-on version, read from blender_manifest.toml (the single source of truth)."""
    try:
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml")
        with open(path, encoding="utf-8") as handle:
            match = re.search(r'^version\s*=\s*"([^"]+)"', handle.read(), re.M)
        return match.group(1) if match else "?"
    except OSError:
        return "?"


ADDON_VERSION = _read_version()


class _QuadForgePanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "QuadForge"


def _prepare_layout(layout) -> None:
    layout.use_property_split = True
    layout.use_property_decorate = False


# --------------------------------------------------------------------------- main panel

class QUADFORGE_MT_target(bpy.types.Menu):
    bl_label = "Target Faces"

    def draw(self, context):
        layout = self.layout
        current = context.scene.quadforge.target_quads
        columns = layout.column_flow(columns=2)
        for value in TARGET_CHOICES:
            columns.operator("quadforge.set_target", text=format_target(value),
                             depress=value == current).value = value


class QUADFORGE_PT_main(_QuadForgePanel, bpy.types.Panel):
    bl_label = f"QuadForge {ADDON_VERSION}"

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge

        messages = guidance.build_guidance(
            guidance.snapshot_from_object(context.active_object), _guidance_options(settings))
        self._draw_advice(layout, context, messages)

        layout.separator()
        layout.label(text=translate(texts.LABEL_SURFACE))
        surface_row = layout.row(align=True)
        surface_row.scale_y = 1.3
        for key, profile in SURFACE_PROFILES.items():
            surface_row.operator("quadforge.set_surface", text=profile.label,
                                 depress=settings.surface_type == key).surface = key

        layout.separator()
        target_row = layout.split(factor=0.4, align=True)
        target_row.label(text="Target Faces")
        target_row.menu("QUADFORGE_MT_target", text=format_target(settings.target_quads))
        layout.prop(settings, "target_quads", text=translate(texts.LABEL_EXACT_NUMBER))
        if settings.target_quads > HEAVY_TARGET:
            layout.label(text=translate(texts.LABEL_HEAVY_TARGET), icon="INFO")

        layout.separator()
        layout.prop(settings, "backend")
        if settings.backend == "FORGE":
            layout.row().prop(settings, "output_type", expand=True)

        remesh_row = layout.row()
        remesh_row.scale_y = 1.6
        remesh_row.enabled = guidance.can_remesh(messages)
        remesh_row.operator("quadforge.remesh", icon="MESH_GRID")
        preview_row = layout.row()
        preview_row.enabled = remesh_row.enabled
        preview_row.operator("quadforge.preview", icon="HIDE_OFF")

        layout.separator()
        draw_wrapped_text(layout, context, _describe_active_options(settings), icon="PREFERENCES")
        draw_wrapped_text(layout, context, translate(texts.HINT_OPTIONAL_PANELS))

    @staticmethod
    def _draw_advice(layout, context, messages) -> None:
        box = layout.box()
        for message in messages:
            draw_wrapped_text(
                box, context, say(message.template, **message.values),
                icon=LEVEL_ICONS[message.level], alert=message.level == guidance.ERROR)


# --------------------------------------------------------------------------- optional sections

class QUADFORGE_PT_hard_edges(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Hard Edges"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.prop(context.scene.quadforge, "use_sharp_angle", text="")

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge

        draw_wrapped_text(layout, context, translate(texts.HELP_HARD_EDGES))
        layout.separator()
        detection = layout.column()
        detection.active = settings.use_sharp_angle
        detection.prop(settings, "sharp_angle")
        detection.prop(settings, "smart_features")
        smart = detection.column()
        smart.active = settings.smart_features
        smart.prop(settings, "min_feature_size")
        smart.prop(settings, "keep_right_angles")
        draw_wrapped_text(detection, context, translate(texts.HELP_SMART_FEATURES))
        detection.prop(settings, "snap_features")
        detection.prop(settings, "soft_bevels")
        layout.separator()
        layout.prop(settings, "use_existing_sharp")
        layout.prop(settings, "use_seams")
        layout.prop(settings, "preserve_boundary")


class QUADFORGE_PT_guides(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Flow Guides"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.scene.quadforge.backend == "FORGE"

    def draw_header(self, context):
        self.layout.prop(context.scene.quadforge, "use_flow_guides", text="")

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge
        draw_wrapped_text(layout, context, translate(texts.HELP_GUIDES))
        layout.separator()
        count = operators.guide_edge_count(context.active_object)
        text = say(texts.LABEL_GUIDE_COUNT, count=count) if count else translate(texts.LABEL_GUIDE_NONE)
        draw_wrapped_text(layout, context, text, icon="INFO")
        column = layout.column()
        column.active = settings.use_flow_guides
        row = column.row(align=True)
        row.operator("quadforge.mark_guides", icon="ADD")
        clear = row.operator("quadforge.mark_guides", text="", icon="REMOVE")
        clear.clear = True
        column.operator("quadforge.clear_guides", icon="X")


class QUADFORGE_PT_zones(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Isolate Zones"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge

        draw_wrapped_text(layout, context, translate(texts.HELP_ZONES))
        layout.separator()
        layout.prop(settings, "use_materials")
        layout.prop(settings, "use_face_sets")
        layout.prop(settings, "use_mask")
        mask_options = layout.column()
        mask_options.active = settings.use_mask
        mask_options.prop(settings, "mask_threshold")
        layout.prop(settings, "use_vertex_groups")
        group_options = layout.column()
        group_options.active = settings.use_vertex_groups
        group_options.prop(settings, "group_cutoff")


class QUADFORGE_PT_symmetry(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Symmetry"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge

        draw_wrapped_text(layout, context, translate(texts.HELP_SYMMETRY))
        layout.separator()
        axes_row = layout.row(align=True, heading="Axes")
        axes_row.prop(settings, "sym_x", toggle=True)
        axes_row.prop(settings, "sym_y", toggle=True)
        axes_row.prop(settings, "sym_z", toggle=True)

        options = layout.column()
        options.active = settings.sym_x or settings.sym_y or settings.sym_z
        options.prop(settings, "sym_center")
        draw_wrapped_text(options, context, translate(texts.HELP_SYMMETRY_PLANE))
        options.separator()
        options.prop(settings, "sym_side")
        draw_wrapped_text(options, context, translate(texts.HELP_SYMMETRY_SIDE))
        options.separator()
        options.prop(settings, "sym_tolerance")
        options.prop(settings, "check_symmetry")


class QUADFORGE_PT_levels(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Extra Levels"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge

        draw_wrapped_text(layout, context, translate(texts.HELP_LEVELS))
        layout.separator()
        layout.prop(settings, "extra_levels")
        layout.operator("quadforge.apply_preset", text=PRESETS["LOD_LADDER"].label).preset = "LOD_LADDER"
        plan = _level_plan(settings)
        levels = ", ".join(planning.format_target(target) for target in plan.targets)
        draw_wrapped_text(layout, context, say(texts.LABEL_LEVELS_PREVIEW, levels=levels), icon="OUTLINER_OB_MESH")


class QUADFORGE_PT_density(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Density"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.scene.quadforge.backend == "FORGE"

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge
        active = context.active_object

        draw_wrapped_text(layout, context, translate(texts.HELP_ENGINE_FORGE))
        layout.separator()
        draw_wrapped_text(layout, context, translate(texts.HELP_DENSITY))
        if active is not None and active.type == "MESH":
            if len(active.vertex_groups):
                layout.prop_search(settings, "density_group", active, "vertex_groups")
            else:
                draw_wrapped_text(layout, context, translate(texts.LABEL_NO_GROUPS), icon="INFO")
        column = layout.column()
        column.active = bool(settings.density_group)
        column.prop(settings, "density_ratio")
        layout.separator()
        box = layout.box()
        box.label(text="Detail by Zone")
        draw_wrapped_text(box, context, translate(texts.HELP_ZONE_DETAIL))
        box.operator("quadforge.refresh_zones", icon="FILE_REFRESH")
        for item in settings.zone_factors:
            box.prop(item, "factor", text=item.name)
        layout.separator()
        draw_wrapped_text(layout, context, translate(texts.HELP_CURVATURE))
        layout.prop(settings, "curvature_detail")


class QUADFORGE_PT_speed(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Speed and Detail"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.scene.quadforge.backend != "FORGE"   # FORGE needs no working copy or polish

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge

        draw_wrapped_text(layout, context, translate(texts.HELP_SPEED))
        layout.separator()
        layout.prop(settings, "use_proxy")
        proxy_options = layout.column()
        proxy_options.active = settings.use_proxy
        proxy_options.prop(settings, "proxy_auto")
        manual_size = proxy_options.column()
        manual_size.active = not settings.proxy_auto
        manual_size.prop(settings, "proxy_faces")

        layout.separator()
        layout.prop(settings, "use_reproject")
        layout.prop(settings, "subdivision_match")
        polish = layout.column()
        polish.active = settings.use_reproject
        polish.prop(settings, "polish_passes")
        draw_wrapped_text(polish, context, translate(texts.HELP_POLISH))


# --------------------------------------------------------------------------- result and help

class QUADFORGE_PT_last_result(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Last Result"
    bl_parent_id = "QUADFORGE_PT_main"

    @classmethod
    def poll(cls, context):
        return context.scene.quadforge.last_has_report

    def draw(self, context):
        layout = self.layout
        settings = context.scene.quadforge

        quad_percent = 100.0 * settings.last_quads / settings.last_faces if settings.last_faces else 0.0
        column = layout.column(align=True)
        column.label(text=say(texts.LABEL_RESULT_FACES, faces=f"{settings.last_faces:,}"),
                     icon="MESH_GRID", translate=False)
        triangles_only = settings.last_faces > 0 and settings.last_tris == settings.last_faces
        if triangles_only:
            column.label(text=say(texts.LABEL_RESULT_TRI_ONLY, tris=f"{settings.last_tris:,}"),
                         icon="CHECKMARK", translate=False)
        else:
            column.label(text=say(texts.LABEL_RESULT_QUADS, quads=f"{settings.last_quads:,}",
                                  percent=f"{quad_percent:.1f}"), icon="CHECKMARK", translate=False)
            column.label(text=say(texts.LABEL_RESULT_TRIS, tris=f"{settings.last_tris:,}",
                                  ngons=f"{settings.last_ngons:,}"), icon="BLANK1", translate=False)
            column.label(text=say(texts.LABEL_RESULT_POLES, poles=f"{settings.last_poles:,}"),
                         icon="BLANK1", translate=False)
        column.label(text=say(texts.LABEL_RESULT_HARD_EDGES, count=f"{settings.last_hard_edges:,}"),
                     icon="BLANK1", translate=False)
        if settings.last_corners_snapped:
            column.label(text=say(texts.LABEL_RESULT_CORNERS, count=f"{settings.last_corners_snapped:,}"),
                         icon="BLANK1", translate=False)
        column.label(text=say(texts.LABEL_RESULT_TIME, seconds=f"{settings.last_seconds:.1f}"),
                     icon="TIME", translate=False)
        if settings.last_peak_memory_gb > 0.0:
            column.label(text=say(texts.LABEL_RESULT_MEMORY, memory=f"{settings.last_peak_memory_gb:.1f}"),
                         icon="BLANK1", translate=False)

        self._draw_levels(layout, context, settings)
        layout.separator()
        if not triangles_only:
            draw_wrapped_text(layout, context, translate(texts.HELP_RESULT), icon="INFO")
        for warning in filter(None, settings.last_warnings.split("\n")):
            draw_wrapped_text(layout, context, warning, icon="ERROR")

    @staticmethod
    def _draw_levels(layout, context, settings) -> None:
        """One line per level when several were built (format: target,faces,quads,poles,seconds)."""
        rows = [line.split(",") for line in settings.last_levels_text.split("\n") if line]
        if len(rows) < 2:
            return
        layout.separator()
        layout.label(text=texts.LABEL_RESULT_LEVELS, icon="OUTLINER_OB_MESH")
        for target, faces, quads, poles, seconds in rows:
            percent = 100.0 * int(quads) / int(faces) if int(faces) else 0.0
            draw_wrapped_text(layout, context, say(
                texts.LABEL_RESULT_LEVEL_LINE, name=planning.format_target(int(target)),
                quads=f"{int(quads):,}", percent=f"{percent:.1f}", poles=poles, seconds=seconds))


class QUADFORGE_PT_how_to_use(_QuadForgePanel, bpy.types.Panel):
    bl_label = "How to Use"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    STEPS = (texts.GUIDE_STEP_1, texts.GUIDE_STEP_2, texts.GUIDE_STEP_3, texts.GUIDE_STEP_4,
             texts.GUIDE_STEP_5, texts.GUIDE_STEP_6, texts.GUIDE_STEP_7, texts.GUIDE_STEP_8)
    TIPS = (texts.GUIDE_TIP_1, texts.GUIDE_TIP_2, texts.GUIDE_TIP_3, texts.GUIDE_TIP_4, texts.GUIDE_TIP_5)

    def draw(self, context):
        layout = self.layout
        layout.label(text=texts.GUIDE_TITLE_STEPS, icon="PLAY")
        for step in self.STEPS:
            draw_wrapped_text(layout, context, translate(step))
            layout.separator(factor=0.4)

        layout.separator()
        layout.label(text=texts.GUIDE_TITLE_TIPS, icon="QUESTION")
        for tip in self.TIPS:
            draw_wrapped_text(layout, context, translate(tip), icon="DOT")
            layout.separator(factor=0.4)


class QUADFORGE_PT_advanced(_QuadForgePanel, bpy.types.Panel):
    bl_label = "Advanced"
    bl_parent_id = "QUADFORGE_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        _prepare_layout(layout)
        settings = context.scene.quadforge
        layout.prop(settings, "shading")
        layout.prop(settings, "clean_input")
        layout.prop(settings, "hide_original")
        layout.prop(settings, "transfer_uvs")
        layout.prop(settings, "transfer_zones")
        if settings.backend != "FORGE":
            layout.prop(settings, "seed")
        layout.separator()
        layout.operator("quadforge.reset_settings", icon="LOOP_BACK")
