"""Buttons of the panel: Remesh, Apply Preset, Reset Settings."""
import math
import time

import bmesh
import bpy
import numpy as np
from bpy.props import BoolProperty, StringProperty

from . import guidance, texts
from . import properties as properties_module
from .core import mesh_io as mio
from .core import pipeline
from .presets import PRESETS

BYTES_PER_GB_IN_MB = 1024.0

PREVIEW_FACES = 3000
PREVIEW_TAG = "quadforge_preview_of"


def remove_previews(original_name: str) -> int:
    """Delete the preview objects (and their meshes) made for ``original_name``. Returns how many were removed."""
    removed = 0
    for obj in list(bpy.data.objects):
        if obj.get(PREVIEW_TAG) == original_name:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
            removed += 1
    return removed


def _store_last_report(settings, result: pipeline.RemeshResult) -> None:
    """Keep the numbers of the last run so the 'Last Result' panel can show them."""
    first = result.levels[0]
    stats = first.stats
    settings.last_has_report = True
    settings.last_faces = stats["faces"]
    settings.last_quads = stats["quads"]
    settings.last_tris = stats["tris"]
    settings.last_ngons = stats["ngons"]
    settings.last_poles = stats["poles_3"] + stats["poles_5plus"]
    settings.last_hard_edges = first.hard_edge_count
    settings.last_seconds = float(result.profile.get("total_seconds", 0.0))
    peak_mb = result.profile.get("peak_memory_mb")
    settings.last_peak_memory_gb = (peak_mb / BYTES_PER_GB_IN_MB) if peak_mb else 0.0
    # One line per level: "target,faces,quads,poles,seconds" (the panel formats it).
    settings.last_levels_text = "\n".join(
        f"{level.target_quads},{level.stats['faces']},{level.stats['quads']},"
        f"{level.stats['poles_3'] + level.stats['poles_5plus']},{level.seconds:.2f}"
        for level in result.levels)
    settings.last_warnings = "\n".join(result.warnings)


def _set_status_text(context, text) -> None:
    """Show (or clear, with None) a message in Blender's status bar. Cosmetic: never raises."""
    try:
        context.workspace.status_text_set(text)
    except (AttributeError, RuntimeError):
        pass


def _report_result(operator, result: pipeline.RemeshResult) -> None:
    stats = result.stats
    operator.report(
        {"INFO"},
        f"{len(result.levels)} level(s) in {result.profile.get('total_seconds', 0):.1f} s. "
        f"First level: {stats['faces']} faces ({stats['quads']} quads, {stats['tris']} tris, "
        f"{stats['ngons']} n-gons), {result.hard_edge_count} hard edges kept")
    for warning in result.warnings:
        operator.report({"WARNING"}, warning)


class QUADFORGE_OT_remesh(bpy.types.Operator):
    """Remesh the active mesh to clean quads or triangles"""
    bl_idname = "quadforge.remesh"
    bl_label = "Remesh"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return active is not None and active.type == "MESH"

    interactive: BoolProperty(options={"HIDDEN", "SKIP_SAVE"}, default=False)

    def invoke(self, context, event):
        """Panel button: ask first on very dense meshes, then run the cancellable (modal) version."""
        self.interactive = True
        snapshot = guidance.snapshot_from_object(context.active_object)
        scene_settings = context.scene.quadforge
        if not guidance.needs_confirmation(snapshot.face_count):
            return self.execute(context)
        window_manager = context.window_manager
        try:
            return window_manager.invoke_confirm(
                self, event, title=texts.CONFIRM_TITLE,
                message=texts.CONFIRM_MESSAGE, confirm_text=texts.CONFIRM_BUTTON)
        except TypeError:  # older Blender builds do not accept the text arguments
            return window_manager.invoke_confirm(self, event)

    def execute(self, context):
        if self.interactive:
            bpy.ops.quadforge.remesh_modal("INVOKE_DEFAULT")   # live progress, Esc cancels
            return {"FINISHED"}
        return self._run_blocking(context)

    def _run_blocking(self, context):
        """Scripts and tests: run everything in one go (not cancellable)."""
        original = context.active_object
        if original.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        scene_settings = context.scene.quadforge
        window_manager = context.window_manager
        window_manager.progress_begin(0, 100)

        def show_progress(percent: int, message: str) -> None:
            window_manager.progress_update(percent)
            _set_status_text(context, f"QuadForge: {message}")

        try:
            result = pipeline.run(context, original, scene_settings.to_settings(), show_progress)
        except pipeline.PipelineError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception as error:  # noqa: BLE001 - never take Blender down with us
            self.report({"ERROR"}, f"Unexpected error: {error!r}")
            return {"CANCELLED"}
        finally:
            window_manager.progress_end()
            _set_status_text(context, None)

        remove_previews(original.name)
        _store_last_report(scene_settings, result)
        _report_result(self, result)
        return {"FINISHED"}


