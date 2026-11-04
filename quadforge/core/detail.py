"""Keeping the sculpt's detail: snap to the original surface, polish quads, carry face data.

The remesh engine works on a light copy of the sculpt, so its vertices lie close
to, but not exactly on, the original surface. The functions here put them back:

  snap_vertices_to_surface   every vertex -> nearest point on the ORIGINAL sculpt
  polish_mesh                relax (even out the quads) + snap, a few passes
  transfer_zone_labels       copy zone labels from the original to the light copy
  copy_face_data             carry materials and zone labels through the engine

All distance queries use Blender's BVH tree (C++), so they stay fast even when
the original has millions of faces.
"""
from __future__ import annotations

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import geometry as geo
from . import mesh_io as mio
from . import symmetry as sym

ZONE_ATTRIBUTE = "quadforge_zone"     # per-face integer: which isolated zone a face belongs to
RELAX_STRENGTH = 0.5                  # how far a vertex moves toward its neighbours' average per pass
SNAP_MAX_MOVE_EDGES = 1.5             # a vertex never moves farther than this many local edge lengths
SNAP_COLLAPSE_RATIO = 0.15            # an edge shrunk below 15% of its length counts as collapsed
FEATURE_SNAP_RADIUS_QUADS = 0.6       # corner vertices snap to a feature line within 0.6 quad lengths
FEATURE_ANGLE_RATIO = 0.6             # result edges above 60% of the hard angle count as corner edges
FEATURE_SAMPLE_SPACING_QUADS = 0.5    # feature lines are sampled every half quad length
FEATURE_MAX_SAMPLES = 400_000         # cap on sample points (keeps memory and time bounded)


def snap_vertices_to_surface(mesh, surface_tree, plan: sym.SymmetryPlan) -> None:
    """Move every vertex to the closest point on the original surface, without collapsing quads.

    Two safeguards (found on a real test where snapping squeezed several vertices into one point):
      * a vertex never moves farther than ``SNAP_MAX_MOVE_EDGES`` times its local edge length;
      * if a move would shrink an edge to almost nothing, both of its vertices stay where they were.
    Vertices that sit on a symmetry plane are put back on that plane afterwards, so the centre seam
    stays flat and the mirrored result stays exactly symmetric.
    """
    arrays = mio.read_mesh_arrays(mesh)
    before = arrays.co.astype(np.float64)
    after = before.copy()
    for index in range(before.shape[0]):
        hit = surface_tree.find_nearest(Vector(before[index]))
        if hit[0] is not None:
            after[index] = tuple(hit[0])

    move = after - before
    distance = np.linalg.norm(move, axis=1)
    limit = SNAP_MAX_MOVE_EDGES * geo.vertex_mean_edge_length(arrays)
    too_far = (distance > limit) & (distance > 0.0)
    scale = np.ones_like(distance)
    scale[too_far] = limit[too_far] / distance[too_far]
    after = before + move * scale[:, None]

    old_lengths = np.linalg.norm(before[arrays.edges[:, 0]] - before[arrays.edges[:, 1]], axis=1)
    new_lengths = np.linalg.norm(after[arrays.edges[:, 0]] - after[arrays.edges[:, 1]], axis=1)
    collapsed = new_lengths < SNAP_COLLAPSE_RATIO * old_lengths
    if collapsed.any():
        stuck = geo.edge_vertex_mask(arrays, collapsed)
        after[stuck] = before[stuck]

    for axis in plan.axes:
        on_plane = np.abs(before[:, axis] - plan.plane_offsets[axis]) <= plan.tolerance
        after[on_plane, axis] = plan.plane_offsets[axis]
    mio.write_vertex_coordinates(mesh, after)


def polish_mesh(mesh, surface_tree, plan: sym.SymmetryPlan, sharp_angle_degrees, passes: int) -> None:
    """Even out the quads without losing the shape.

    Each pass relaxes the free vertices and snaps them back onto the original
    surface. Vertices on hard edges and on open borders (including the symmetry
    cut) are never relaxed, so corners and the centre seam stay exactly where
    the engine put them. ``sharp_angle_degrees`` is ``None`` when hard edges are off.
    With ``passes == 0`` only a single snap is done.
    """
    if passes <= 0:
        snap_vertices_to_surface(mesh, surface_tree, plan)
        return
    for _ in range(passes):
        arrays = mio.read_mesh_arrays(mesh)
        fixed = geo.boundary_vertex_mask(arrays)
        if sharp_angle_degrees is not None:
            fixed |= geo.edge_vertex_mask(arrays, geo.detect_sharp_edges(arrays, sharp_angle_degrees))
        relaxed = geo.relax_positions(arrays.co, arrays.edges, fixed, RELAX_STRENGTH, iterations=1)
        mio.write_vertex_coordinates(mesh, relaxed)
        snap_vertices_to_surface(mesh, surface_tree, plan)


def transfer_zone_labels(surface_tree, original_labels: np.ndarray, target_mesh) -> bool:
    """Give every face of ``target_mesh`` the zone label of the nearest original face.

    Needed because decimation does not reliably keep Face Sets or masks. Returns
    ``False`` if the BVH returned indices that do not match the label array
    (the caller then warns and continues without zones).
    """
    arrays = mio.read_mesh_arrays(target_mesh)
    centers = geo.polygon_centroids(arrays)
    labels = np.zeros(arrays.n_polys, dtype=np.int32)
    face_count = original_labels.shape[0]
    for face_index, center in enumerate(centers):
        hit = surface_tree.find_nearest(Vector(center))
        found = hit[2]
        if found is None:
            continue
        if found >= face_count:
            return False
        labels[face_index] = original_labels[found]
    mio.write_int_face_attribute(target_mesh, ZONE_ATTRIBUTE, labels)
    return True


