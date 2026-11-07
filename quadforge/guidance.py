"""Plain-language advice shown at the top of the panel.

This module deliberately never imports ``bpy``: it works on a small snapshot of
the active object, so the rules can be unit-tested without Blender
(see ``tests/test_guidance.py``).

Flow:  active object --snapshot_from_object--> ObjectSnapshot
       snapshot + user options --build_guidance--> [GuidanceMessage, ...]
The UI only has to draw the messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import planning, texts

# Message levels (ERROR blocks the Remesh button; the others are only advice).
ERROR = "ERROR"
WARNING = "WARNING"
INFO = "INFO"
OK = "OK"

LARGE_MESH_FACE_COUNT = 6_000_000             # FORGE reads dense sculpts directly; ask before going beyond this
OFF_CENTER_FRACTION = 0.05                 # origin farther than 5% of the model width from the middle
AXIS_LETTERS = "XYZ"


@dataclass(frozen=True)
class ObjectSnapshot:
    """The few facts about the active object that the advice depends on."""

    exists: bool = False
    name: str = ""
    object_type: str = ""
    mode: str = "OBJECT"
    face_count: int = 0
    quad_equivalent: int = 0              # faces counted as quads: a triangle is half a quad
    active_modifier_count: int = 0
    material_slot_count: int = 0
    has_sculpt_mask: bool = False
    has_face_sets: bool = False
    vertex_group_count: int = 0
    bounds_min: tuple = (0.0, 0.0, 0.0)   # bounding box in the object's local space
    bounds_max: tuple = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class GuidanceOptions:
    """The user's current settings that influence the advice."""

    target_quads: int = 4000
    symmetry_axes: tuple = ()             # e.g. (0,) for X
    symmetry_plane_at_origin: bool = True
    use_materials: bool = False
    use_face_sets: bool = False
    use_mask: bool = False
    use_vertex_groups: bool = False
    ignored_levels: tuple = ()            # extra-level fragments that could not be used
    output_kind: str = "quads"            # "quads" or "tris" (what the user will get)


@dataclass(frozen=True)
class GuidanceMessage:
    level: str
    template: str                          # one of the constants in texts.py
    values: dict = field(default_factory=dict)


def snapshot_from_object(blender_object) -> ObjectSnapshot:
    """Read the facts we need from a Blender object. Cheap: safe to call while drawing the UI."""
    if blender_object is None:
        return ObjectSnapshot()
    if blender_object.type != "MESH":
        return ObjectSnapshot(exists=True, name=blender_object.name,
                              object_type=blender_object.type, mode=blender_object.mode)
    mesh = blender_object.data
    corners = [tuple(corner) for corner in blender_object.bound_box]
    face_count = len(mesh.polygons)
    loops = getattr(mesh, "loops", None)
    # Each polygon with k corners is worth (k - 2) triangles, and two triangles make a quad.
    quad_equivalent = max(0, (len(loops) - 2 * face_count) // 2) if loops is not None else 0
    return ObjectSnapshot(
        exists=True,
        name=blender_object.name,
        object_type="MESH",
        mode=blender_object.mode,
        face_count=face_count,
        quad_equivalent=quad_equivalent,
        active_modifier_count=sum(1 for modifier in blender_object.modifiers if modifier.show_viewport),
        material_slot_count=len(blender_object.material_slots),
        has_sculpt_mask=mesh.attributes.get(".sculpt_mask") is not None,
        has_face_sets=mesh.attributes.get(".sculpt_face_set") is not None,
        vertex_group_count=len(getattr(blender_object, "vertex_groups", ())),
        bounds_min=tuple(min(corner[i] for corner in corners) for i in range(3)),
        bounds_max=tuple(max(corner[i] for corner in corners) for i in range(3)),
    )


def needs_confirmation(face_count: int) -> bool:
    """Ask before starting when the mesh is heavy enough to freeze Blender for a long time."""
    return face_count > LARGE_MESH_FACE_COUNT


def build_guidance(snapshot: ObjectSnapshot, options: GuidanceOptions) -> list:
    """Return the messages to show, most important first."""
    if not snapshot.exists:
        return [GuidanceMessage(ERROR, texts.GUIDE_SELECT_MESH)]
    if snapshot.object_type != "MESH":
        return [GuidanceMessage(ERROR, texts.GUIDE_NOT_A_MESH,
                                {"name": snapshot.name, "kind": snapshot.object_type.lower()})]
    if snapshot.face_count == 0:
        return [GuidanceMessage(ERROR, texts.GUIDE_NO_FACES, {"name": snapshot.name})]

    messages = [GuidanceMessage(OK, texts.GUIDE_READY, {
        "name": snapshot.name,
        "faces": f"{snapshot.face_count:,}",
        "target": f"{options.target_quads:,}",
        "kind": options.output_kind,
    })]

    existing_detail = snapshot.quad_equivalent or snapshot.face_count
    if options.output_kind == "quads" and options.target_quads >= existing_detail:
        messages.append(GuidanceMessage(WARNING, texts.GUIDE_TARGET_NOT_LOWER, {
            "target": f"{options.target_quads:,}", "faces": f"{existing_detail:,}"}))
    if options.ignored_levels:
        messages.append(GuidanceMessage(WARNING, texts.GUIDE_LEVELS_IGNORED,
                                        {"items": ", ".join(options.ignored_levels)}))
    if snapshot.mode != "OBJECT":
        messages.append(GuidanceMessage(INFO, texts.GUIDE_LEAVES_MODE))
    if snapshot.active_modifier_count:
        messages.append(GuidanceMessage(INFO, texts.GUIDE_MODIFIERS,
                                        {"count": snapshot.active_modifier_count}))

    messages.extend(_zone_messages(snapshot, options))
    messages.extend(_symmetry_messages(snapshot, options))
    return messages


def can_remesh(messages: list) -> bool:
    return not any(message.level == ERROR for message in messages)


def _zone_messages(snapshot: ObjectSnapshot, options: GuidanceOptions) -> list:
    messages = []
    if options.use_materials and snapshot.material_slot_count < 2:
        messages.append(GuidanceMessage(WARNING, texts.GUIDE_NO_MATERIAL_ZONES))
    if options.use_face_sets and not snapshot.has_face_sets:
        messages.append(GuidanceMessage(WARNING, texts.GUIDE_NO_FACE_SETS))
    if options.use_mask and not snapshot.has_sculpt_mask:
        messages.append(GuidanceMessage(WARNING, texts.GUIDE_NO_MASK))
    if options.use_vertex_groups and snapshot.vertex_group_count < 1:
        messages.append(GuidanceMessage(WARNING, texts.GUIDE_NO_VERTEX_GROUPS))
    return messages


def _symmetry_messages(snapshot: ObjectSnapshot, options: GuidanceOptions) -> list:
    """Warn when the symmetry plane (object origin) is not in the middle of the model."""
    messages = []
    if not options.symmetry_plane_at_origin:
        return messages
    for axis in options.symmetry_axes:
        low, high = snapshot.bounds_min[axis], snapshot.bounds_max[axis]
        width = high - low
        if width <= 0.0:
            continue
        middle = 0.5 * (low + high)
        if abs(middle) / width > OFF_CENTER_FRACTION:
            messages.append(GuidanceMessage(WARNING, texts.GUIDE_OFF_CENTER,
                                            {"axis": AXIS_LETTERS[axis]}))
    return messages
