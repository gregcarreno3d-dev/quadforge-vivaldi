"""Pure-NumPy mesh analysis (no ``bpy`` import, so it can be unit-tested anywhere).

Everything here works on flat arrays instead of Python-level mesh iteration.
That is what keeps the add-on fast and light on RAM with multi-million-face
sculpts: the heavy lifting is vectorised C code inside NumPy.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MeshArrays:
    """Flat, array-based view of a polygon mesh (same layout Blender uses)."""

    co: np.ndarray            # (V, 3) float vertex positions
    edges: np.ndarray         # (E, 2) int vertex indices per edge
    poly_offsets: np.ndarray  # (P + 1,) int, first loop of each polygon + total loops
    loop_verts: np.ndarray    # (L,) int vertex index of each loop (corner)
    loop_edges: np.ndarray    # (L,) int edge index of each loop (corner)

    @property
    def n_verts(self) -> int:
        return int(self.co.shape[0])

    @property
    def n_edges(self) -> int:
        return int(self.edges.shape[0])

    @property
    def n_polys(self) -> int:
        return int(self.poly_offsets.shape[0] - 1)

    @property
    def poly_sizes(self) -> np.ndarray:
        return np.diff(self.poly_offsets)

    @property
    def loop_poly(self) -> np.ndarray:
        """Polygon index of every loop."""
        return np.repeat(np.arange(self.n_polys, dtype=np.int64), self.poly_sizes)


def from_polygons(co, polys) -> MeshArrays:
    """Build ``MeshArrays`` from vertex positions and a list of polygons.

    Mostly used by the tests; Blender meshes come through ``mesh_io``.
    """
    co = np.asarray(co, dtype=np.float64)
    n_verts = co.shape[0]
    sizes = np.array([len(p) for p in polys], dtype=np.int64)
    offsets = np.concatenate(([0], np.cumsum(sizes)))
    loop_verts = np.concatenate([np.asarray(p, dtype=np.int64) for p in polys])
    nxt = np.arange(loop_verts.size) + 1
    nxt[offsets[1:] - 1] = offsets[:-1]
    a, b = loop_verts, loop_verts[nxt]
    keys = np.minimum(a, b) * n_verts + np.maximum(a, b)
    uniq, inverse = np.unique(keys, return_inverse=True)
    edges = np.stack((uniq // n_verts, uniq % n_verts), axis=1)
    return MeshArrays(co, edges, offsets, loop_verts, inverse.reshape(-1).astype(np.int64))


# --------------------------------------------------------------------------
# Per-polygon quantities
# --------------------------------------------------------------------------

def polygon_normals_areas(m: MeshArrays) -> tuple[np.ndarray, np.ndarray]:
    """Unit normals ``(P, 3)`` and areas ``(P,)`` using Newell's method.

    Works for triangles, quads and n-gons alike, fully vectorised.
    """
    n_polys = m.n_polys
    if n_polys == 0:
        return np.zeros((0, 3)), np.zeros(0)
    p = m.co.astype(np.float64, copy=False)[m.loop_verts]
    nxt = np.arange(p.shape[0]) + 1
    nxt[m.poly_offsets[1:] - 1] = m.poly_offsets[:-1]  # last corner wraps to the first
    newell = np.add.reduceat(np.cross(p, p[nxt]), m.poly_offsets[:-1], axis=0)
    length = np.linalg.norm(newell, axis=1)
    safe = np.where(length > 1e-30, length, 1.0)
    return newell / safe[:, None], 0.5 * length


def polygon_centroids(m: MeshArrays) -> np.ndarray:
    """Vertex-average centroid of each polygon, ``(P, 3)``."""
    if m.n_polys == 0:
        return np.zeros((0, 3))
    p = m.co.astype(np.float64, copy=False)[m.loop_verts]
    return np.add.reduceat(p, m.poly_offsets[:-1], axis=0) / m.poly_sizes[:, None]


# --------------------------------------------------------------------------
# Edge / face adjacency
# --------------------------------------------------------------------------

def edge_face_pairs(m: MeshArrays) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For every edge return ``(first_face, second_face, face_count)``.

    Missing faces are ``-1``. Edges with more than two faces (non-manifold)
    report their first two faces and ``face_count > 2``.
    """
    n_edges = m.n_edges
    n_loops = m.loop_edges.shape[0]
    if n_loops == 0:
        empty = np.full(n_edges, -1, dtype=np.int64)
        return empty, empty.copy(), np.zeros(n_edges, dtype=np.int64)
    order = np.argsort(m.loop_edges, kind="stable")
    sorted_poly = m.loop_poly[order]
    counts = np.bincount(m.loop_edges, minlength=n_edges)
    starts = np.cumsum(counts) - counts
    last = n_loops - 1
    first = np.where(counts >= 1, sorted_poly[np.minimum(starts, last)], -1)
    second = np.where(counts >= 2, sorted_poly[np.minimum(starts + 1, last)], -1)
    return first, second, counts


