"""Settings stored in the scene (``scene.quadforge``) and the last result shown in the panel."""
import math

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

from .core.pipeline import RemeshSettings

_applying_profile = False     # set while a surface button writes its values, so they do not unmark the button


def _unmark_surface(self, _context):
    """Changing one of the values a surface button sets by hand means the button no longer describes the settings."""
    if not _applying_profile and self.surface_type != "NONE":
        self.surface_type = "NONE"


def _recolor_surfaces(context):
    """Repaint the active mesh from its stored surface attributes when the colour mode changes."""
    try:
        from .operators import apply_surface_colors
        obj = context.active_object
        if obj is not None and obj.type == "MESH" and obj.data.attributes.get("qf_surface") is not None and obj.mode == "OBJECT":
            apply_surface_colors(obj.data, context.scene.quadforge.surface_view)
    except Exception:   # noqa: BLE001 - a colour refresh must never break the UI
        pass


def apply_surface_profile(settings, key: str) -> bool:
    """Write the values of a surface profile into ``settings`` and mark its button. Returns False for an unknown key."""
    global _applying_profile
    from .presets import SURFACE_PROFILES
    profile = SURFACE_PROFILES.get(key)
    if profile is None:
        return False
    _applying_profile = True
    try:
        for name, value in profile.values.items():
            if name == "sharp_angle_degrees":
                settings.sharp_angle = math.radians(value)
            else:
                setattr(settings, name, value)
        settings.surface_type = key
    finally:
        _applying_profile = False
    return True


class QuadForgeZoneFactor(bpy.types.PropertyGroup):
    """Detail multiplier of one zone (listed by Refresh Zones in the Density panel)."""

    name: StringProperty(name="Zone", default="")
    factor: FloatProperty(
        name="Detail",
        description="How much finer (above 1) or coarser (below 1) the faces of this zone are compared with the rest. "
                    "2 gives roughly 1.7 times more faces in the zone (the engine changes the density gradually to keep clean quads)",
        default=1.0, min=0.25, max=4.0, precision=2,
    )


