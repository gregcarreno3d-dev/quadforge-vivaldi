"""Subdivision match: move the quads' control points so that the Catmull-Clark limit surface (what Subdivision Surface
converges to) lies on the source surface instead of shrinking inside it.

Subdividing a quad mesh pulls it toward its centre: every vertex moves to a weighted average of its neighbours. For a
remesh that is meant to be subdivided again, this fit runs the same averaging in reverse: it computes where each vertex
would end up (its limit position), measures how far that is from the source surface along the surface normal, and moves
the vertex by that error. A few passes converge. Boundary vertices, vertices on hard edges, vertices touching a
non-quad face and points on a symmetry plane (they only slide inside it) are handled so that creases and borders keep
their shape.

The limit position of a vertex of valence n is (n^2 v + 4 sum(edge neighbours) + sum(face-opposite vertices)) / (n (n + 5)).
"""
import numpy as np

from . import mesh_io as mio


def _limit_positions(co, quads, edges, valence):
    n = valence.astype(np.float64)
    edge_sum = np.zeros_like(co)
    np.add.at(edge_sum, edges[:, 0], co[edges[:, 1]])
    np.add.at(edge_sum, edges[:, 1], co[edges[:, 0]])
    face_sum = np.zeros_like(co)
    for k in range(4):
        np.add.at(face_sum, quads[:, k], co[quads[:, (k + 2) % 4]])
    weight = np.maximum(n * (n + 5.0), 1.0)[:, None]
    return ((n * n)[:, None] * co + 4.0 * edge_sum + face_sum) / weight


def fit_to_limit_surface(mesh, surface_tree, plan=None, sharp_edge_flags=None, passes: int = 8, step: float = 0.9):
    """Adjust ``mesh`` in place. Returns (mean error before, mean error after) in units of the mean edge length, or
    None when there is nothing to do."""
    arrays = mio.read_mesh_arrays(mesh)
    sizes = np.diff(arrays.poly_offsets)
    if len(sizes) == 0 or not np.all(sizes == 4):
        return None
    co = arrays.co.astype(np.float64).copy()
    quads = arrays.loop_verts.reshape(-1, 4).astype(np.int64)
    edges = arrays.edges.astype(np.int64)
    n_verts = len(co)

    valence = np.bincount(edges.ravel(), minlength=n_verts)
    # an edge used by a single face is a border
    pair = np.sort(np.stack([quads, np.roll(quads, -1, axis=1)], axis=2), axis=2).reshape(-1, 2)
    key = pair[:, 0] * n_verts + pair[:, 1]
    unique, counts = np.unique(key, return_counts=True)
    border_vertices = np.zeros(n_verts, dtype=bool)
    for k in unique[counts == 1]:
        border_vertices[k // n_verts] = border_vertices[k % n_verts] = True
    free = (valence >= 3) & ~border_vertices
    # a crease (two faces meeting at more than about 25 degrees) is a hard edge even if nobody flagged it: keep it
    corners = co[quads]
    normals = np.cross(corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 1])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-30
    order = np.argsort(key, kind="stable")
    sorted_key = key[order]
    same_as_next = np.flatnonzero(sorted_key[:-1] == sorted_key[1:])
    first_face = order[same_as_next] // 4
    second_face = order[same_as_next + 1] // 4
    cosine = np.einsum("ij,ij->i", normals[first_face], normals[second_face])
    creased = same_as_next[cosine < 0.9]
    crease_pairs = pair[order[creased]]
    free[crease_pairs.ravel()] = False
    if sharp_edge_flags is not None and sharp_edge_flags.any():
        sharp_edges = edges[sharp_edge_flags]
        free[sharp_edges.ravel()] = False
    if not free.any():
        return None

    edge_len = np.linalg.norm(co[edges[:, 0]] - co[edges[:, 1]], axis=1)
    mean_len = np.zeros(n_verts)
    np.add.at(mean_len, edges[:, 0], edge_len)
    np.add.at(mean_len, edges[:, 1], edge_len)
    mean_len /= np.maximum(valence, 1)
    limit_step = 0.35 * mean_len        # never move a vertex by more than a third of its edges per pass

    on_plane = {}
    if plan is not None and plan.is_active:
        span = float(np.ptp(co, axis=0).max()) or 1.0
        for axis in plan.axes:
            on_plane[axis] = np.abs(co[:, axis] - plan.plane_offsets.get(axis, 0.0)) < max(plan.tolerance, 1e-6 * span) * 2.0

    partners = {}
    if plan is not None and plan.is_active:
        span = float(np.ptp(co, axis=0).max()) or 1.0
        grid = max(plan.tolerance, 1e-6 * span) * 4.0
        for axis in plan.axes:
            offset = plan.plane_offsets.get(axis, 0.0)
            mirrored = co.copy()
            mirrored[:, axis] = 2.0 * offset - mirrored[:, axis]
            lookup = {tuple(k): i for i, k in enumerate(np.round(co / grid).astype(np.int64))}
            partner = np.array([lookup.get(tuple(k), -1) for k in np.round(mirrored / grid).astype(np.int64)])
            partners[axis] = (partner, offset)

    indices = np.flatnonzero(free)
    first = last = None
    start = co.copy()
    for sweep in range(passes):
        limit = _limit_positions(co, quads, edges, valence)
        move = np.zeros_like(co)
        error = 0.0
        for v in indices:
            found = surface_tree.find_nearest(tuple(limit[v]))
            if found is None or found[0] is None:
                continue
            target, normal = np.array(found[0]), np.array(found[1])
            gap = float(np.dot(target - limit[v], normal))
            gap = max(-limit_step[v], min(limit_step[v], gap))
            move[v] = step * gap * normal
            error += abs(gap) / max(mean_len[v], 1e-30)
        for axis, mask in on_plane.items():
            move[mask, axis] = 0.0
        error /= max(1, len(indices))
        if first is None:
            first = error
        last = error
        co += move
        drift = co - start   # in total never further than a third of an edge from where the remesh put the vertex
        too_far = np.linalg.norm(drift, axis=1) > limit_step
        co[too_far] = start[too_far] + drift[too_far] * (limit_step[too_far] / np.linalg.norm(drift[too_far], axis=1))[:, None]
        for axis, (partner, offset) in partners.items():   # the two sides get the same correction: stay exactly symmetric
            paired = partner >= 0
            reflected = co[partner[paired]].copy()
            reflected[:, axis] = 2.0 * offset - reflected[:, axis]
            co[paired] = 0.5 * (co[paired] + reflected)
    co32 = co.astype(np.float32)
    mesh.vertices.foreach_set("co", co32.ravel())
    mesh.update()
    return first, last