# --------------------------------------------------------------------------
# Feature detection
# --------------------------------------------------------------------------

def detect_sharp_edges(m: MeshArrays, angle_deg: float,
                       normals: np.ndarray | None = None) -> np.ndarray:
    """Boolean mask of edges whose dihedral angle exceeds ``angle_deg``.

    The dihedral angle is the angle between the normals of the two faces that
    share the edge (0 = flat, 90 = cube corner).
    """
    first, second, counts = edge_face_pairs(m)
    mask = np.zeros(m.n_edges, dtype=bool)
    two = np.flatnonzero(counts == 2)
    if two.size == 0:
        return mask
    if normals is None:
        normals, _ = polygon_normals_areas(m)
    cos = np.einsum("ij,ij->i", normals[first[two]], normals[second[two]])
    angle = np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))
    mask[two] = angle > angle_deg
    return mask


def face_labels_from_vertex_groups(m: MeshArrays, matrix: np.ndarray, cutoff: float = 0.5) -> np.ndarray:
    """Per-face label: the vertex group with the highest mean weight on the face, or -1 when none reaches ``cutoff``."""
    if m.n_polys == 0:
        return np.zeros(0, dtype=np.int64)
    sums = np.add.reduceat(np.asarray(matrix, dtype=np.float64)[m.loop_verts], m.poly_offsets[:-1], axis=0)
    mean = sums / m.poly_sizes[:, None]
    best = mean.argmax(axis=1)
    return np.where(mean[np.arange(m.n_polys), best] >= cutoff, best, -1).astype(np.int64)


def combine_labels(*columns: np.ndarray) -> np.ndarray:
    """Merge several per-face integer labels into one label per unique combination."""
    stacked = np.stack([np.asarray(c, dtype=np.int64) for c in columns], axis=1)
    _, inverse = np.unique(stacked, axis=0, return_inverse=True)
    return inverse.reshape(-1)


def region_boundary_edges(m: MeshArrays, face_labels: np.ndarray) -> np.ndarray:
    """Mask of edges that separate two faces with different labels.

    Used to isolate zones: materials, sculpt face sets, painted masks...
    """
    first, second, counts = edge_face_pairs(m)
    mask = np.zeros(m.n_edges, dtype=bool)
    two = np.flatnonzero(counts == 2)
    if two.size:
        mask[two] = face_labels[first[two]] != face_labels[second[two]]
    return mask


def boundary_edges(m: MeshArrays) -> np.ndarray:
    """Mask of open-border edges (used by exactly one face)."""
    _, _, counts = edge_face_pairs(m)
    return counts == 1


def face_mask_from_vertex_mask(m: MeshArrays, vertex_mask: np.ndarray,
                               threshold: float = 0.5) -> np.ndarray:
    """Per-face 0/1 label from a per-vertex mask (faces whose mean mask > threshold)."""
    if m.n_polys == 0:
        return np.zeros(0, dtype=np.int64)
    vals = np.asarray(vertex_mask, dtype=np.float64)[m.loop_verts]
    mean = np.add.reduceat(vals, m.poly_offsets[:-1]) / m.poly_sizes
    return (mean > threshold).astype(np.int64)


