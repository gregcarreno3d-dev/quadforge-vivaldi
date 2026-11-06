"""The remesh pipeline, written as a sequence of small, named stages.

Read ``run()`` at the bottom first: it lists the stages in order. Each stage is
a short function above it that does exactly one job.

    1. read the source             modifiers applied, original untouched, BVH tree of the surface
    2. zone labels                 materials / Face Sets / mask -> one label per face
    3. working copy (proxy)        heavy sculpts are decimated to a light copy (e.g. 3M -> 80K faces)
    4. symmetry plan               which planes, which halves to keep
    5. clean and cut               only the kept half goes to the engine
    6. for every level (20K, 15K, 10K ...):
         a. mark hard edges and zone borders   (the constraints the engine must respect)
         b. remesh with the engine             (QuadriFlow, through the backend interface)
         c. restore materials and zone labels (the engine drops them)
         d. snap corners                       (corner vertices go exactly onto the model's edge lines)
         e. polish                             (relax the quads, snap back onto the ORIGINAL sculpt)
         f. the polished half becomes the input of the next, lower level
    7. mirror and weld each level  exact symmetry
    8. create the result objects   same transform as the original
    9. quality warnings

Why this is faster and lighter than remeshing the sculpt directly: the engine only
ever sees an 80K-face half (or less) instead of millions of faces, and lower levels
start from a 20K quad mesh instead of the sculpt. Why it keeps the detail: every
level is snapped onto the ORIGINAL surface at the end (stage 6d).

Stability rules:
  * the original object is never modified;
  * every temporary Blender datablock is deleted when ``run()`` ends, even after an error;
  * bulk work is done with NumPy arrays (see ``geometry`` / ``mesh_io``), not Python loops;
  * optional steps (snap, polish, zone transfer) warn and continue instead of failing.
"""
from __future__ import annotations

import dataclasses
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import bmesh
import bpy
import numpy as np

from .. import planning
from . import detail
from . import features
from . import geometry as geo
from . import mesh_io as mio
from . import profiling
from . import subdfit
from . import uvtransfer
from . import zonedata
from . import symmetry as sym
from .backends import BackendError, get_backend
from .backends.base import RemeshRequest

# Called as progress(percent, message) so the UI can show a progress bar.
ProgressCallback = Callable[[int, str], None]

# ---- named constants (instead of unexplained numbers scattered in the code) ----
DUPLICATE_VERTEX_DISTANCE = 1e-6          # relative to the model size; merges doubled vertices
MIN_SYMMETRY_TOLERANCE = 1e-7             # relative to the model size
MIN_FACES_PER_PIECE = 8                   # never ask the engine for fewer quads than this
MAX_HARD_EDGE_FRACTION = 0.25             # warn if more than 25% of the edges are "hard"
TARGET_MISS_WARNING_FRACTION = 0.15       # warn if the quad count is 15% away from the target
SYMMETRY_MATCH_WARNING_THRESHOLD = 0.9    # warn if fewer than 90% of sampled points match
MIN_DECIMATE_RATIO = 0.001
WORK_OBJECT_NAME = "QuadForge_work"
PROXY_OBJECT_NAME = "QuadForge_proxy"
DECIMATE_MODIFIER_NAME = "QuadForge_Decimate"
DENSITY_ATTRIBUTE = "quadforge_density"
CURVATURE_ANGLES = {"OFF": 0.0, "LOW": 0.35, "MEDIUM": 0.22, "HIGH": 0.15}   # radians a face edge may turn the surface


class PipelineError(RuntimeError):
    """A problem the user can understand and fix. The message is shown in Blender."""


# --------------------------------------------------------------------------- data classes

@dataclass
class RemeshSettings:
    """Everything the user can choose. Mirrors ``QuadForgeProperties`` in properties.py."""

    target_quads: int = 4000
    extra_levels: str = ""          # e.g. "15K, 10K, 5K, 4K"
    backend: str = "FORGE"          # FORGE (own engine) | QUADRIFLOW (Blender's)
    output: str = "QUADS"           # QUADS | TRIS (FORGE only; QuadriFlow always makes quads)
    density_group: str = ""         # vertex group that asks FORGE for finer faces (weight 1 = finest)
    density_ratio: float = 4.0      # how many times shorter the edges are where the weight is 1
    subdivision_match: bool = False  # move the control points so the Subdivision Surface limit lies on the source
    zone_factors: dict = dataclasses.field(default_factory=dict)  # zone name -> detail multiplier (2 = twice as fine)
    use_flow_guides: bool = True    # edges marked with Mark Flow Guide pull the quad rows along them (soft hint)
    curvature_detail: str = "OFF"   # OFF | LOW | MEDIUM | HIGH: smaller faces on strongly curved areas (eyes, lips, ear rims)

    # speed and detail
    use_proxy: bool = True
    proxy_auto: bool = True
    proxy_faces: int = 80_000
    use_reproject: bool = True
    polish_passes: int = 3

    # hard edges
    use_sharp_angle: bool = True
    sharp_angle_deg: float = 35.0
    use_existing_sharp: bool = True
    use_seams: bool = True
    smart_features: bool = True      # judge whole chains of sharp edges, not single edges
    min_feature_size: float = 2.0    # chains shorter than this many quad lengths are noise
    keep_right_angles: bool = True
    snap_features: bool = True       # put corner vertices exactly on the edge lines
    preserve_boundary: bool = True

    # zone isolation
    use_materials: bool = False
    use_face_sets: bool = False
    use_mask: bool = False
    use_vertex_groups: bool = False
    group_cutoff: float = 0.5
    mask_threshold: float = 0.5

    # symmetry
    sym_x: bool = False
    sym_y: bool = False
    sym_z: bool = False
    sym_side: str = "AUTO"          # AUTO | POSITIVE | NEGATIVE
    sym_center: str = "ORIGIN"      # ORIGIN | BBOX
    sym_tolerance: float = 1e-4     # relative to the bounding-box diagonal
    check_symmetry: bool = True

    # misc
    clean_input: bool = True
    seed: int = 0
    shading: str = "AUTO"           # AUTO (smooth + sharp edges) | SMOOTH | FLAT
    hide_original: bool = True
    transfer_uvs: bool = True       # copy the UV maps of the original onto the result
    transfer_zones: bool = True     # copy Face Sets and the sculpt mask of the original onto the result


@dataclass
class LevelResult:
    """One finished level of the ladder."""

    target_quads: int
    object: bpy.types.Object
    stats: dict
    hard_edge_count: int
    seconds: float