class QUADFORGE_OT_remesh_modal(bpy.types.Operator):
    """Remesh with a live progress bar; press Esc to cancel (the original is never touched)"""
    bl_idname = "quadforge.remesh_modal"
    bl_label = "Remesh"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    TICK_SECONDS = 0.03        # how long one timer tick may work before Blender gets control back

    _generator = None
    _timer = None
    _original_name = ""

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return active is not None and active.type == "MESH"

    def invoke(self, context, event):
        original = context.active_object
        self._original_name = original.name
        if original.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        scene_settings = context.scene.quadforge
        window_manager = context.window_manager
        window_manager.progress_begin(0, 100)

        def show_progress(percent: int, message: str) -> None:
            window_manager.progress_update(percent)
            _set_status_text(context, f"QuadForge: {message}  (Esc cancels)")

        self._generator = pipeline.run_steps(context, original, scene_settings.to_settings(), show_progress)
        self._timer = window_manager.event_timer_add(0.01, window=context.window)
        window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _stop(self, context):
        window_manager = context.window_manager
        if self._timer is not None:
            window_manager.event_timer_remove(self._timer)
            self._timer = None
        window_manager.progress_end()
        _set_status_text(context, None)

    def modal(self, context, event):
        if event.type == "ESC":
            self._generator.close()          # kills the engine process and deletes the temporary data
            self._generator = None
            self._stop(context)
            self.report({"WARNING"}, "QuadForge: cancelled. Your original mesh was not changed.")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        started = time.monotonic()
        try:
            while time.monotonic() - started < self.TICK_SECONDS:
                next(self._generator)
        except StopIteration as done:
            self._generator = None
            self._stop(context)
            remove_previews(self._original_name)
            _store_last_report(context.scene.quadforge, done.value)
            _report_result(self, done.value)
            return {"FINISHED"}
        except pipeline.PipelineError as error:
            self._generator = None
            self._stop(context)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception as error:  # noqa: BLE001 - never take Blender down with us
            self._generator = None
            self._stop(context)
            self.report({"ERROR"}, f"Unexpected error: {error!r}")
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        if self._generator is not None:
            self._generator.close()
            self._generator = None
        self._stop(context)


class QUADFORGE_OT_preview(bpy.types.Operator):
    """Make a quick low-resolution version (about 3,000 faces) with your current settings, to check the flow before the full run"""
    bl_idname = "quadforge.preview"
    bl_label = "Quick Preview"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return active is not None and active.type == "MESH" and active.get(PREVIEW_TAG) is None

    def execute(self, context):
        original = context.active_object
        if original.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        scene_settings = context.scene.quadforge
        settings = scene_settings.to_settings()
        settings.target_quads = min(settings.target_quads, PREVIEW_FACES)
        settings.extra_levels = ""
        settings.hide_original = False
        settings.transfer_uvs = False
        settings.transfer_zones = False
        remove_previews(original.name)
        started = time.perf_counter()
        try:
            result = pipeline.run(context, original, settings)
        except pipeline.PipelineError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        except Exception as error:  # noqa: BLE001 - never take Blender down with us
            self.report({"ERROR"}, f"Unexpected error: {error!r}")
            return {"CANCELLED"}
        preview = result.levels[0].object
        preview.name = f"{original.name}_preview"
        preview.data.name = preview.name
        preview[PREVIEW_TAG] = original.name
        # the original stays the active, selected object so the next click on Remesh works on it
        for obj in context.selected_objects:
            obj.select_set(False)
        original.select_set(True)
        context.view_layer.objects.active = original
        self.report({"INFO"}, f"Preview with {len(preview.data.polygons)} faces in "
                              f"{time.perf_counter() - started:.1f} s. It is deleted when you run the full Remesh.")
        return {"FINISHED"}


class QUADFORGE_OT_refresh_zones(bpy.types.Operator):
    """List the zones of the active mesh (from the zone options that are on) so each can get its own detail level"""
    bl_idname = "quadforge.refresh_zones"
    bl_label = "Refresh Zones"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return active is not None and active.type == "MESH"

    def execute(self, context):
        original = context.active_object
        scene_settings = context.scene.quadforge
        settings = scene_settings.to_settings()
        mesh = original.data
        warnings: list = []
        labels = pipeline._compute_zone_labels(mesh, original, settings, warnings)
        if labels is None:
            self.report({"WARNING"}, "Turn on at least one option in Isolate Zones (materials, Face Sets, mask or vertex groups) first.")
            return {"CANCELLED"}
        names = pipeline._zone_names(mesh, original, settings, labels)
        kept = {item.name: item.factor for item in scene_settings.zone_factors}
        scene_settings.zone_factors.clear()
        for label in sorted(names):
            item = scene_settings.zone_factors.add()
            item.name = names[label]
            item.factor = kept.get(item.name, 1.0)
        self.report({"INFO"}, f"{len(names)} zones listed.")
        return {"FINISHED"}


