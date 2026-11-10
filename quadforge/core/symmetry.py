"""Exact symmetry: cut -> remesh one half -> mirror -> weld.

Why this beats a "symmetry flag" inside a remesher: the remesher only ever sees
HALF the model, with an open border lying exactly on the symmetry plane. Because
that border is preserved, the mirrored result is symmetric by construction and
the centre seam welds cleanly.

All planes are expressed in the object's LOCAL space (the space the mesh is
stored in), so object rotation and scale never matter.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import bmesh
import numpy as np
from mathutils import Matrix, Vector

from . import geometry as geo

AXIS_NAMES = ("X", "Y", "Z")

# A sampled vertex "has a mirror partner" when its mirror image lies within this
# fraction of the model's bounding-box diagonal from the surface.
SYMMETRY_MATCH_RELATIVE_DISTANCE = 1e-3

# Border vertices closer to the plane than this fraction of the typical border edge are put on it.
SEAM_SNAP_EDGE_FRACTION = 0.4


@dataclass(frozen=True)
class SymmetryPlan:
    """Where to cut and which half to keep, for every active axis."""

    axes: tuple = ()                                  # 0 = X, 1 = Y, 2 = Z
    plane_offsets: dict = field(default_factory=dict)  # axis -> plane position (local space)
    kept_sides: dict = field(default_factory=dict)     # axis -> +1 (positive half) or -1
    tolerance: float = 0.0                            # cut / weld distance (absolute units)
    cut_after: bool = False                           # remesh the whole model with the plane as a guide line, cut afterwards

    @property
    def is_active(self) -> bool:
        return len(self.axes) > 0

    @property
    def engine_pieces(self) -> int:
        """How many equal pieces the ENGINE sees: the whole model when the cut happens after remeshing."""
        return 1 if self.cut_after else self.piece_count

    @property
    def piece_count(self) -> int:
        """Into how many equal pieces the model is split (2 per axis: 2, 4 or 8)."""
        return 2 ** len(self.axes)


def active_axes(use_x: bool, use_y: bool, use_z: bool) -> tuple:
    return tuple(axis for axis, enabled in enumerate((use_x, use_y, use_z)) if enabled)


def plane_position(coordinates: np.ndarray, axis: int, plane_at: str) -> float:
    """Plane position along ``axis``: object origin (0) or bounding-box centre."""
    if plane_at == "BBOX":
        return geo.bbox_center_along(coordinates, axis)
    return 0.0


def make_symmetry_plan(mesh_arrays: geo.MeshArrays, axes: tuple, plane_at: str,
                       keep_side: str, tolerance: float) -> SymmetryPlan:
    """Decide plane positions and kept halves. Pure NumPy, no Blender data needed."""
    offsets = {axis: plane_position(mesh_arrays.co, axis, plane_at) for axis in axes}
    sides = {}
    for axis in axes:
        if keep_side == "POSITIVE":
            sides[axis] = 1
        elif keep_side == "NEGATIVE":
            sides[axis] = -1
        else:  # AUTO: keep the half with more surface area
            sides[axis] = geo.side_by_area(mesh_arrays, axis, offsets[axis])
    return SymmetryPlan(axes, offsets, sides, tolerance)


def measure_symmetry_match(surface_tree, coordinates: np.ndarray, axis: int,
                           plane_offset: float, diagonal: float, sample_count: int = 2000):
    """Fraction of sampled vertices whose mirror image lies on the surface (0..1).

    ``surface_tree`` is a BVH tree of the ORIGINAL surface (built once by the
    pipeline and shared with the snap-to-surface step). A random sample of
    vertices is mirrored and its distance to that surface is measured; the BVH
    query runs in C, so this is fast even on dense sculpts.
    Returns ``None`` if the check cannot run: it is only a diagnostic and must
    never block the remesh.
    """
    if surface_tree is None or diagonal <= 0.0 or coordinates.shape[0] == 0:
        return None
    generator = np.random.default_rng(0)
    chosen = generator.choice(coordinates.shape[0],
                              size=min(sample_count, coordinates.shape[0]), replace=False)
    mirrored_points = coordinates[chosen].astype(np.float64)
    mirrored_points[:, axis] = 2.0 * plane_offset - mirrored_points[:, axis]
    distances = np.empty(len(mirrored_points))
    for index, point in enumerate(mirrored_points):
        hit = surface_tree.find_nearest(Vector(point))
        distances[index] = hit[3] if hit[3] is not None else np.inf
    return float(np.mean(distances / diagonal < SYMMETRY_MATCH_RELATIVE_DISTANCE))


def _unit_vector(axis: int, value: float = 1.0) -> Vector:
    vector = Vector((0.0, 0.0, 0.0))
    vector[axis] = value
    return vector


def cut_mesh_to_half(bm: bmesh.types.BMesh, axis: int, plane_offset: float,
                     kept_side: int, tolerance: float) -> None:
    """Cut at the plane and keep one side (``kept_side`` is +1 or -1).

    Vertices within ``tolerance`` of the plane are snapped onto it exactly
    (``use_snap_center``), so the open border left by the cut is perfectly flat.
    """
    everything = list(bm.verts) + list(bm.edges) + list(bm.faces)
    bmesh.ops.bisect_plane(
        bm,
        geom=everything,
        dist=tolerance,
        plane_co=_unit_vector(axis, plane_offset),
        plane_no=_unit_vector(axis, 1.0),
        use_snap_center=True,
        clear_inner=kept_side > 0,   # "inner" is the negative side of the plane
        clear_outer=kept_side < 0,
    )
    orphans = [vertex for vertex in bm.verts if not vertex.link_faces]
    if orphans:
        bmesh.ops.delete(bm, geom=orphans, context="VERTS")


def insert_plane_lines(bm: bmesh.types.BMesh, axes: tuple, plane_offsets: dict, tolerance: float) -> None:
    """Add an edge line where each plane crosses the mesh (nothing is removed).

    The engine is told to keep these lines, so its quads line up with the plane and the cut after
    the remesh passes between rows of quads instead of through them.
    """
    for axis in axes:
        everything = list(bm.verts) + list(bm.edges) + list(bm.faces)
        bmesh.ops.bisect_plane(
            bm, geom=everything, dist=tolerance,
            plane_co=_unit_vector(axis, plane_offsets[axis]), plane_no=_unit_vector(axis, 1.0),
            use_snap_center=True, clear_inner=False, clear_outer=False)


def plane_edge_mask(coordinates: np.ndarray, edge_vertices: np.ndarray, axes: tuple, plane_offsets: dict,
                    tolerance: float) -> np.ndarray:
    """Boolean per edge: both end points lie on one of the symmetry planes."""
    mask = np.zeros(len(edge_vertices), dtype=bool)
    for axis in axes:
        on_plane = np.abs(coordinates[:, axis] - plane_offsets[axis]) <= tolerance
        mask |= on_plane[edge_vertices[:, 0]] & on_plane[edge_vertices[:, 1]]
    return mask


def cut_result_to_half(mesh, plan: SymmetryPlan) -> None:
    """Cut a remeshed full model at every plane and keep the chosen sides (in place)."""
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        for axis in plan.axes:
            snap_straddling_corners(bm, axis, plan.plane_offsets[axis], max(plan.tolerance, 1e-6))
            cut_mesh_to_half(bm, axis, plan.plane_offsets[axis], plan.kept_sides[axis], plan.tolerance)
            # a quad lying flat ON the plane (the engine can make one where the surface just touches it)
            # would be doubled by the mirror and leave a non-manifold edge: it carries no area, drop it
            reach = max(plan.tolerance, 1e-6)
            # the cut can add a vertex right on top of one that is already on the plane (zero-area slivers)
            on_plane = [vertex for vertex in bm.verts
                        if abs(vertex.co[axis] - plan.plane_offsets[axis]) <= max(plan.tolerance, 1e-6)]
            if on_plane:
                bmesh.ops.remove_doubles(bm, verts=on_plane, dist=max(plan.tolerance, 1e-5))
            flat = [face for face in bm.faces
                    if all(abs(vertex.co[axis] - plan.plane_offsets[axis]) <= reach for vertex in face.verts)]
            if flat:
                bmesh.ops.delete(bm, geom=flat, context="FACES")
                loose = [vertex for vertex in bm.verts if not vertex.link_faces]
                if loose:
                    bmesh.ops.delete(bm, geom=loose, context="VERTS")
            # a quad with three vertices on the plane is pinched: two of them next to each other would
            # give one plane edge four faces after the mirror. Drop them; the hole is filled after the mirror.
            def on(vertex):
                return abs(vertex.co[axis] - plan.plane_offsets[axis]) <= reach
            pinched = set()
            for edge in bm.edges:
                if len(edge.link_faces) > 1 and on(edge.verts[0]) and on(edge.verts[1]):
                    pinched.update(face for face in edge.link_faces if sum(on(v) for v in face.verts) >= 3)
            # any other plane edge that still carries several faces (a thin flap folded on one side):
            # keep the biggest face, drop the rest
            for edge in bm.edges:
                if edge.is_valid and len(edge.link_faces) > 1 and on(edge.verts[0]) and on(edge.verts[1]):
                    faces_here = sorted(edge.link_faces, key=lambda face: face.calc_area(), reverse=True)
                    pinched.update(faces_here[1:])
            if pinched:
                bmesh.ops.delete(bm, geom=list(pinched), context="FACES")
                loose = [vertex for vertex in bm.verts if not vertex.link_faces]
                if loose:
                    bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(mesh)
    finally:
        bm.free()


def snap_vertices_to_plane(bm: bmesh.types.BMesh, axis: int, plane_offset: float,
                           tolerance: float) -> int:
    """Move vertices that are (almost) on the plane exactly onto it. Returns how many moved."""
    moved = 0
    for vertex in bm.verts:
        if abs(vertex.co[axis] - plane_offset) <= tolerance and vertex.co[axis] != plane_offset:
            vertex.co[axis] = plane_offset
            moved += 1
    return moved


def snap_straddling_corners(bm: bmesh.types.BMesh, axis: int, plane_offset: float, reach: float) -> int:
    """Pull a corner that sits just across the plane onto it, for quads the plane touches at another corner.

    A quad with one corner on the plane and the rest on both sides would be cut into a triangle and a quad (and the
    triangle would be mirrored too). Its lone corner on the far side is moved onto the plane when it is within half an
    edge length of it: the quad then simply has two corners on the plane. Returns how many corners moved.
    """
    moved = 0
    for face in list(bm.faces):
        d = [vertex.co[axis] - plane_offset for vertex in face.verts]
        if not any(abs(x) <= reach for x in d):
            continue
        pos = [i for i, x in enumerate(d) if x > reach]
        neg = [i for i, x in enumerate(d) if x < -reach]
        if not pos or not neg:
            continue
        group = pos if (len(pos), max(d[i] for i in pos)) < (len(neg), max(-d[i] for i in neg)) else neg
        mean_edge = sum(edge.calc_length() for edge in face.edges) / len(face.edges)
        if max(abs(d[i]) for i in group) <= 0.5 * mean_edge:
            for i in group:
                vertex = face.verts[i]
                if vertex.co[axis] != plane_offset:
                    vertex.co[axis] = plane_offset
                    moved += 1
    return moved


def median_border_edge_length(bm: bmesh.types.BMesh) -> float:
    """Typical length of the open-border edges (the cut line), or 0 when there is no border."""
    lengths = [edge.calc_length() for edge in bm.edges if edge.is_boundary]
    return float(np.median(lengths)) if lengths else 0.0


def snap_border_vertices_to_plane(bm: bmesh.types.BMesh, axis: int, plane_offset: float,
                                  distance: float) -> int:
    """Move OPEN-BORDER vertices that are close to the plane exactly onto it.

    The remesh engine can leave a border vertex a little off the cut plane (a fraction of one
    quad). With a tiny tolerance those vertices never meet their mirror image and a thin gap
    stays along the seam. The distance here is relative to the size of the quads on the border.
    """
    moved = 0
    for vertex in bm.verts:
        if vertex.is_boundary and abs(vertex.co[axis] - plane_offset) <= distance and vertex.co[axis] != plane_offset:
            vertex.co[axis] = plane_offset
            moved += 1
    return moved


def count_inconsistent_edges(bm: bmesh.types.BMesh) -> int:
    """Edges whose two faces walk the edge in the same direction (flipped faces)."""
    inconsistent = 0
    for edge in bm.edges:
        loops = edge.link_loops
        if len(loops) == 2 and loops[0].vert == loops[1].vert:
            inconsistent += 1
    return inconsistent


def make_winding_consistent(bm: bmesh.types.BMesh, mirrored_faces: list) -> None:
    """Make every face of the mirrored result face the same way as its neighbours.

    A mirrored copy can come out inside-out. That shows as a dark line along the seam in
    smooth shading. First flip only the mirrored faces; if that does not fix it, let Blender
    orient every face consistently.
    """
    before = count_inconsistent_edges(bm)
    if before == 0:
        return
    faces = [face for face in mirrored_faces if face.is_valid]
    if faces:
        bmesh.ops.reverse_faces(bm, faces=faces)
        after = count_inconsistent_edges(bm)
        if after > before:                      # it made things worse: undo
            bmesh.ops.reverse_faces(bm, faces=faces)
            after = before
        if after == 0:
            return
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)


def mirror_and_weld_seam(bm: bmesh.types.BMesh, axis: int, plane_offset: float,
                         tolerance: float) -> None:
    """Mirror everything across the plane, merge the centre seam and fix the face orientation."""
    seam_distance = max(tolerance, SEAM_SNAP_EDGE_FRACTION * median_border_edge_length(bm))
    snap_border_vertices_to_plane(bm, axis, plane_offset, seam_distance)
    snap_vertices_to_plane(bm, axis, plane_offset, tolerance)
    everything = list(bm.verts) + list(bm.edges) + list(bm.faces)
    result = bmesh.ops.mirror(
        bm,
        geom=everything,
        matrix=Matrix.Translation(_unit_vector(axis, plane_offset)),
        merge_dist=tolerance,
        axis=AXIS_NAMES[axis],
    )
    mirrored_faces = [item for item in result.get("geom", []) if isinstance(item, bmesh.types.BMFace)]
    make_winding_consistent(bm, mirrored_faces)
