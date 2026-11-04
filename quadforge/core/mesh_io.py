"""Fast Blender <-> NumPy transfer using ``foreach_get`` / ``foreach_set``.

No per-element Python loops: reading a 1M-face mesh takes a fraction of a second
and does not create millions of temporary Python objects (stable RAM usage).
"""
from __future__ import annotations

import numpy as np

from .geometry import MeshArrays


def _poly_offsets(mesh, n_polys: int, n_loops: int) -> np.ndarray:
    """First-loop index of every polygon plus the total loop count at the end."""
    # Blender 4.0+: ``polygon_offset_indices`` (length P+1 on current versions).
    try:
        raw = mesh.polygon_offset_indices
        arr = np.empty(len(raw), dtype=np.int32)
        raw.foreach_get(arr)
        if arr.shape[0] == n_polys + 1:
            return arr.astype(np.int64)
        if arr.shape[0] == n_polys:
            return np.append(arr, n_loops).astype(np.int64)
    except (AttributeError, TypeError, RuntimeError):
        pass
    # Fallback for older API: ``loop_start`` per polygon.
    starts = np.empty(n_polys, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    return np.append(starts, n_loops).astype(np.int64)


def read_mesh_arrays(mesh) -> MeshArrays:
    nv, ne, nl, npoly = len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons)

    co = np.empty(nv * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)

    edges = np.empty(ne * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edges)

    loop_verts = np.empty(nl, dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_verts)

    loop_edges = np.empty(nl, dtype=np.int32)
    mesh.loops.foreach_get("edge_index", loop_edges)

    return MeshArrays(
        co=co.reshape(nv, 3),
        edges=edges.reshape(ne, 2).astype(np.int64),
        poly_offsets=_poly_offsets(mesh, npoly, nl),
        loop_verts=loop_verts.astype(np.int64),
        loop_edges=loop_edges.astype(np.int64),
    )


def read_attribute(mesh, name: str, expected_len: int):
    """Read a (possibly hidden, e.g. ``.sculpt_mask``) attribute; ``None`` if absent."""
    attr = mesh.attributes.get(name)
    if attr is None or len(attr.data) != expected_len:
        return None
    kind = attr.data_type
    if kind == "FLOAT":
        arr = np.empty(expected_len, dtype=np.float32)
    elif kind == "INT":
        arr = np.empty(expected_len, dtype=np.int32)
    elif kind == "BOOLEAN":
        arr = np.empty(expected_len, dtype=bool)
    else:
        return None
    attr.data.foreach_get("value", arr)
    return arr


def read_edge_flag(mesh, prop: str) -> np.ndarray:
    """``prop`` is e.g. ``use_edge_sharp`` or ``use_seam``."""
    arr = np.zeros(len(mesh.edges), dtype=bool)
    try:
        mesh.edges.foreach_get(prop, arr)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return arr


_EDGE_FLAG_ATTRIBUTES = {"use_edge_sharp": "sharp_edge", "use_seam": ".uv_seam"}


def write_edge_flag(mesh, prop: str, values: np.ndarray) -> None:
    values = np.ascontiguousarray(values, dtype=bool)
    try:
        mesh.edges.foreach_set(prop, values)
    except (AttributeError, TypeError, RuntimeError):
        # Some Blender versions only expose the flag as a boolean edge attribute.
        name = _EDGE_FLAG_ATTRIBUTES.get(prop)
        if name is None:
            raise
        attribute = mesh.attributes.get(name)
        if attribute is None:
            attribute = mesh.attributes.new(name=name, type="BOOLEAN", domain="EDGE")
        attribute.data.foreach_set("value", values)
    mesh.update()


def read_material_indices(mesh) -> np.ndarray:
    arr = np.zeros(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("material_index", arr)
    return arr


def write_material_indices(mesh, values: np.ndarray) -> None:
    mesh.polygons.foreach_set("material_index", np.ascontiguousarray(values, dtype=np.int32))
    mesh.update()


def read_vertex_coordinates(mesh) -> np.ndarray:
    """Vertex positions as an ``(V, 3)`` float32 array (cheaper than reading the whole mesh)."""
    coordinates = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", coordinates)
    return coordinates.reshape(-1, 3)


def write_vertex_coordinates(mesh, coordinates: np.ndarray) -> None:
    flat = np.ascontiguousarray(coordinates, dtype=np.float32).reshape(-1)
    mesh.vertices.foreach_set("co", flat)
    mesh.update()


def write_int_face_attribute(mesh, name: str, values: np.ndarray) -> None:
    """Create (or overwrite) an integer attribute stored per face."""
    attribute = mesh.attributes.get(name)
    if attribute is None or attribute.domain != "FACE" or attribute.data_type != "INT":
        if attribute is not None:
            mesh.attributes.remove(attribute)
        attribute = mesh.attributes.new(name=name, type="INT", domain="FACE")
    attribute.data.foreach_set("value", np.ascontiguousarray(values, dtype=np.int32))
    mesh.update()


def read_vertex_group_matrix(mesh, group_count: int):
    """Weights of every vertex group as a ``(V, G)`` float32 array, or ``None`` when nothing is assigned."""
    if group_count <= 0:
        return None
    matrix = np.zeros((len(mesh.vertices), group_count), dtype=np.float32)
    found = False
    for vertex in mesh.vertices:       # Blender has no bulk call for vertex groups
        for assignment in vertex.groups:
            if assignment.group < group_count:
                matrix[vertex.index, assignment.group] = assignment.weight
                found = True
    return matrix if found else None


def read_vertex_group_weights(mesh, group_index: int):
    """Weight of one vertex group for every vertex (0 where unassigned); ``None`` if the mesh has no weights."""
    weights = np.zeros(len(mesh.vertices), dtype=np.float32)
    found = False
    for vertex in mesh.vertices:       # Blender has no bulk call for vertex groups
        for assignment in vertex.groups:
            if assignment.group == group_index:
                weights[vertex.index] = assignment.weight
                found = True
                break
    return weights if found else None