class QUADFORGE_OT_detect_bands(bpy.types.Operator):
    """Find the chamfers and bevels of the active mesh (CAD-style) and colour them: orange = chamfer, blue = bevel"""
    bl_idname = "quadforge.detect_bands"
    bl_label = "Detect Chamfers and Bevels"
    bl_options = {"REGISTER", "UNDO"}

    max_width_mm: bpy.props.FloatProperty(
        name="Widest Band (mm)", default=0.0, min=0.0, soft_max=50.0,
        description="Widest chamfer or bevel to look for, in millimetres. 0 = automatic (4% of the part size)")

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return active is not None and active.type == "MESH"

    def execute(self, context):
        from .core import bands as bands_core
        obj = context.active_object
        mesh = obj.data
        if obj.mode == "EDIT":
            bpy.ops.object.mode_set(mode="OBJECT")
        dims = obj.dimensions
        size_max = max(dims.x, dims.y, dims.z)
        mm_per_unit, how = bands_core.guess_mm_per_unit(context.scene, size_max)
        width_units = self.max_width_mm / mm_per_unit if self.max_width_mm > 0 else 0.0
        try:
            face_band, bands, report = bands_core.detect(obj, width_units)
        except Exception as error:   # noqa: BLE001 - shown to the person
            self.report({"ERROR"}, f"Detection failed: {error}")
            return {"CANCELLED"}
        # colours
        mio.write_int_face_attribute(mesh, "qf_band", face_band + 1)
        kinds = np.array([0] + [1 if b["kind"] == "chamfer" else 2 for b in bands])
        face_kind = kinds[face_band + 1]
        palette = np.array([[0.62, 0.62, 0.64, 1], [1.0, 0.42, 0.05, 1], [0.1, 0.45, 1.0, 1]], dtype=np.float32)
        totals = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get("loop_total", totals)
        loop_face = np.repeat(np.arange(len(mesh.polygons)), totals)
        colors = palette[face_kind[loop_face]]
        name = "QuadForge Bands"
        attribute = mesh.color_attributes.get(name) or mesh.color_attributes.new(name, "FLOAT_COLOR", "CORNER")
        attribute.data.foreach_set("color", colors.reshape(-1))
        mesh.color_attributes.active_color = attribute
        mesh.color_attributes.render_color_index = list(mesh.color_attributes).index(attribute)
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces[0].shading.type = "SOLID"
                area.spaces[0].shading.color_type = "VERTEX"
        # text report
        text = bpy.data.texts.get("QuadForge Bands") or bpy.data.texts.new("QuadForge Bands")
        text.clear()
        lines = [f"Part size: {dims.x * mm_per_unit:.2f} x {dims.y * mm_per_unit:.2f} x {dims.z * mm_per_unit:.2f} mm  ({how})",
                 f"Bands found: {len(bands)}   (widest searched: {(width_units or 0.112 * float(np.linalg.norm(np.array(dims)))) * mm_per_unit:.2f} mm)", "",
                 "  #  kind     side     width_mm  length_mm  turn   radius_mm  faces"]
        for i, b in enumerate(bands[:200]):
            lines.append(f"{i:3d}  {b['kind']:<8} {'convex' if b['convex'] else 'concave':<8} {b['width'] * mm_per_unit:8.2f} {b['length'] * mm_per_unit:10.2f} "
                         f"{b['turn']:5.0f}  {b['radius'] * mm_per_unit:9.2f}  {b['faces']}")
        text.write("\n".join(lines))
        ch = sum(1 for b in bands if b["kind"] == "chamfer")
        self.report({"INFO"}, f"{ch} chamfers, {len(bands) - ch} bevels found (orange / blue). Details in the text 'QuadForge Bands'.")
        return {"FINISHED"}


def guide_edge_count(obj) -> int:
    """How many edges of ``obj`` are marked as flow guides (works in Edit Mode too)."""
    if obj is None or obj.type != "MESH":
        return 0
    try:
        if obj.mode == "EDIT":
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.edges.layers.bool.get(pipeline.GUIDE_ATTRIBUTE)
            return 0 if layer is None else sum(1 for edge in bm.edges if edge[layer])
        attribute = obj.data.attributes.get(pipeline.GUIDE_ATTRIBUTE)
        if attribute is None or attribute.domain != "EDGE":
            return 0
        return int(sum(1 for item in attribute.data if item.value))
    except (AttributeError, RuntimeError, KeyError):
        return 0