# --------------------------------------------------------------------------
# Symmetry helpers
# --------------------------------------------------------------------------

def bbox_diagonal(co: np.ndarray) -> float:
    if co.shape[0] == 0:
        return 0.0
    return float(np.linalg.norm(co.max(axis=0) - co.min(axis=0)))


def bbox_center_along(co: np.ndarray, axis: int) -> float:
    return float(0.5 * (co[:, axis].min() + co[:, axis].max()))


def side_by_area(m: MeshArrays, axis: int, offset: float = 0.0) -> int:
    """Return +1 / -1: the side of the plane that holds more surface area."""
    _, areas = polygon_normals_areas(m)
    c = polygon_centroids(m)[:, axis] - offset
    pos = float(areas[c > 0].sum())
    neg = float(areas[c < 0].sum())
    return 1 if pos >= neg else -1


# --------------------------------------------------------------------------
# Statistics
# --------------------------------------------------------------------------

def vertex_valence(m: MeshArrays) -> np.ndarray:
    return np.bincount(m.edges.reshape(-1), minlength=m.n_verts)


def mesh_stats(m: MeshArrays) -> dict:
    """Quality numbers shown in the report after remeshing."""
    sizes = m.poly_sizes
    first, second, counts = edge_face_pairs(m)
    valence = vertex_valence(m)
    on_border = np.zeros(m.n_verts, dtype=bool)
    border_edges = m.edges[counts == 1]
    if border_edges.size:
        on_border[border_edges.reshape(-1)] = True
    interior = (valence > 0) & ~on_border
    return {
        "verts": m.n_verts,
        "faces": m.n_polys,
        "tris": int(np.count_nonzero(sizes == 3)),
        "quads": int(np.count_nonzero(sizes == 4)),
        "ngons": int(np.count_nonzero(sizes > 4)),
        "border_edges": int(np.count_nonzero(counts == 1)),
        "non_manifold_edges": int(np.count_nonzero(counts > 2)),
        "inconsistent_edges": inconsistent_winding_edges(m),
        "poles_3": int(np.count_nonzero(interior & (valence == 3))),
        "poles_5plus": int(np.count_nonzero(interior & (valence >= 5))),
    }


# --------------------------------------------------------------------------
# Quad polishing (v0.3): which vertices must stay put, and Laplacian relaxation
# --------------------------------------------------------------------------

def edge_vertex_mask(m: MeshArrays, edge_mask: np.ndarray) -> np.ndarray:
    """Boolean mask of the vertices that touch at least one of the marked edges."""
    touched = np.zeros(m.n_verts, dtype=bool)
    marked = m.edges[np.asarray(edge_mask, dtype=bool)]
    if marked.size:
        touched[marked.reshape(-1)] = True
    return touched


def boundary_vertex_mask(m: MeshArrays) -> np.ndarray:
    """Boolean mask of the vertices on an open border (including a symmetry cut)."""
    return edge_vertex_mask(m, boundary_edges(m))


