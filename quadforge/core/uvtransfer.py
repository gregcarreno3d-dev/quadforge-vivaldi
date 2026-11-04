"""Copy the UV maps of the original mesh onto a remeshed result.

Every corner of every new face is projected onto the original surface and takes the UV of that point
(barycentric inside the original triangle). The projection of a face is limited to the UV island that its
centre falls on, so a new face next to an old UV seam does not pick up UVs from the far side of the seam.

The result follows the old islands closely, but a new face that crosses an old seam is only an approximation.
For final texturing, bake from the original or unwrap the result again.
"""
from __future__ import annotations

import numpy as np

MAX_ISLANDS = 400            # above this the island split is skipped (one surface for the whole mesh)
UV_MATCH_TOLERANCE = 1e-5


def _loop_arrays(mesh):
    loops = len(mesh.loops)
    loop_vertex = np.empty(loops, dtype=np.int32)
    loop_edge = np.empty(loops, dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vertex)
    mesh.loops.foreach_get("edge_index", loop_edge)
    count = len(mesh.polygons)
    start = np.empty(count, dtype=np.int32)
    total = np.empty(count, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", total)
    return loop_vertex, loop_edge, start, total


def _island_labels(mesh, uv: np.ndarray) -> np.ndarray:
    """Island number of every polygon: faces are joined across an edge when the UVs on both sides agree."""
    loop_vertex, loop_edge, start, total = _loop_arrays(mesh)
    count = len(start)
    polygon_of_loop = np.repeat(np.arange(count, dtype=np.int32), total)
    local = np.arange(len(loop_vertex), dtype=np.int32) - np.repeat(start, total)
    next_loop = np.repeat(start, total) + (local + 1) % np.repeat(total, total)
    uv_a, uv_b = uv, uv[next_loop]
    n_edges = int(loop_edge.max()) + 1 if len(loop_edge) else 0
    per_edge = np.bincount(loop_edge, minlength=n_edges)
    order = np.argsort(loop_edge, kind="stable")
    firsts = np.concatenate([[0], np.cumsum(per_edge)[:-1]])
    pair_edges = np.flatnonzero(per_edge == 2)
    l1, l2 = order[firsts[pair_edges]], order[firsts[pair_edges] + 1]
    same = ((np.abs(uv_a[l1] - uv_b[l2]).max(axis=1) < UV_MATCH_TOLERANCE)
            & (np.abs(uv_b[l1] - uv_a[l2]).max(axis=1) < UV_MATCH_TOLERANCE))
    join_a, join_b = polygon_of_loop[l1[same]], polygon_of_loop[l2[same]]

    parent = np.arange(count, dtype=np.int64)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in zip(join_a.tolist(), join_b.tolist()):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    roots = np.array([find(i) for i in range(count)])
    _, labels = np.unique(roots, return_inverse=True)
    return labels.astype(np.int32)


def transfer_uvs(source_mesh, target_mesh) -> int:
    """Copy every UV map of ``source_mesh`` onto ``target_mesh``. Returns the number of maps copied (0 = none)."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    layers = list(source_mesh.uv_layers)
    if not layers or len(target_mesh.polygons) == 0:
        return 0

    source_mesh.calc_loop_triangles()
    n_tris = len(source_mesh.loop_triangles)
    if n_tris == 0:
        return 0
    tri_verts = np.empty(n_tris * 3, dtype=np.int32)
    tri_loops = np.empty(n_tris * 3, dtype=np.int32)
    tri_poly = np.empty(n_tris, dtype=np.int32)
    source_mesh.loop_triangles.foreach_get("vertices", tri_verts)
    source_mesh.loop_triangles.foreach_get("loops", tri_loops)
    source_mesh.loop_triangles.foreach_get("polygon_index", tri_poly)
    tri_verts = tri_verts.reshape(-1, 3)
    tri_loops = tri_loops.reshape(-1, 3)
    co = np.empty(len(source_mesh.vertices) * 3, dtype=np.float32)
    source_mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)

    # islands from the active UV map
    active = source_mesh.uv_layers.active or layers[0]
    uv_active = np.empty(len(source_mesh.loops) * 2, dtype=np.float32)
    active.uv.foreach_get("vector", uv_active)
    uv_active = uv_active.reshape(-1, 2).astype(np.float64)
    labels = _island_labels(source_mesh, uv_active)
    tri_island = labels[tri_poly]
    n_islands = int(labels.max()) + 1 if len(labels) else 1
    split = 1 < n_islands <= MAX_ISLANDS

    vertex_list = [Vector(v) for v in co.tolist()]
    whole_tree = BVHTree.FromPolygons(vertex_list, tri_verts.tolist(), all_triangles=True)
    island_trees = {}
    island_tris = {}
    if split:
        order = np.argsort(tri_island, kind="stable")
        bounds = np.searchsorted(tri_island[order], np.arange(n_islands + 1))
        for island in range(n_islands):
            ids = order[bounds[island]:bounds[island + 1]]
            if len(ids):
                island_tris[island] = ids
                island_trees[island] = BVHTree.FromPolygons(vertex_list, tri_verts[ids].tolist(), all_triangles=True)

    # the target: corner positions and face centres
    target_co = np.empty(len(target_mesh.vertices) * 3, dtype=np.float32)
    target_mesh.vertices.foreach_get("co", target_co)
    target_co = target_co.reshape(-1, 3).astype(np.float64)
    t_loop_vertex, _t_edge, t_start, t_total = _loop_arrays(target_mesh)
    t_poly_of_loop = np.repeat(np.arange(len(t_start), dtype=np.int32), t_total)
    centres = np.zeros((len(t_start), 3))
    np.add.at(centres, t_poly_of_loop, target_co[t_loop_vertex])
    centres /= t_total[:, None]

    corner_points = target_co[t_loop_vertex]
    hit_tri = np.full(len(t_loop_vertex), -1, dtype=np.int64)
    hit_point = np.zeros((len(t_loop_vertex), 3))

    face_island = np.zeros(len(t_start), dtype=np.int32)
    if split:
        for f, c in enumerate(centres.tolist()):
            found = whole_tree.find_nearest(Vector(c))
            if found[2] is not None:
                face_island[f] = tri_island[found[2]]
    loops_by_face = [np.arange(t_start[f], t_start[f] + t_total[f]) for f in range(len(t_start))] if split else None

    if split:
        for f in range(len(t_start)):
            island = int(face_island[f])
            tree = island_trees.get(island, whole_tree)
            ids = island_tris.get(island)
            for l in loops_by_face[f]:
                found = tree.find_nearest(Vector(corner_points[l]))
                if found[2] is None:
                    continue
                hit_point[l] = found[0]
                hit_tri[l] = int(ids[found[2]]) if ids is not None else found[2]
    else:
        for l in range(len(t_loop_vertex)):
            found = whole_tree.find_nearest(Vector(corner_points[l]))
            if found[2] is None:
                continue
            hit_point[l] = found[0]
            hit_tri[l] = found[2]

    def barycentric(tri_ids_, points):
        a = co[tri_verts[tri_ids_, 0]]
        b = co[tri_verts[tri_ids_, 1]]
        c = co[tri_verts[tri_ids_, 2]]
        v0, v1, v2 = b - a, c - a, points - a
        d00 = np.einsum("ij,ij->i", v0, v0)
        d01 = np.einsum("ij,ij->i", v0, v1)
        d11 = np.einsum("ij,ij->i", v1, v1)
        d20 = np.einsum("ij,ij->i", v2, v0)
        d21 = np.einsum("ij,ij->i", v2, v1)
        denom = d00 * d11 - d01 * d01
        denom = np.where(np.abs(denom) < 1e-30, 1.0, denom)
        w1_ = (d11 * d20 - d01 * d21) / denom
        w2_ = (d00 * d21 - d01 * d20) / denom
        return 1.0 - w1_ - w2_, w1_, w2_

    def sample(uv_loops, tri_ids_, weights):
        w0_, w1_, w2_ = weights
        return (w0_[:, None] * uv_loops[tri_loops[tri_ids_, 0]] + w1_[:, None] * uv_loops[tri_loops[tri_ids_, 1]]
                + w2_[:, None] * uv_loops[tri_loops[tri_ids_, 2]])

    ok = hit_tri >= 0
    tri_ids = np.where(ok, hit_tri, 0)
    weights = barycentric(tri_ids, hit_point)

    # A face that straddles a seam inside one island (a sphere or a tube) would take UVs from both sides.
    # For those faces every corner picks, among the triangles near it, the one whose UV is closest to the
    # UV of the face centre.
    uv0 = sample(uv_active, tri_ids, weights)
    face_uv = np.zeros((len(t_start), 2))
    np.add.at(face_uv, t_poly_of_loop, uv0)
    face_uv /= t_total[:, None]
    spread = np.zeros(len(t_start))
    np.maximum.at(spread, t_poly_of_loop, np.abs(uv0 - face_uv[t_poly_of_loop]).max(axis=1))
    typical = max(float(np.median(spread)), 1e-9)
    suspicious = np.flatnonzero(spread > 4.0 * typical)
    cand_loop, cand_loc, cand_idx, cand_dist, cand_face = [], [], [], [], []
    centre_hits = []
    for f in suspicious.tolist():
        near = whole_tree.find_nearest(Vector(centres[f]))
        if near[2] is None:
            centre_hits.append((-1, None))
            continue
        centre_hits.append((near[2], near[0]))
        ls = np.arange(t_start[f], t_start[f] + t_total[f])
        radius = 2.0 * float(np.linalg.norm(corner_points[ls] - centres[f], axis=1).max()) + 1e-9
        for l in ls.tolist():
            for loc, _n, idx, dist in whole_tree.find_nearest_range(Vector(corner_points[l]), radius):
                if split and tri_island[idx] != face_island[f]:
                    continue
                cand_loop.append(l); cand_loc.append(loc[:]); cand_idx.append(idx); cand_dist.append(dist)
                cand_face.append(f)
    if cand_loop:
        c_loop = np.array(cand_loop); c_idx = np.array(cand_idx); c_loc = np.array(cand_loc)
        c_dist = np.array(cand_dist); c_face = np.array(cand_face)
        hits_tri = np.array([h[0] for h in centre_hits])
        hits_pt = np.array([h[1] if h[1] is not None else (0.0, 0.0, 0.0) for h in centre_hits])
        valid = hits_tri >= 0
        centre_uv = np.zeros((len(suspicious), 2))
        if valid.any():
            centre_uv[valid] = sample(uv_active, hits_tri[valid], barycentric(hits_tri[valid], hits_pt[valid]))
        face_row = {int(f): i for i, f in enumerate(suspicious.tolist())}
        rows = np.array([face_row[int(f)] for f in c_face])
        uv_here = sample(uv_active, c_idx, barycentric(c_idx, c_loc))
        gap = np.abs(uv_here - centre_uv[rows]).max(axis=1)
        order = np.lexsort((c_dist, gap, c_loop))       # per loop: smallest UV gap, then nearest
        first = np.concatenate([[True], c_loop[order][1:] != c_loop[order][:-1]])
        pick = order[first]
        hit_point[c_loop[pick]] = c_loc[pick]
        hit_tri[c_loop[pick]] = c_idx[pick]
    if len(suspicious):
        ok = hit_tri >= 0
        tri_ids = np.where(ok, hit_tri, 0)
        weights = barycentric(tri_ids, hit_point)

    copied = 0
    for layer in layers:
        src = np.empty(len(source_mesh.loops) * 2, dtype=np.float32)
        layer.uv.foreach_get("vector", src)
        src = src.reshape(-1, 2).astype(np.float64)
        uv = sample(src, tri_ids, weights)
        uv[~ok] = 0.0
        new_layer = target_mesh.uv_layers.get(layer.name) or target_mesh.uv_layers.new(name=layer.name)
        new_layer.uv.foreach_set("vector", uv.astype(np.float32).reshape(-1))
        copied += 1
    if source_mesh.uv_layers.active is not None:
        target_mesh.uv_layers.active = target_mesh.uv_layers.get(source_mesh.uv_layers.active.name)
    target_mesh.update()
    return copied