def copy_face_data(reference_mesh, target_mesh) -> None:
    """Copy material index and zone label from the nearest face of ``reference_mesh``.

    The engine rebuilds the mesh from scratch and drops per-face data; this
    restores it so materials survive and the next level still knows its zones.
    """
    reference = mio.read_mesh_arrays(reference_mesh)
    materials = mio.read_material_indices(reference_mesh)
    zone_attribute = reference_mesh.attributes.get(ZONE_ATTRIBUTE)
    zones = mio.read_attribute(reference_mesh, ZONE_ATTRIBUTE, reference.n_polys) if zone_attribute else None

    flat_loops = reference.loop_verts.tolist()
    offsets = reference.poly_offsets.tolist()
    polygons = [flat_loops[offsets[i]:offsets[i + 1]] for i in range(reference.n_polys)]
    tree = BVHTree.FromPolygons(reference.co.tolist(), polygons)

    target = mio.read_mesh_arrays(target_mesh)
    centers = geo.polygon_centroids(target)
    new_materials = np.zeros(target.n_polys, dtype=np.int32)
    new_zones = np.zeros(target.n_polys, dtype=np.int32)
    for face_index, center in enumerate(centers):
        hit = tree.find_nearest(Vector(center))
        if hit[2] is None:
            continue
        new_materials[face_index] = materials[hit[2]]
        if zones is not None:
            new_zones[face_index] = zones[hit[2]]
    mio.write_material_indices(target_mesh, new_materials)
    if zones is not None:
        mio.write_int_face_attribute(target_mesh, ZONE_ATTRIBUTE, new_zones)
        return new_zones
    return None


def snap_corners_to_feature_lines(mesh, feature_segments, hard_angle_degrees: float,
                                  quad_length: float, plan: sym.SymmetryPlan) -> int:
    """Put the corner vertices of the result exactly ON the hard-edge lines of the model.

    The engine keeps hard edges only approximately, and snapping to the surface lets a vertex
    land on either side of a crease, which rounds right angles. Here the vertices that already
    run along a corner (result edges sharper than ``FEATURE_ANGLE_RATIO`` of the hard angle) are
    moved onto the nearest point of the original hard-edge lines, if it is within
    ``FEATURE_SNAP_RADIUS_QUADS`` quad lengths. Open-border vertices (the symmetry cut) are never
    touched. Returns how many vertices moved.
    """
    from mathutils.kdtree import KDTree

    segment_start, segment_end = feature_segments
    if segment_start.shape[0] == 0 or quad_length <= 0.0:
        return 0

    arrays = mio.read_mesh_arrays(mesh)
    corner_edges = geo.detect_sharp_edges(arrays, hard_angle_degrees * FEATURE_ANGLE_RATIO)
    candidates = geo.edge_vertex_mask(arrays, corner_edges) & ~geo.boundary_vertex_mask(arrays)
    candidate_indices = np.flatnonzero(candidates)
    if candidate_indices.size == 0:
        return 0

    # Sample every feature segment so a KD-tree can find the nearest line quickly.
    vectors = segment_end - segment_start
    lengths = np.linalg.norm(vectors, axis=1)
    spacing = FEATURE_SAMPLE_SPACING_QUADS * quad_length
    steps = np.maximum(1, np.ceil(lengths / spacing)).astype(np.int64)
    if int((steps + 1).sum()) > FEATURE_MAX_SAMPLES:
        steps = np.maximum(1, (steps * FEATURE_MAX_SAMPLES // int((steps + 1).sum()))).astype(np.int64)
    per_segment = steps + 1
    segment_of_sample = np.repeat(np.arange(segment_start.shape[0]), per_segment)
    first_sample = np.cumsum(per_segment) - per_segment
    position = np.arange(segment_of_sample.shape[0]) - np.repeat(first_sample, per_segment)
    fraction = position / np.repeat(steps, per_segment)
    samples = segment_start[segment_of_sample] + fraction[:, None] * vectors[segment_of_sample]

    tree = KDTree(samples.shape[0])
    for index, point in enumerate(samples):
        tree.insert(Vector(point), index)
    tree.balance()

    radius = FEATURE_SNAP_RADIUS_QUADS * quad_length
    coordinates = arrays.co.astype(np.float64)
    moved = 0
    for vertex in candidate_indices:
        _, sample_index, distance = tree.find(Vector(coordinates[vertex]))
        if sample_index is None or distance > radius:
            continue
        segment = segment_of_sample[sample_index]
        start, direction = segment_start[segment], vectors[segment]
        squared = float(direction @ direction)
        t = 0.0 if squared <= 0.0 else float(np.clip((coordinates[vertex] - start) @ direction / squared, 0.0, 1.0))
        target = start + t * direction
        if np.linalg.norm(target - coordinates[vertex]) <= radius:
            coordinates[vertex] = target
            moved += 1
    if moved:
        mio.write_vertex_coordinates(mesh, coordinates)
    return moved