class QuadForgeProperties(bpy.types.PropertyGroup):

    surface_type: EnumProperty(
        name="Surface Type",
        description="Which surface button was used last (changing a value it sets unmarks it)",
        items=[("NONE", "None", ""), ("HARD", "Hard Surface", ""), ("ORGANIC", "Soft Surface / Organic", "")],
        default="NONE",
    )

    # ---------------------------------------------------------------- engine and output
    output_type: EnumProperty(
        name="Output",
        description="What kind of faces the result is made of",
        items=[
            ("QUADS", "Quads", "Clean quad topology (for animation, subdivision, sculpting)"),
            ("TRIS", "Triangles", "Even, well-shaped triangles (for games, printing, simulation)"),
        ],
        default="QUADS",
    )
    density_group: StringProperty(
        name="Density Group",
        description="Vertex group that asks for smaller faces where its weight is high (FORGE only). "
                    "Paint it in Weight Paint mode on the areas that need more detail",
        default="",
    )
    density_ratio: FloatProperty(
        name="Detail Ratio",
        description="How many times shorter the edges are where the group weight is 1 "
                    "(4 means four times finer, so about 16 times more faces there)",
        default=4.0, min=1.0, max=16.0, precision=1,
    )

    zone_factors: CollectionProperty(type=QuadForgeZoneFactor)

    use_flow_guides: BoolProperty(
        name="Use Flow Guides",
        description="Edges marked with Mark Flow Guide pull the quad rows along them. A soft hint: the engine "
                    "follows the guide where the surface allows it (FORGE quads only)",
        default=True,
    )

    curvature_detail: EnumProperty(
        update=_unmark_surface,
        name="Detail on Curves",
        description="Smaller faces where the surface bends sharply (eyelids, lips, nostrils, ear rims). "
                    "Costs a few more irregular vertices elsewhere, so it is off by default (FORGE quads only)",
        items=[
            ("OFF", "Off", "Even faces everywhere"),
            ("LOW", "Low", "A little more detail on sharp curves"),
            ("MEDIUM", "Medium", "Recommended for faces and characters"),
            ("HIGH", "High", "Strong detail on every sharp curve"),
        ],
        default="OFF",
    )

    # ---------------------------------------------------------------- 1. target
    target_quads: IntProperty(
        name="Target Faces",
        description="Approximate number of faces (quads or triangles) you want in the result. "
                    "The final count can differ by a few percent",
        default=4000, min=16, soft_max=100_000, max=5_000_000,
    )
    extra_levels: StringProperty(
        name="Extra Levels",
        description="Lower face counts to build as well, separated by commas, for example 15K, 10K, 5K, 4K. "
                    "Each level is built from your source mesh",
        default="",
    )

    # ---------------------------------------------------------------- subdivision
    subdivision_match: BoolProperty(
        name="Subdivision Match",
        description="Move the vertices so that, once you add a Subdivision Surface modifier, the smooth result lies on "
                    "your original surface instead of shrinking inside it. Made for smooth, organic shapes; quads only. Sharp creases "
                    "stay where they are, and no point moves more than a third of an edge",
        default=False,
    )


    # ---------------------------------------------------------------- hard edges
    use_sharp_angle: BoolProperty(
        update=_unmark_surface,
        name="Keep Hard Edges",
        description="Detect sharp corners and keep them as clean edge loops",
        default=True,
    )
    sharp_angle: FloatProperty(
        update=_unmark_surface,
        name="Angle",
        description="Edges where the surface bends more than this angle are kept. "
                    "Lower keeps more edges, higher keeps fewer",
        default=math.radians(35.0), min=math.radians(1.0), max=math.radians(179.0),
        subtype="ANGLE", unit="ROTATION",
    )
    use_existing_sharp: BoolProperty(
        name="Edges Marked Sharp",
        description="Also keep the edges you already marked as sharp in Blender",
        default=True,
    )
    soft_bevels: BoolProperty(
        name="Rounded Bevels",
        description="Keep the profile of rounded bevels and thin chamfer strips: the quad rows follow both sides of them "
                    "and the faces get smaller there. Turn off if a smooth surface gets too many small faces",
        default=True,
    )
    use_surface_analysis: BoolProperty(
        name="Use Surface Analysis (experimental)",
        description="Analyse the whole model first (planes, cylinders, spheres, chamfers, bevels) and keep the borders "
                    "between surfaces and both sides of every chamfer or bevel as hard edges. Made for CAD and hard "
                    "surface models; the Hard Surface button turns it on",
        default=False,
    )
    surface_band_sides: BoolProperty(
        name="Keep Chamfer / Bevel Sides",
        description="With Use Surface Analysis: also keep the two long sides of every chamfer and bevel as hard edges. "
                    "Turn it off to keep only the borders between surfaces (fewer constraints, fewer triangles)",
        default=True,
    )
    surface_view: EnumProperty(
        name="Colours",
        description="How Analyze Surfaces paints the model",
        items=(("SURFACE", "By Surface", "One colour per surface: every wall of the part is a different colour"),
               ("TYPE", "By Type", "Colour by kind: plane, cylinder, sphere, cone, revolution, free-form, chamfer (orange), bevel (blue)")),
        default="TYPE",
        update=lambda self, context: _recolor_surfaces(context),
    )
    surface_select_kind: EnumProperty(
        name="Kind",
        description="Kind of surface to select",
        items=(("plane", "Planes", ""), ("cylinder", "Cylinders", ""), ("sphere", "Spheres", ""), ("cone", "Cones", ""),
               ("revolution", "Revolution", "Surfaces of revolution such as the wall of a spool"),
               ("freeform", "Free-form", ""), ("chamfer", "Chamfers", ""), ("bevel", "Bevels", "")),
        default="chamfer",
    )
    use_seams: BoolProperty(
        name="UV Seams",
        description="Also keep UV seams as hard edges",
        default=True,
    )
    smart_features: BoolProperty(
        update=_unmark_surface,
        name="Smart Detection",
        description="Look at whole chains of sharp edges: ignore tiny bumps, keep right-angle corners",
        default=True,
    )
    min_feature_size: FloatProperty(
        name="Minimum Feature Size",
        description="Chains of sharp edges shorter than this many quad lengths are treated as noise and ignored. "
                    "Lower keeps small details, higher keeps only long creases",
        default=2.0, min=0.0, max=10.0, precision=1,
    )
    keep_right_angles: BoolProperty(
        update=_unmark_surface,
        name="Keep Right Angles",
        description="Keep short runs of 90 degree corners (pegs, boxes, rims) even if they are small",
        default=True,
    )
    preserve_boundary: BoolProperty(
        name="Keep Open Borders",
        description="Keep the outline of open meshes (holes and cut edges)",
        default=True,
    )

    # ---------------------------------------------------------------- isolate zones
    use_materials: BoolProperty(
        name="By Material",
        description="Keep zones that use different materials separated by clean edge loops",
        default=False,
    )
    use_face_sets: BoolProperty(
        name="By Face Set",
        description="Keep sculpt Face Sets separated by clean edge loops",
        default=False,
    )
    use_mask: BoolProperty(
        name="By Sculpt Mask",
        description="Keep the masked area separated from the rest by a clean edge loop",
        default=False,
    )
    use_vertex_groups: BoolProperty(
        name="By Vertex Group",
        description="Keep areas painted in different vertex groups separated by clean edge loops "
                    "(each face goes with the group that has the most weight on it)",
        default=False,
    )
    group_cutoff: FloatProperty(
        name="Group Cutoff",
        description="Weight a face needs in a vertex group to belong to it",
        default=0.5, min=0.01, max=0.99, subtype="FACTOR",
    )
    mask_threshold: FloatProperty(
        name="Mask Cutoff",
        description="Mask value above which a face counts as masked",
        default=0.5, min=0.01, max=0.99, subtype="FACTOR",
    )

    # ---------------------------------------------------------------- symmetry
    sym_x: BoolProperty(name="X", description="Mirror on the local X axis", default=False)
    sym_y: BoolProperty(name="Y", description="Mirror on the local Y axis", default=False)
    sym_z: BoolProperty(name="Z", description="Mirror on the local Z axis", default=False)
    sym_side: EnumProperty(
        name="Keep Side",
        description="Which half of the model is remeshed and mirrored",
        items=[
            ("AUTO", "Auto", "Keep the half with more surface area"),
            ("POSITIVE", "Positive", "Keep the positive side of the axis"),
            ("NEGATIVE", "Negative", "Keep the negative side of the axis"),
        ],
        default="AUTO",
    )
    sym_center: EnumProperty(
        name="Plane At",
        description="Where the mirror plane sits",
        items=[
            ("ORIGIN", "Object Origin", "The plane passes through the object origin"),
            ("BBOX", "Bounding Box Centre", "The plane passes through the middle of the model"),
        ],
        default="ORIGIN",
    )
    sym_tolerance: FloatProperty(
        name="Weld Distance",
        description="How close to the plane counts as 'on the plane', relative to the model size",
        default=1e-4, min=1e-6, max=1e-2, precision=5,
    )
    check_symmetry: BoolProperty(
        name="Warn If Not Symmetric",
        description="Check the model and warn if it is not really symmetric",
        default=True,
    )

    # ---------------------------------------------------------------- advanced
    clean_input: BoolProperty(
        name="Clean Mesh First",
        description="Merge duplicate vertices and remove loose geometry before remeshing",
        default=True,
    )
    shading: EnumProperty(
        name="Shading",
        description="How the result is shaded",
        items=[
            ("AUTO", "Smooth with Sharp Edges", "Smooth surfaces, crisp corners (hard edges are marked sharp)"),
            ("SMOOTH", "Smooth", "Everything smooth"),
            ("FLAT", "Flat", "Every face flat"),
        ],
        default="AUTO",
    )
    hide_original: BoolProperty(
        name="Hide Original",
        description="Hide the original object after remeshing (it is never deleted)",
        default=True,
    )
    transfer_uvs: BoolProperty(
        name="Copy UV Maps",
        description="Copy the UV maps of the original onto the result. Faces next to an old UV seam are only approximate; bake from the original or unwrap again for final texturing",
        default=True,
    )
    transfer_zones: BoolProperty(
        name="Copy Face Sets and Mask",
        description="Copy the sculpt Face Sets (polygroups) and the sculpt mask of the original onto the result, so it goes straight back into Sculpt Mode with the same groups",
        default=True,
    )

    # ---------------------------------------------------------------- last result (read by the UI)
    last_has_report: BoolProperty(default=False, options={"SKIP_SAVE"})
    last_faces: IntProperty(default=0, options={"SKIP_SAVE"})
    last_quads: IntProperty(default=0, options={"SKIP_SAVE"})
    last_tris: IntProperty(default=0, options={"SKIP_SAVE"})
    last_ngons: IntProperty(default=0, options={"SKIP_SAVE"})
    last_poles: IntProperty(default=0, options={"SKIP_SAVE"})
    last_hard_edges: IntProperty(default=0, options={"SKIP_SAVE"})
    last_seconds: FloatProperty(default=0.0, options={"SKIP_SAVE"})
    last_peak_memory_gb: FloatProperty(default=0.0, options={"SKIP_SAVE"})   # 0 = unknown
    last_levels_text: StringProperty(default="", options={"SKIP_SAVE"})      # one line per level
    last_warnings: StringProperty(default="", options={"SKIP_SAVE"})

    def to_settings(self) -> RemeshSettings:
        """Copy the UI values into a plain object the pipeline can use (no Blender types)."""
        return RemeshSettings(
            target_quads=self.target_quads,
            extra_levels=self.extra_levels,
            output=self.output_type,
            density_group=self.density_group,
            density_ratio=self.density_ratio,
            zone_factors={item.name: item.factor for item in self.zone_factors if abs(item.factor - 1.0) > 1e-6},
            curvature_detail=self.curvature_detail,
            use_flow_guides=self.use_flow_guides,
            subdivision_match=self.subdivision_match,
            use_sharp_angle=self.use_sharp_angle,
            sharp_angle_deg=math.degrees(self.sharp_angle),
            use_existing_sharp=self.use_existing_sharp,
            soft_bevels=self.soft_bevels,
            use_surface_analysis=self.use_surface_analysis,
            surface_band_sides=self.surface_band_sides,
            use_seams=self.use_seams,
            smart_features=self.smart_features,
            min_feature_size=self.min_feature_size,
            keep_right_angles=self.keep_right_angles,
            shading=self.shading,
            preserve_boundary=self.preserve_boundary,
            use_materials=self.use_materials,
            use_face_sets=self.use_face_sets,
            use_mask=self.use_mask,
            mask_threshold=self.mask_threshold,
            use_vertex_groups=self.use_vertex_groups,
            group_cutoff=self.group_cutoff,
            sym_x=self.sym_x, sym_y=self.sym_y, sym_z=self.sym_z,
            sym_side=self.sym_side, sym_center=self.sym_center,
            sym_tolerance=self.sym_tolerance,
            check_symmetry=self.check_symmetry,
            clean_input=self.clean_input,
            hide_original=self.hide_original,
            transfer_uvs=self.transfer_uvs,
            transfer_zones=self.transfer_zones,
        )