@dataclass
class RemeshResult:
    levels: list                                  # LevelResult, largest first
    warnings: list = field(default_factory=list)
    profile: dict = field(default_factory=dict)   # see profiling.Profiler.as_dict()
    source_faces: int = 0
    proxy_faces_used: Optional[int] = None        # None when no working copy was needed
    corners_snapped: int = 0                      # corner vertices moved onto the edge lines (all levels)

    # Shortcuts to the first (largest) level, which is what most callers want.
    @property
    def object(self) -> bpy.types.Object:
        return self.levels[0].object

    @property
    def stats(self) -> dict:
        return self.levels[0].stats

    @property
    def hard_edge_count(self) -> int:
        return self.levels[0].hard_edge_count

    @property
    def target_quads(self) -> int:
        return self.levels[0].target_quads


@dataclass
class SourceInfo:
    """Facts about the original object that do not change during the run."""

    evaluated_object: bpy.types.Object
    depsgraph: bpy.types.Depsgraph
    diagonal: float                 # length of the bounding-box diagonal
    face_count: int
    surface_tree: object = None     # BVH tree of the original surface (None if not built)


class TemporaryData:
    """Remembers temporary Blender datablocks and deletes them when the block ends.

        with TemporaryData() as temp:
            mesh = temp.track_mesh(bpy.data.meshes.new("scratch"))
            ...        # mesh is removed automatically, even if an error happens
    """

    def __init__(self):
        self._objects = []
        self._meshes = []

    def track_mesh(self, mesh):
        self._meshes.append(mesh)
        return mesh

    def track_object(self, blender_object):
        self._objects.append(blender_object)
        return blender_object

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        for blender_object in self._objects:
            try:
                bpy.data.objects.remove(blender_object, do_unlink=True)
            except (ReferenceError, RuntimeError):
                pass  # already gone
        for mesh in self._meshes:
            try:
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
            except (ReferenceError, RuntimeError):
                pass
        return False  # never swallow the original error


# --------------------------------------------------------------------------- stage 1

def _bounding_box_diagonal(evaluated_object) -> float:
    corners = np.array([tuple(corner) for corner in evaluated_object.bound_box], dtype=np.float64)
    return float(np.linalg.norm(corners.max(axis=0) - corners.min(axis=0)))


def _wants_surface_tree(settings: RemeshSettings, uses_zones: bool) -> bool:
    """The BVH tree costs time and memory, so only build it when something will use it."""
    symmetry_check = settings.check_symmetry and (settings.sym_x or settings.sym_y or settings.sym_z)
    return settings.use_reproject or symmetry_check or uses_zones or settings.subdivision_match


def _read_source(context, original, settings: RemeshSettings, uses_zones: bool,
                 temp: TemporaryData, warnings: list):
    """Copy of the original with modifiers applied, plus the BVH tree of its surface."""
    depsgraph = context.evaluated_depsgraph_get()
    evaluated_object = original.evaluated_get(depsgraph)
    mesh = temp.track_mesh(bpy.data.meshes.new_from_object(
        evaluated_object, preserve_all_data_layers=True, depsgraph=depsgraph))
    if len(mesh.polygons) == 0:
        raise PipelineError("The mesh has no faces.")

    diagonal = _bounding_box_diagonal(evaluated_object)
    if diagonal <= 0.0:
        raise PipelineError("The mesh has zero size.")

    info = SourceInfo(evaluated_object, depsgraph, diagonal, len(mesh.polygons))
    if _wants_surface_tree(settings, uses_zones):
        try:
            from mathutils.bvhtree import BVHTree
            info.surface_tree = BVHTree.FromObject(evaluated_object, depsgraph)  # object (local) space
        except Exception as error:  # noqa: BLE001
            warnings.append(f"Could not build the surface tree ({error!r}). "
                            f"Snapping to the sculpt and the symmetry check are skipped.")
    return info, mesh


GUIDE_ATTRIBUTE = "quadforge_guide"


def collect_guides(mesh, plan=None):
    """Segments (n, 2, 3) of the edges marked as flow guides, plus their mirror images when symmetry is on."""
    attribute = mesh.attributes.get(GUIDE_ATTRIBUTE)
    if attribute is None or attribute.domain != "EDGE" or len(mesh.edges) == 0:
        return None
    marked = np.zeros(len(mesh.edges), dtype=bool)
    try:
        attribute.data.foreach_get("value", marked)
    except (AttributeError, TypeError, RuntimeError):
        return None
    if not marked.any():
        return None
    arrays = mio.read_mesh_arrays(mesh)
    segments = arrays.co[arrays.edges[marked]].astype(np.float64)      # (n, 2, 3)
    if plan is not None and plan.is_active:
        for axis in plan.axes:
            mirrored = segments.copy()
            mirrored[:, :, axis] = 2.0 * plan.plane_offsets[axis] - mirrored[:, :, axis]
            segments = np.concatenate([segments, mirrored])
    return segments


def _zone_multiplier(mesh, settings: RemeshSettings, zone_labels, zone_names: dict):
    """Per-vertex edge-length multiplier from the per-zone detail factors, or None. A vertex on a border takes the finer side."""
    if zone_labels is None or not settings.zone_factors:
        return None
    face_multiplier = np.ones(len(zone_labels), dtype=np.float64)
    for label, name in zone_names.items():
        factor = float(settings.zone_factors.get(name, 1.0))
        if factor != 1.0:
            face_multiplier[zone_labels == label] = 1.0 / max(0.05, factor)
    if np.all(face_multiplier == 1.0):
        return None
    arrays = mio.read_mesh_arrays(mesh)
    sizes = np.diff(arrays.poly_offsets)
    face_of_loop = np.repeat(np.arange(len(sizes)), sizes)
    vertex_multiplier = np.ones(len(mesh.vertices), dtype=np.float64)
    np.minimum.at(vertex_multiplier, arrays.loop_verts, face_multiplier[face_of_loop])
    return _blend_across_zone_borders(vertex_multiplier, arrays, len(sizes), settings.target_quads)


def _blend_across_zone_borders(multiplier, arrays, n_faces: int, target_quads: int):
    """Spread the step at a zone border over about eight quad cells. The engine follows a gradual change in density
    but gives up on a sudden jump, so a plain per-zone value would be ignored."""
    edges = arrays.edges
    if edges is None or len(edges) == 0:
        return multiplier
    rings_per_cell = max(1.0, math.sqrt(n_faces / max(1, target_quads)))
    iterations = int(min(400, max(10, (8.0 * rings_per_cell) ** 2)))
    a, b = edges[:, 0], edges[:, 1]
    degree = np.bincount(a, minlength=len(multiplier)) + np.bincount(b, minlength=len(multiplier)) + 1.0
    value = np.log(multiplier)
    for _ in range(iterations):
        total = value.copy()
        np.add.at(total, a, value[b])
        np.add.at(total, b, value[a])
        value = total / degree
    return np.exp(value)