def relax_positions(co: np.ndarray, edges: np.ndarray, fixed_vertices: np.ndarray,
                    strength: float = 0.5, iterations: int = 1) -> np.ndarray:
    """Laplacian relaxation: move each free vertex toward the average of its neighbours.

    This evens out the size of the quads. Vertices in ``fixed_vertices`` (hard
    edges, open borders) never move. The surface shrinks slightly, so always
    follow it with a snap back onto the original surface (see ``core/detail.py``).
    Vectorised with ``np.bincount``: no Python loop over vertices.
    """
    positions = np.asarray(co, dtype=np.float64).copy()
    n_verts = positions.shape[0]
    if n_verts == 0 or edges.shape[0] == 0 or iterations <= 0:
        return positions
    first, second = edges[:, 0], edges[:, 1]
    degree = np.bincount(first, minlength=n_verts) + np.bincount(second, minlength=n_verts)
    movable = (degree > 0) & ~np.asarray(fixed_vertices, dtype=bool)
    safe_degree = np.where(degree > 0, degree, 1).astype(np.float64)
    for _ in range(iterations):
        neighbour_sum = np.empty_like(positions)
        for axis in range(3):
            neighbour_sum[:, axis] = (
                np.bincount(first, weights=positions[second, axis], minlength=n_verts)
                + np.bincount(second, weights=positions[first, axis], minlength=n_verts))
        average = neighbour_sum / safe_degree[:, None]
        positions[movable] += strength * (average[movable] - positions[movable])
    return positions


# --------------------------------------------------------------------------
# v0.4: orientation check, local edge sizes, connected chains
# --------------------------------------------------------------------------

def inconsistent_winding_edges(m: MeshArrays) -> int:
    """How many edges have two faces that walk the edge in the SAME direction.

    In a consistently oriented mesh the two faces of an edge walk it in opposite
    directions. A positive count means flipped faces (for example a mirrored half
    that came out inside-out), which shows up as a dark line in smooth shading.
    """
    n_loops = m.loop_edges.shape[0]
    if n_loops == 0:
        return 0
    following = np.arange(n_loops) + 1
    following[m.poly_offsets[1:] - 1] = m.poly_offsets[:-1]   # last corner wraps to the first
    start, end = m.loop_verts, m.loop_verts[following]
    order = np.argsort(m.loop_edges, kind="stable")
    counts = np.bincount(m.loop_edges, minlength=m.n_edges)
    firsts = np.cumsum(counts) - counts
    two = np.flatnonzero(counts == 2)
    if two.size == 0:
        return 0
    loop_a, loop_b = order[firsts[two]], order[firsts[two] + 1]
    same_direction = (start[loop_a] == start[loop_b]) & (end[loop_a] == end[loop_b])
    return int(np.count_nonzero(same_direction))


def edge_lengths(m: MeshArrays) -> np.ndarray:
    co = m.co.astype(np.float64, copy=False)
    return np.linalg.norm(co[m.edges[:, 0]] - co[m.edges[:, 1]], axis=1)


def vertex_mean_edge_length(m: MeshArrays) -> np.ndarray:
    """Average length of the edges around each vertex: its local resolution, ``(V,)``."""
    lengths = edge_lengths(m)
    first, second = m.edges[:, 0], m.edges[:, 1]
    total = np.bincount(first, weights=lengths, minlength=m.n_verts) + np.bincount(second, weights=lengths, minlength=m.n_verts)
    degree = np.bincount(first, minlength=m.n_verts) + np.bincount(second, minlength=m.n_verts)
    return total / np.maximum(degree, 1)


def quad_edge_length(total_area: float, quad_count: int) -> float:
    """Typical edge length of a quad mesh that covers ``total_area`` with ``quad_count`` quads."""
    return float(np.sqrt(max(total_area, 0.0) / max(int(quad_count), 1)))


def connected_labels(n_nodes: int, edge_a: np.ndarray, edge_b: np.ndarray) -> np.ndarray:
    """Label the connected components of a graph (min-label hooking with pointer jumping).

    Vectorised: used to find chains of connected hard edges without a Python loop.
    Nodes with no edge keep their own label.
    """
    labels = np.arange(n_nodes)
    if edge_a.size == 0:
        return labels
    while True:
        label_a, label_b = labels[edge_a], labels[edge_b]
        lower = np.minimum(label_a, label_b)
        updated = labels.copy()
        np.minimum.at(updated, label_a, lower)
        np.minimum.at(updated, label_b, lower)
        updated = updated[updated]                      # pointer jumping
        if np.array_equal(updated, labels):
            return labels
        labels = updated