class QUADFORGE_OT_mark_guides(bpy.types.Operator):
    """Mark the selected edges as flow guides: the quad rows will try to run along them"""
    bl_idname = "quadforge.mark_guides"
    bl_label = "Mark Flow Guide"
    bl_options = {"INTERNAL", "UNDO"}

    clear: BoolProperty(options={"HIDDEN", "SKIP_SAVE"}, default=False)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH" and obj.mode == "EDIT"

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        layer = bm.edges.layers.bool.get(pipeline.GUIDE_ATTRIBUTE) or bm.edges.layers.bool.new(pipeline.GUIDE_ATTRIBUTE)
        changed = 0
        for edge in bm.edges:
            if edge.select:
                edge[layer] = not self.clear
                changed += 1
        bmesh.update_edit_mesh(obj.data)
        if changed == 0:
            self.report({"WARNING"}, "Select some edges first.")
            return {"CANCELLED"}
        return {"FINISHED"}


class QUADFORGE_OT_clear_guides(bpy.types.Operator):
    """Remove all flow guides from the active object"""
    bl_idname = "quadforge.clear_guides"
    bl_label = "Clear Flow Guides"
    bl_options = {"INTERNAL", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "MESH"

    def execute(self, context):
        obj = context.active_object
        if obj.mode == "EDIT":
            bm = bmesh.from_edit_mesh(obj.data)
            layer = bm.edges.layers.bool.get(pipeline.GUIDE_ATTRIBUTE)
            if layer is not None:
                bm.edges.layers.bool.remove(layer)
            bmesh.update_edit_mesh(obj.data)
        else:
            attribute = obj.data.attributes.get(pipeline.GUIDE_ATTRIBUTE)
            if attribute is not None:
                obj.data.attributes.remove(attribute)
        return {"FINISHED"}


class QUADFORGE_OT_apply_preset(bpy.types.Operator):
    """Set the target count and hard-edge angle for a kind of asset"""
    bl_idname = "quadforge.apply_preset"
    bl_label = "Apply Preset"
    bl_options = {"INTERNAL", "UNDO"}

    preset: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        preset = PRESETS.get(properties.preset)
        return preset.description if preset else cls.__doc__

    def execute(self, context):
        preset = PRESETS.get(self.preset)
        if preset is None:
            return {"CANCELLED"}
        settings = context.scene.quadforge
        settings.target_quads = preset.target_quads
        settings.sharp_angle = math.radians(preset.sharp_angle_degrees)
        settings.curvature_detail = preset.curvature_detail
        if preset.extra_levels is not None:
            settings.extra_levels = preset.extra_levels
        return {"FINISHED"}


class QUADFORGE_OT_set_surface(bpy.types.Operator):
    """Configure the add-on for a kind of surface"""
    bl_idname = "quadforge.set_surface"
    bl_label = "Surface Type"
    bl_options = {"INTERNAL", "UNDO"}

    surface: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        from .presets import SURFACE_PROFILES
        profile = SURFACE_PROFILES.get(properties.surface)
        return profile.description if profile else cls.__doc__

    def execute(self, context):
        return {"FINISHED"} if properties_module.apply_surface_profile(context.scene.quadforge, self.surface) else {"CANCELLED"}


class QUADFORGE_OT_set_target(bpy.types.Operator):
    """Set the number of faces you want in the result"""
    bl_idname = "quadforge.set_target"
    bl_label = "Target Faces"
    bl_options = {"INTERNAL", "UNDO"}

    value: bpy.props.IntProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, properties):
        from .presets import HEAVY_TARGET
        text = f"Aim for about {properties.value:,} faces"
        return text + ". Slow and heavy: needs a lot of time and memory" if properties.value > HEAVY_TARGET else text

    def execute(self, context):
        context.scene.quadforge.target_quads = self.value
        return {"FINISHED"}


class QUADFORGE_OT_reset_settings(bpy.types.Operator):
    """Put every QuadForge setting back to its default value"""
    bl_idname = "quadforge.reset_settings"
    bl_label = "Reset Settings"
    bl_options = {"INTERNAL", "UNDO"}

    def execute(self, context):
        settings = context.scene.quadforge
        for prop in settings.bl_rna.properties:
            if prop.identifier in {"rna_type", "name"} or prop.identifier.startswith("last_"):
                continue
            settings.property_unset(prop.identifier)
        return {"FINISHED"}