def _attach_density(mesh, original, settings: RemeshSettings, warnings: list, zone_labels=None, zone_names=None) -> None:
    """Build the per-vertex edge-length multiplier FORGE reads (1 = normal, <1 = finer) from the density vertex group
    and the per-zone detail factors (the finer of the two wins)."""
    multiplier = None
    if settings.density_group:
        group = original.vertex_groups.get(settings.density_group)
        if group is None:
            warnings.append(f"The vertex group '{settings.density_group}' does not exist, so the density is ignored.")
        else:
            weights = mio.read_vertex_group_weights(mesh, group.index)
            if weights is None or not weights.any():
                warnings.append(f"The vertex group '{settings.density_group}' is empty, so the density is ignored.")
            else:
                ratio = max(1.0, float(settings.density_ratio))
                multiplier = 1.0 / (1.0 + (ratio - 1.0) * np.clip(weights, 0.0, 1.0))
    by_zone = _zone_multiplier(mesh, settings, zone_labels, zone_names or {})
    if by_zone is not None:
        multiplier = by_zone if multiplier is None else np.minimum(multiplier, by_zone)
    if multiplier is None:
        return
    attribute = mesh.attributes.get(DENSITY_ATTRIBUTE)
    if attribute is not None:
        mesh.attributes.remove(attribute)
    attribute = mesh.attributes.new(name=DENSITY_ATTRIBUTE, type="FLOAT", domain="POINT")
    attribute.data.foreach_set("value", multiplier.astype(np.float32))


# --------------------------------------------------------------------------- stage 2

def _zone_names(mesh, original, settings: RemeshSettings, labels: np.ndarray) -> dict:
    """Readable name for every zone label (only when a single zone option is on; otherwise 'Zone N')."""
    kinds = []
    if settings.use_materials and len(original.material_slots) > 1:
        kinds.append("material")
    if settings.use_face_sets and mesh.attributes.get(".sculpt_face_set") is not None:
        kinds.append("face_set")
    if settings.use_mask and mesh.attributes.get(".sculpt_mask") is not None:
        kinds.append("mask")
    if settings.use_vertex_groups and len(original.vertex_groups) > 0:
        kinds.append("vertex_group")
    unique, first = np.unique(labels, return_index=True)
    names = {int(z): f"Zone {int(z) + 1}" for z in unique}
    if len(kinds) != 1:
        return names
    if kinds[0] == "material":
        materials = mio.read_material_indices(mesh)
        for z, i in zip(unique.tolist(), first.tolist()):
            slot = int(materials[i])
            material = original.material_slots[slot].material if slot < len(original.material_slots) else None
            names[z] = f"material {material.name}" if material else f"material slot {slot + 1}"
    elif kinds[0] == "vertex_group":
        groups = _vertex_group_labels(mesh, original, settings)
        for z, i in zip(unique.tolist(), first.tolist()):
            g = int(groups[i]) if groups is not None else -1
            names[z] = f"vertex group {original.vertex_groups[g].name}" if 0 <= g < len(original.vertex_groups) else "no vertex group"
    elif kinds[0] == "face_set":
        values = mio.read_attribute(mesh, ".sculpt_face_set", len(labels))
        for z, i in zip(unique.tolist(), first.tolist()):
            names[z] = f"Face Set {int(values[i])}"
    return names


def _vertex_group_labels(mesh, original, settings: RemeshSettings):
    matrix = mio.read_vertex_group_matrix(mesh, len(original.vertex_groups))
    if matrix is None:
        return None
    return geo.face_labels_from_vertex_groups(mio.read_mesh_arrays(mesh), matrix, settings.group_cutoff)


def _lost_zone_warnings(zone_labels, zone_names: dict, zone_report: list, several_levels: bool) -> list:
    """Zones that exist on the original but have no face in a result (smaller than one face at that size)."""
    if zone_labels is None or not zone_report:
        return []
    present = set(np.unique(zone_labels).tolist())
    messages = []
    for label, seen in zone_report:
        lost = sorted(present - seen)
        if not lost:
            continue
        shown = ", ".join(zone_names.get(z, f"Zone {z + 1}") for z in lost[:4]) + (" ..." if len(lost) > 4 else "")
        where = f" in the {label} result" if several_levels else ""
        subject = "1 of" if len(lost) == 1 else f"{len(lost)} of"
        messages.append(f"{subject} {len(present)} zones {'has' if len(lost) == 1 else 'have'} no faces{where} because "
                        f"{'it is' if len(lost) == 1 else 'they are'} smaller than one face at this size ({shown}). "
                        f"Raise the target faces to keep {'it' if len(lost) == 1 else 'them'}.")
    return messages


def _compute_zone_labels(mesh, original, settings: RemeshSettings, warnings: list):
    """One integer label per face of the FULL sculpt for every zone option that is on, or None."""
    columns = []
    if settings.use_materials and len(original.material_slots) > 1:
        columns.append(mio.read_material_indices(mesh))
    if settings.use_face_sets:
        face_sets = mio.read_attribute(mesh, ".sculpt_face_set", len(mesh.polygons))
        if face_sets is None:
            warnings.append("Face Sets were requested but the mesh has none.")
        else:
            columns.append(face_sets)
    if settings.use_mask:
        vertex_mask = mio.read_attribute(mesh, ".sculpt_mask", len(mesh.vertices))
        if vertex_mask is None:
            warnings.append("The sculpt mask was requested but the mesh has none.")
        else:
            arrays = mio.read_mesh_arrays(mesh)
            columns.append(geo.face_mask_from_vertex_mask(arrays, vertex_mask, settings.mask_threshold))
    if settings.use_vertex_groups:
        groups = _vertex_group_labels(mesh, original, settings)
        if groups is None:
            warnings.append("Vertex groups were requested but none has weights on this mesh.")
        else:
            columns.append(groups)
    return geo.combine_labels(*columns) if columns else None


# --------------------------------------------------------------------------- stage 3

def _decimate_to_proxy(context, mesh, proxy_faces: int, settings: RemeshSettings,
                       warnings: list) -> bool:
    """Reduce ``mesh`` in place to about ``proxy_faces`` triangles. Returns True if it did."""
    triangle_count = len(mesh.loop_triangles)
    if not planning.needs_proxy(triangle_count, proxy_faces, settings.use_proxy):
        return False

    proxy_object = bpy.data.objects.new(PROXY_OBJECT_NAME, mesh)
    context.scene.collection.objects.link(proxy_object)
    try:
        modifier = proxy_object.modifiers.new(DECIMATE_MODIFIER_NAME, "DECIMATE")
        modifier.decimate_type = "COLLAPSE"
        modifier.ratio = max(MIN_DECIMATE_RATIO, min(1.0, proxy_faces / triangle_count))
        modifier.use_collapse_triangulate = True
        with context.temp_override(object=proxy_object, active_object=proxy_object,
                                   selected_objects=[proxy_object],
                                   selected_editable_objects=[proxy_object]):
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        return True
    except RuntimeError as error:
        warnings.append(f"The working copy could not be created ({error}). "
                        f"The full mesh will be remeshed instead, which is slower.")
        return False
    finally:
        bpy.data.objects.remove(proxy_object, do_unlink=True)  # the mesh data stays


def _attach_zone_labels(mesh, labels, source: SourceInfo, was_decimated: bool, warnings: list) -> None:
    """Store the zone labels on the (possibly decimated) mesh as a face attribute."""
    if labels is None:
        return
    if not was_decimated:
        mio.write_int_face_attribute(mesh, detail.ZONE_ATTRIBUTE, labels)
        return
    if source.surface_tree is None or not detail.transfer_zone_labels(source.surface_tree, labels, mesh):
        warnings.append("Zones could not be carried onto the working copy, so they are ignored.")


# --------------------------------------------------------------------------- stage 4

def _plan_symmetry(proxy_arrays: geo.MeshArrays, source: SourceInfo, settings: RemeshSettings,
                   warnings: list) -> sym.SymmetryPlan:
    axes = sym.active_axes(settings.sym_x, settings.sym_y, settings.sym_z)
    tolerance = max(settings.sym_tolerance, MIN_SYMMETRY_TOLERANCE) * source.diagonal
    plan = sym.make_symmetry_plan(proxy_arrays, axes, settings.sym_center, settings.sym_side, tolerance)

    if settings.check_symmetry:
        for axis in plan.axes:
            match = sym.measure_symmetry_match(
                source.surface_tree, proxy_arrays.co, axis, plan.plane_offsets[axis], source.diagonal)
            if match is not None and match < SYMMETRY_MATCH_WARNING_THRESHOLD:
                warnings.append(
                    f"The mesh is not symmetric about {sym.AXIS_NAMES[axis]} "
                    f"({match:.0%} of the sampled points match). One half will be discarded "
                    f"and mirrored, which changes the shape.")
    return plan


# --------------------------------------------------------------------------- stage 5

def _clean_and_cut_to_half(proxy_mesh, plan: sym.SymmetryPlan, settings: RemeshSettings,
                           diagonal: float, temp: TemporaryData) -> bpy.types.Mesh:
    """Return the mesh that will be sent to the engine (cleaned, and cut if symmetric)."""
    bm = bmesh.new()
    try:
        bm.from_mesh(proxy_mesh)
        if settings.clean_input:
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=DUPLICATE_VERTEX_DISTANCE * diagonal)
            loose_vertices = [vertex for vertex in bm.verts if not vertex.link_faces]
            if loose_vertices:
                bmesh.ops.delete(bm, geom=loose_vertices, context="VERTS")

        if plan.cut_after:
            sym.insert_plane_lines(bm, plan.axes, plan.plane_offsets, plan.tolerance)   # cut later, after the remesh
        else:
            for axis in plan.axes:
                sym.cut_mesh_to_half(bm, axis, plan.plane_offsets[axis], plan.kept_sides[axis], plan.tolerance)
        if len(bm.faces) == 0:
            raise PipelineError("Nothing is left after the symmetry cut. "
                                "Check the symmetry axis, kept side and plane position.")

        work_mesh = temp.track_mesh(bpy.data.meshes.new("QuadForge_cut"))
        bm.to_mesh(work_mesh)
        return work_mesh
    finally:
        bm.free()


# --------------------------------------------------------------------------- stage 6a

@dataclass
class LevelConstraints:
    """What the engine must respect on one level, plus what is needed to snap corners afterwards."""

    hard_edge_count: int
    quad_length: float              # typical edge length of the quads this level will have
    feature_segments: tuple = None  # (starts, ends) of the geometric hard edges, or None
    has_guides: bool = False        # symmetry guide lines were marked (the engine must align to them)


def _mark_hard_edges_and_zones(mesh, settings: RemeshSettings, quads_for_piece: int,
                               warnings: list, plan: sym.SymmetryPlan = None) -> LevelConstraints:
    """Mark every edge the engine must keep as 'sharp'."""
    arrays = mio.read_mesh_arrays(mesh)
    normals, areas = geo.polygon_normals_areas(arrays)
    quad_length = geo.quad_edge_length(float(areas.sum()), quads_for_piece)
    hard = np.zeros(arrays.n_edges, dtype=bool)
    geometric = np.zeros(arrays.n_edges, dtype=bool)      # creases of the shape itself (not zone borders)

    if settings.use_sharp_angle:
        if settings.smart_features:
            found = features.detect_hard_edges(
                arrays, settings.sharp_angle_deg, quad_length,
                min_chain_quads=settings.min_feature_size,
                keep_right_angles=settings.keep_right_angles, normals=normals)
            geometric |= found.mask
        else:
            geometric |= geo.detect_sharp_edges(arrays, settings.sharp_angle_deg, normals)
    hard |= geometric
    guide_lines = 0
    planes = np.zeros(arrays.n_edges, dtype=bool)
    if plan is not None and plan.cut_after:
        # the symmetry planes act as guide lines for the engine (they are not creases of the shape)
        planes = sym.plane_edge_mask(arrays.co, arrays.edges, plan.axes, plan.plane_offsets, plan.tolerance)
        guide_lines = int(planes.sum())
        hard |= planes
    if settings.use_existing_sharp:
        marked = mio.read_edge_flag(mesh, "use_edge_sharp")
        if arrays.n_edges and settings.use_sharp_angle and marked.sum() > MAX_HARD_EDGE_FRACTION * arrays.n_edges:
            # flat-shaded imports (FBX, OBJ smoothing groups) mark nearly every edge sharp: that is shading, not creases
            message = (f"{marked.sum() / arrays.n_edges:.0%} of the edges were already marked sharp (typical of a "
                       f"flat-shaded import), so those marks were ignored and the sharp angle decided instead.")
            if message not in warnings:
                warnings.append(message)
        else:
            hard |= marked
    if settings.use_seams:
        hard |= mio.read_edge_flag(mesh, "use_seam")

    zone_labels = mio.read_attribute(mesh, detail.ZONE_ATTRIBUTE, arrays.n_polys)
    if zone_labels is not None:
        hard |= geo.region_boundary_edges(arrays, zone_labels)

    hard_count = int(hard.sum())
    hard_count_shown = int((hard & ~planes).sum())     # the guide lines are not counted as hard edges
    if arrays.n_edges and hard_count > MAX_HARD_EDGE_FRACTION * arrays.n_edges:
        message = (f"{hard_count / arrays.n_edges:.0%} of the edges are marked as hard. "
                   f"The angle is probably too low: raise it to keep the remesh clean.")
        if message not in warnings:
            warnings.append(message)
    mio.write_edge_flag(mesh, "use_edge_sharp", hard)
    segments = features.feature_segments(arrays, geometric) if geometric.any() else None
    return LevelConstraints(hard_count_shown, quad_length, segments, guide_lines > 0)


# --------------------------------------------------------------------------- stage 6b

SLIVER_EDGE_FRACTION = 0.01     # edges shorter than this share of the median edge are collapsed
SLIVER_MIN_SHARE = 0.015         # tiny edges must be at least this share of all edges before they are collapsed
SLIVER_RETARGET_FRACTION = 0.05  # if more than this share of faces vanished, run once more with a higher target


def _collapse_slivers(mesh) -> float:
    """Collapse the tiny edges that thin bevels leave behind (strips of quads squeezed to almost zero width).

    Returns the share of faces that disappeared. Only edges are collapsed, never loose vertices, so the mesh
    stays manifold.
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        count = len(bm.faces)
        if count == 0 or not bm.edges:
            return 0.0
        lengths = np.array([edge.calc_length() for edge in bm.edges])
        limit = SLIVER_EDGE_FRACTION * float(np.median(lengths))
        short = [edge for edge, length in zip(bm.edges, lengths) if length < limit]
        if len(short) < SLIVER_MIN_SHARE * len(bm.edges):
            return 0.0   # a few tiny edges are normal; only real strips are worth cleaning
        bmesh.ops.collapse(bm, edges=short)
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        removed = count - len(bm.faces)
        if removed > 0:
            bm.to_mesh(mesh)
            mesh.update()
        return removed / count
    finally:
        bm.free()


def _remesh_with_engine(context, mesh, plan: sym.SymmetryPlan, settings: RemeshSettings,
                        target_quads: int, hard_edge_count: int,
                        temp: TemporaryData, engine_progress=None, warnings=None, guides=None):
    """Generator: yields while the engine works; returns the remeshed mesh."""
    """Run the engine on ``mesh`` (a half if symmetric) and return the remeshed mesh."""
    quads_for_this_piece = max(MIN_FACES_PER_PIECE, round(target_quads / plan.engine_pieces))
    request = RemeshRequest(
        target_faces=int(quads_for_this_piece),
        preserve_sharp=hard_edge_count > 0 or plan.cut_after,
        fast_seams=plan.cut_after and hard_edge_count == 0,
        curv_angle=CURVATURE_ANGLES.get(settings.curvature_detail, 0.0),
        preserve_boundary=settings.preserve_boundary or plan.is_active,  # the cut edge must stay put
        seed=settings.seed,
        output=settings.output,
        progress=engine_progress,
        guides=guides,
    )

    keep_input = mesh.copy() if (settings.output == "QUADS" and request.preserve_sharp) else None
    work_object = bpy.data.objects.new(WORK_OBJECT_NAME, mesh)
    context.scene.collection.objects.link(work_object)
    try:
        try:
            yield from get_backend(settings.backend).remesh_steps(context, work_object, request)
            if keep_input is not None:
                vanished = _collapse_slivers(work_object.data)
                if vanished > SLIVER_RETARGET_FRACTION:
                    # thin bevels ate part of the face budget: run again asking for more so the final count matches
                    bigger = dataclasses.replace(request, target_faces=int(request.target_faces / max(0.4, 1.0 - vanished)),
                                                 engine_info={})
                    work_object.data = keep_input
                    keep_input = None
                    yield from get_backend(settings.backend).remesh_steps(context, work_object, bigger)
                    _collapse_slivers(work_object.data)
                    request.engine_info.clear()
                    request.engine_info.update(bigger.engine_info)
        except BackendError as error:
            raise PipelineError(str(error)) from error
        remeshed = work_object.data
        info = request.engine_info or {}
        if warnings is not None and info.get("density_note") == "partial" and info.get("method") != "pair":
            warnings.append("The density variation was only partly followed: this shape kept a clean quad flow instead. Try a lower density strength for a stronger effect.")
        if warnings is not None and info.get("curv_dropped"):
            with_curves, without = info["curv_dropped"]
            times = max(1, round(with_curves / max(1, without)))
            warnings.append(f"Detail on Curves was left out for this shape: it made the flow about {times}x more irregular "
                            "(many extra poles). Mechanical shapes do better with Detail on Curves Off.")
        if warnings is not None and settings.output == "QUADS" and info.get("method") == "pair":
            reason = info.get("fallback") or "unknown"
            warnings.append(
                "The grid method could not be used, so the engine used the simpler pairing method "
                f"(more irregular vertices). Reason: {reason}. Please report this text.")
    finally:
        bpy.data.objects.remove(work_object, do_unlink=True)  # the mesh data stays
        if keep_input is not None and keep_input.users == 0:
            bpy.data.meshes.remove(keep_input)

    if remeshed is not mesh:
        temp.track_mesh(remeshed)
    if len(remeshed.polygons) == 0:
        raise PipelineError("The engine returned an empty mesh.")
    return remeshed


# --------------------------------------------------------------------------- stage 7

def _apply_shading(mesh, settings: RemeshSettings) -> None:
    """Smooth / flat / smooth with the hard edges marked sharp (no more odd smoothing on corners)."""
    smooth = settings.shading != "FLAT"
    try:
        mesh.polygons.foreach_set("use_smooth", np.full(len(mesh.polygons), smooth, dtype=bool))
    except (AttributeError, TypeError, RuntimeError):
        pass  # shading is cosmetic; never fail because of it
    if settings.shading == "AUTO" and settings.use_sharp_angle:
        arrays = mio.read_mesh_arrays(mesh)
        mio.write_edge_flag(mesh, "use_edge_sharp", geo.detect_sharp_edges(arrays, settings.sharp_angle_deg))


def _fill_small_loops(bm, boundary_edges: list, max_sides: int = 12) -> list:
    """Close the small open loops that bmesh.holes_fill leaves (e.g. a flat sliver on the symmetry plane)."""
    by_vertex: dict = {}
    for edge in boundary_edges:
        for vert in edge.verts:
            by_vertex.setdefault(vert, []).append(edge)
    created, used = [], set()
    for start in boundary_edges:
        if start in used or not start.is_valid:
            continue
        loop, edge, vert = [], start, start.verts[0]
        closed = False
        while len(loop) <= max_sides:
            used.add(edge)
            loop.append(vert)
            vert = edge.other_vert(vert)
            options = [e for e in by_vertex.get(vert, []) if e is not edge and e not in used]
            if vert is loop[0]:
                closed = True
                break
            if not options:
                break
            edge = options[0]
        if closed and len(loop) >= 3:
            try:
                created.append(bm.faces.new(loop))
            except ValueError:
                try:
                    created.append(bm.faces.new(loop[::-1]))
                except ValueError:
                    pass
    return created


def _surface_defects(bm) -> int:
    """Open plus non-manifold edges: the number a cleanup must never raise."""
    return sum(1 for edge in bm.edges if len(edge.link_faces) != 2)


def _tidy_seam(bm, plan: sym.SymmetryPlan, settings) -> None:
    """Degenerate-geometry cleanup and seam-corner merge, applied only when a trial run on a copy leaves the surface
    no more open or non-manifold than before (hard-surface and open meshes must never get worse)."""
    def _run(target) -> None:
        _remove_degenerate_geometry(target)
        if plan.is_active and settings.output == "QUADS":
            for _ in range(3):      # a corner next to another one is left for the next round
                if not _dissolve_seam_corners(target, plan):
                    break

    trial = bm.copy()
    try:
        _run(trial)
        better = _surface_defects(trial) <= _surface_defects(bm)
    finally:
        trial.free()
    if better:
        _run(bm)


def _remove_degenerate_geometry(bm) -> int:
    """Zero-area faces (a quad folded onto a line, two of its corners being separate vertices at the same spot), stray
    wire edges and loose vertices. Merging the twin vertices lets the faces on both sides of the fold share their edge,
    so removing the folded face leaves a closed surface. Returns how many faces were removed."""
    if not bm.faces:
        return 0
    areas = [face.calc_area() for face in bm.faces]
    typical = float(sum(areas)) / len(areas)
    flat = [face for face, area in zip(bm.faces, areas) if area <= 1e-7 * typical]
    removed = 0
    if flat:
        twins = list({vert for face in flat for vert in face.verts})
        low = [min(vert.co[k] for vert in bm.verts) for k in range(3)]
        high = [max(vert.co[k] for vert in bm.verts) for k in range(3)]
        diagonal = sum((high[k] - low[k]) ** 2 for k in range(3)) ** 0.5
        bmesh.ops.remove_doubles(bm, verts=twins, dist=1e-6 * diagonal)
        broken = [face for face in bm.faces if face.is_valid and (
            len({vert.index for vert in face.verts}) < len(face.verts) or face.calc_area() <= 1e-7 * typical)]
        removed = len(broken)
        if broken:
            bmesh.ops.delete(bm, geom=broken, context="FACES_ONLY")
    wires = [edge for edge in bm.edges if not edge.link_faces]
    if wires:
        bmesh.ops.delete(bm, geom=wires, context="EDGES")
    loose = [vert for vert in bm.verts if not vert.link_edges]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    return removed


def _dissolve_seam_corners(bm, plan: sym.SymmetryPlan) -> int:
    """A vertex on the symmetry plane whose only edges lie in the plane has valence 2: the two quads around it (a mirror
    pair) share both of its edges and each has a flat 180 degree corner there. It is the half mesh's boundary corner,
    a defect the symmetric field leaves, always next to a valence-6 vertex. Dissolving the vertex merges the pair into one
    well-shaped quad that straddles the plane (a diamond), turning the 2 and 6 into a 3 and a 5. Returns how many."""
    def _on_plane(vert) -> bool:
        return any(abs(vert.co[axis] - plan.plane_offsets[axis]) <= 3.0 * plan.tolerance + 1e-9 for axis in plan.axes)

    corners = []
    for vert in bm.verts:
        if len(vert.link_edges) != 2 or len(vert.link_faces) != 2 or not _on_plane(vert):
            continue
        first, second = vert.link_faces
        if len(first.verts) != 4 or len(second.verts) != 4:
            continue
        if len(set(first.verts) | set(second.verts)) != 5:
            continue                    # the pair must share exactly the corner's two edges
        corners.append(vert)
    # two corners next to each other would merge into a 6-gon: keep one of each such pair
    chosen, taken = [], set()
    for vert in corners:
        faces = set(vert.link_faces)
        if faces & taken:
            continue
        taken |= faces
        chosen.append(vert)
    merged_count = 0
    for vert in chosen:
        first, second = vert.link_faces
        loop_a = [vertex for vertex in first.verts]
        shift = loop_a.index(vert)
        loop_a = loop_a[shift:] + loop_a[:shift]          # [corner, next, opposite, previous]
        loop_b = [vertex for vertex in second.verts]
        shift = loop_b.index(vert)
        loop_b = loop_b[shift:] + loop_b[:shift]
        if {loop_b[1], loop_b[3]} != {loop_a[1], loop_a[3]}:
            continue
        merged = [loop_a[1], loop_a[2], loop_a[3], loop_b[2]]
        try:
            face = bm.faces.new(merged)
        except ValueError:
            continue
        face.copy_from(first)
        bmesh.ops.delete(bm, geom=[vert], context="VERTS")    # takes the corner, its two edges and the two old faces
        merged_count += 1
    return merged_count


def _mirror_halves_into_final_mesh(half_mesh, plan: sym.SymmetryPlan, input_was_closed: bool,
                                   original, mesh_name: str, settings: RemeshSettings) -> bpy.types.Mesh:
    bm = bmesh.new()
    try:
        bm.from_mesh(half_mesh)
        for axis in plan.axes:
            sym.mirror_and_weld_seam(bm, axis, plan.plane_offsets[axis], plan.tolerance)
        if plan.is_active and plan.cut_after:
            def _on_plane(vert) -> bool:
                return any(abs(vert.co[axis] - plan.plane_offsets[axis]) <= 3.0 * plan.tolerance + 1e-9
                           for axis in plan.axes)

            def _seam_gaps() -> list:
                # open edges of a closed source are all seam gaps; on an open source (a neck, a cut) only those on the plane
                return [edge for edge in bm.edges if edge.is_boundary
                        and (input_was_closed or all(_on_plane(vert) for vert in edge.verts))]
            extra = []
            for edge in bm.edges:  # a seam edge shared by three faces: keep the two biggest
                if len(edge.link_faces) > 2 and all(_on_plane(vert) for vert in edge.verts):
                    extra.extend(sorted(edge.link_faces, key=lambda face: face.calc_area())[:len(edge.link_faces) - 2])
            if extra:
                bmesh.ops.delete(bm, geom=list({face for face in extra if face.is_valid}), context="FACES")
            gaps = _seam_gaps()
            if gaps:  # tiny holes left where pinched quads were dropped at the seam
                # a slit of near-duplicate vertices first (they sit a hair apart on the plane), then fill what is left
                reach = 0.25 * sum(edge.calc_length() for edge in gaps) / len(gaps)
                edge_verts = list({vertex for edge in gaps for vertex in edge.verts})
                bmesh.ops.remove_doubles(bm, verts=edge_verts, dist=reach)
                bmesh.ops.delete(bm, geom=[face for face in bm.faces if face.calc_area() <= 1e-12], context="FACES")
                gaps = _seam_gaps()
                if gaps:
                    filled = list(bmesh.ops.holes_fill(bm, edges=gaps, sides=12)["faces"])
                    filled += _fill_small_loops(bm, _seam_gaps())   # what holes_fill refused (flat slivers)
                    left = _seam_gaps()
                    if left:  # a larger hole (several quads lost together): close it too, as triangles, rather than leave it open
                        filled += list(bmesh.ops.holes_fill(bm, edges=left, sides=max(64, len(left)))["faces"])
                    big = [face for face in filled if face.is_valid and len(face.verts) > 4]
                    if big:  # keep the result quads and triangles only
                        bmesh.ops.triangulate(bm, faces=big, quad_method="BEAUTY", ngon_method="BEAUTY")
        _tidy_seam(bm, plan, settings)
        if plan.is_active and input_was_closed and all(not edge.is_boundary for edge in bm.edges):
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)  # mirroring can flip normals

        final_mesh = bpy.data.meshes.new(mesh_name)
        bm.to_mesh(final_mesh)
    finally:
        bm.free()

    for slot in original.material_slots:
        final_mesh.materials.append(slot.material)
    attribute = final_mesh.attributes.get(detail.ZONE_ATTRIBUTE)
    if attribute is not None:
        final_mesh.attributes.remove(attribute)  # internal bookkeeping, not for the user's mesh
    final_mesh.update()
    _apply_shading(final_mesh, settings)
    return final_mesh


# --------------------------------------------------------------------------- stage 8

def _create_result_object(context, original, final_mesh, object_name: str) -> bpy.types.Object:
    result_object = bpy.data.objects.new(object_name, final_mesh)
    for collection in (original.users_collection or (context.scene.collection,)):
        collection.objects.link(result_object)
    result_object.parent = original.parent
    result_object.matrix_parent_inverse = original.matrix_parent_inverse.copy()
    result_object.matrix_basis = original.matrix_basis.copy()
    _clear_component_selection(final_mesh)
    return result_object


def _clear_component_selection(mesh) -> None:
    """Leave no vertex, edge or face selected (the symmetry mirror step otherwise leaves one half selected,
    which shows as an orange half in Edit Mode)."""
    try:
        for collection in (mesh.polygons, mesh.edges, mesh.vertices):
            if len(collection):
                collection.foreach_set("select", [False] * len(collection))
    except (RuntimeError, TypeError, AttributeError):
        pass


def _finish_selection(context, original, result_objects: list, settings: RemeshSettings) -> None:
    if settings.hide_original:
        original.hide_set(True)
    for other in list(context.view_layer.objects):
        if other is not None:
            other.select_set(False)
    first = result_objects[0]
    first.select_set(True)
    context.view_layer.objects.active = first


# --------------------------------------------------------------------------- stage 9

def _quality_warnings(stats: dict, proxy_stats: dict, plan: sym.SymmetryPlan, target_quads: int,
                      level_label: str, several_levels: bool) -> list:
    prefix = f"[{level_label}] " if several_levels else ""
    warnings = []
    if plan.is_active and proxy_stats["border_edges"] == 0 and stats["border_edges"] > 0:
        warnings.append(f"{prefix}The centre seam has {stats['border_edges']} open edges. "
                        f"Try a larger Weld Distance or check where the symmetry plane sits.")
    if stats["inconsistent_edges"] and not proxy_stats["inconsistent_edges"]:
        warnings.append(f"{prefix}{stats['inconsistent_edges']} edges have faces that point in different "
                        f"directions (flipped faces). Use Mesh > Normals > Recalculate Outside.")
    if stats["non_manifold_edges"] and not proxy_stats["non_manifold_edges"]:
        warnings.append(f"{prefix}The result contains non-manifold edges.")
    if target_quads and abs(stats["faces"] - target_quads) / target_quads > TARGET_MISS_WARNING_FRACTION:
        warnings.append(f"{prefix}The result has {stats['faces']} faces, more than "
                        f"{TARGET_MISS_WARNING_FRACTION:.0%} away from the target of {target_quads}.")
    return warnings


# --------------------------------------------------------------------------- entry point

def _ignore_progress(percent: int, message: str) -> None:
    pass


def run(context, original, settings: RemeshSettings,
        progress: ProgressCallback = _ignore_progress) -> RemeshResult:
    """Remesh ``original`` and return the new objects. Raises ``PipelineError`` on problems."""
    steps = run_steps(context, original, settings, progress)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return done.value


def run_steps(context, original, settings: RemeshSettings,
              progress: ProgressCallback = _ignore_progress):
    """Generator version of ``run`` that yields often (used by the cancellable operator).

    Closing the generator before it ends (Esc) stops the engine process and deletes every temporary datablock,
    because the cleanup lives in ``finally`` / ``with`` blocks. The result is returned via StopIteration.value.
    """
    if original.type != "MESH":
        raise PipelineError("The active object is not a mesh.")

    warnings: list = []
    profiler = profiling.Profiler()
    level_plan = planning.parse_level_targets(settings.target_quads, settings.extra_levels)
    several_levels = level_plan.has_extra_levels
    uses_zones = ((settings.use_materials and len(original.material_slots) > 1)
                  or settings.use_face_sets or settings.use_mask or settings.use_vertex_groups)

    forge = settings.backend == "FORGE"
    if forge:
        # FORGE handles any density by itself and projects onto the source, so the working copy,
        # the corner snap and the polish of the QuadriFlow route are not needed (and would only cost time).
        settings = dataclasses.replace(settings, use_proxy=False, use_reproject=False,
                                       snap_features=False, polish_passes=0)
    else:
        settings = dataclasses.replace(settings, output="QUADS")
    kind = "tris" if settings.output == "TRIS" else "quads"

    with TemporaryData() as temp:
        progress(5, "Reading mesh")
        yield
        with profiler.stage("Read source"):
            source, mesh = _read_source(context, original, settings, uses_zones, temp, warnings)
        wants_uvs = settings.transfer_uvs and len(mesh.uv_layers) > 0
        wants_zone_data = settings.transfer_zones and zonedata.has_sculpt_data(mesh)
        data_source = temp.track_mesh(mesh.copy()) if (wants_uvs or wants_zone_data) else None

        progress(12, "Reading zones")
        yield
        with profiler.stage("Zone labels"):
            zone_labels = _compute_zone_labels(mesh, original, settings, warnings) if uses_zones else None
            zone_names = _zone_names(mesh, original, settings, zone_labels) if zone_labels is not None else {}

        if forge:
            _attach_density(mesh, original, settings, warnings, zone_labels, zone_names)

        progress(20, "Making the working copy")
        yield
        proxy_faces = planning.choose_proxy_faces(level_plan.largest, settings.proxy_auto, settings.proxy_faces)
        with profiler.stage("Working copy"):
            was_decimated = _decimate_to_proxy(context, mesh, proxy_faces, settings, warnings)
            _attach_zone_labels(mesh, zone_labels, source, was_decimated, warnings)

        progress(30, "Planning symmetry")
        yield
        with profiler.stage("Symmetry plan"):
            proxy_arrays = mio.read_mesh_arrays(mesh)
            proxy_stats = geo.mesh_stats(proxy_arrays)
            plan = _plan_symmetry(proxy_arrays, source, settings, warnings)
            if forge and settings.output == "QUADS" and plan.is_active \
                    and not os.environ.get("FORGE_HALF_FIRST"):
                # closed model: remesh the whole thing with the planes as guide lines and cut afterwards
                # (a flat cap on the cut disturbs the engine; this keeps the surface natural at the seam)
                plan = dataclasses.replace(plan, cut_after=True)

        progress(35, "Cutting")
        yield
        with profiler.stage("Clean and cut"):
            level_input = _clean_and_cut_to_half(mesh, plan, settings, source.diagonal, temp)

        base_input = temp.track_mesh(level_input.copy()) if forge else None   # FORGE: every level starts from the source
        zone_report = []       # (level label, zone labels that made it into the result)
        finished_halves = []   # (target, polished half mesh, hard edge count, seconds)
        snap_angle = settings.sharp_angle_deg if settings.use_sharp_angle else None
        keep_face_data = len(original.material_slots) > 1 or zone_labels is not None
        feature_lines = None   # hard-edge lines of the model, found once and reused by every level
        corners_snapped = 0
        for index, target in enumerate(level_plan.targets):
            label = planning.format_target(target)
            progress(40 + int(50 * index / len(level_plan.targets)), f"Remeshing {label}")
            yield
            level_started = time.perf_counter()
            if forge:
                level_input = temp.track_mesh(base_input.copy())
            span_start = 40 + int(50 * index / len(level_plan.targets))
            span = 50 / len(level_plan.targets)

            def engine_progress(percent, message, _start=span_start, _span=span, _label=label):
                progress(_start + int(_span * percent / 100), f"{_label}: {message}")
            quads_for_piece = max(MIN_FACES_PER_PIECE, round(target / plan.engine_pieces))
            with profiler.stage(f"Level {label}: constraints"):
                constraints = _mark_hard_edges_and_zones(level_input, settings, quads_for_piece, warnings, plan)
                if feature_lines is None:
                    feature_lines = constraints.feature_segments
                reference_mesh = temp.track_mesh(level_input.copy()) if keep_face_data else None
            with profiler.stage(f"Level {label}: engine"):
                guides = collect_guides(mesh, plan) if (forge and settings.use_flow_guides and settings.output == "QUADS") else None
                half_mesh = yield from _remesh_with_engine(context, level_input, plan, settings, target,
                                                constraints.hard_edge_count, temp, engine_progress, warnings, guides)
            if plan.cut_after:
                with profiler.stage(f"Level {label}: cut at the plane"):
                    sym.cut_result_to_half(half_mesh, plan)
            if reference_mesh is not None:
                with profiler.stage(f"Level {label}: face data"):
                    found_labels = detail.copy_face_data(reference_mesh, half_mesh)
                if found_labels is not None and not plan.is_active:
                    zone_report.append((label, set(np.unique(found_labels).tolist())))
            if settings.snap_features and snap_angle is not None and feature_lines is not None:
                with profiler.stage(f"Level {label}: corners"):
                    corners_snapped += detail.snap_corners_to_feature_lines(
                        half_mesh, feature_lines, snap_angle, constraints.quad_length, plan)
            if settings.use_reproject and source.surface_tree is not None:
                with profiler.stage(f"Level {label}: polish and snap"):
                    detail.polish_mesh(half_mesh, source.surface_tree, plan, snap_angle,
                                       settings.polish_passes)
            finished_halves.append((target, half_mesh, constraints.hard_edge_count,
                                    time.perf_counter() - level_started))
            if index + 1 < len(level_plan.targets) and not forge:
                level_input = temp.track_mesh(half_mesh.copy())   # the next, lower level starts here

        warnings.extend(_lost_zone_warnings(zone_labels, zone_names, zone_report, several_levels))
        progress(92, "Building the results")
        yield
        levels = []
        with profiler.stage("Build results"):
            for target, half_mesh, hard_edge_count, seconds in finished_halves:
                name = (f"{original.name}_{kind}_{planning.format_target(target)}"
                        if several_levels else f"{original.name}_{kind}")
                final_mesh = _mirror_halves_into_final_mesh(
                    half_mesh, plan, proxy_stats["border_edges"] == 0, original, name, settings)
                if settings.subdivision_match and settings.output == "QUADS" and source.surface_tree is not None:
                    try:
                        sharp = mio.read_edge_flag(final_mesh, "use_edge_sharp") \
                            if len(final_mesh.edges) else None
                        outcome = subdfit.fit_to_limit_surface(final_mesh, source.surface_tree, plan, sharp)
                        if outcome is None:
                            warnings.append("Subdivision match was skipped: the result has triangles or n-gons.")
                    except Exception as error:  # noqa: BLE001
                        warnings.append(f"Subdivision match failed ({error!r}); the result is unchanged.")
                result_object = _create_result_object(context, original, final_mesh, name)
                if data_source is not None and wants_uvs:
                    try:
                        uvtransfer.transfer_uvs(data_source, final_mesh)
                    except Exception as error:  # noqa: BLE001
                        warnings.append(f"The UV maps could not be copied to the result ({error}).")
                        wants_uvs = False
                if data_source is not None and wants_zone_data:
                    try:
                        zonedata.copy_sculpt_data(data_source, final_mesh)
                    except Exception as error:  # noqa: BLE001
                        warnings.append(f"The Face Sets and mask could not be copied to the result ({error}).")
                        wants_zone_data = False
                stats = geo.mesh_stats(mio.read_mesh_arrays(final_mesh))
                levels.append(LevelResult(target, result_object, stats, hard_edge_count, seconds))
                warnings.extend(_quality_warnings(stats, proxy_stats, plan, target,
                                                  planning.format_target(target), several_levels))
            _finish_selection(context, original, [level.object for level in levels], settings)

        if level_plan.ignored:
            warnings.append("Ignored extra levels: " + ", ".join(level_plan.ignored))
        progress(100, "Done")
        yield
        return RemeshResult(
            levels=levels,
            warnings=warnings,
            profile=profiler.as_dict(),
            source_faces=source.face_count,
            proxy_faces_used=proxy_faces if was_decimated else None,
            corners_snapped=corners_snapped,
        )
