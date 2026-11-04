"""Unit tests for core/geometry.py. Run without Blender:

    python -m pytest tests/        (or)        python tests/test_geometry.py
"""
import importlib.util
import pathlib
import sys

import numpy as np

# Load geometry.py directly so the tests never import bpy (core/__init__ is lightweight,
# but this keeps them independent of the package layout).
_path = pathlib.Path(__file__).resolve().parent.parent / "core" / "geometry.py"
_spec = importlib.util.spec_from_file_location("qf_geometry", _path)
g = importlib.util.module_from_spec(_spec)
sys.modules["qf_geometry"] = g          # dataclasses needs the module registered
_spec.loader.exec_module(g)


def cube(offset=(0.0, 0.0, 0.0), size=1.0):
    h = size / 2
    co = np.array([[x, y, z] for z in (-h, h) for y in (-h, h) for x in (-h, h)], float) + offset
    # vertex index = x_bit + 2*y_bit + 4*z_bit
    polys = [
        (0, 2, 3, 1),  # -Z
        (4, 5, 7, 6),  # +Z
        (0, 1, 5, 4),  # -Y
        (2, 6, 7, 3),  # +Y
        (0, 4, 6, 2),  # -X
        (1, 3, 7, 5),  # +X
    ]
    return g.from_polygons(co, polys)


def test_cube_topology_counts():
    m = cube()
    assert (m.n_verts, m.n_edges, m.n_polys) == (8, 12, 6)
    first, second, counts = g.edge_face_pairs(m)
    assert np.all(counts == 2)
    assert np.all(first >= 0) and np.all(second >= 0) and np.all(first != second)


def test_normals_and_areas():
    m = cube(size=2.0)
    n, a = g.polygon_normals_areas(m)
    assert np.allclose(np.linalg.norm(n, axis=1), 1.0)
    assert np.allclose(a, 4.0)
    # outward normals: dot with centroid direction must be positive for a closed cube
    c = g.polygon_centroids(m)
    assert np.all(np.einsum("ij,ij->i", n, c) > 0)


def test_sharp_edge_threshold():
    m = cube()
    assert g.detect_sharp_edges(m, 35).sum() == 12     # all 90 degree edges
    assert g.detect_sharp_edges(m, 89).sum() == 12
    assert g.detect_sharp_edges(m, 91).sum() == 0


def test_flat_plane_has_no_sharp_edges_but_has_border():
    co = [[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0], [1, 1, 0], [2, 1, 0]]
    m = g.from_polygons(co, [(0, 1, 4, 3), (1, 2, 5, 4)])
    assert g.detect_sharp_edges(m, 1).sum() == 0
    assert g.boundary_edges(m).sum() == 6              # 2x1 grid perimeter


def test_region_boundaries_from_labels():
    m = cube()
    labels = np.array([0, 1, 0, 0, 0, 0])              # only the +Z face differs
    assert g.region_boundary_edges(m, labels).sum() == 4
    combined = g.combine_labels(np.array([0, 0, 0, 0, 0, 0]), np.array([0, 1, 0, 0, 0, 0]))
    assert g.region_boundary_edges(m, combined).sum() == 4


def test_mask_to_face_labels():
    m = cube()
    vmask = np.zeros(8)
    vmask[4:] = 1.0                                     # top four vertices masked (z = +h)
    labels = g.face_mask_from_vertex_mask(m, vmask, 0.5)
    assert labels.tolist() == [0, 1, 0, 0, 0, 0] or labels.sum() == 1


def test_symmetry_side_by_area():
    m = cube(offset=(0.3, 0, 0))                       # centred at +0.3 in X
    assert g.side_by_area(m, 0, 0.0) == 1
    m2 = cube(offset=(-0.3, 0, 0))
    assert g.side_by_area(m2, 0, 0.0) == -1
    assert g.side_by_area(m2, 0, -0.3) in (1, -1)      # balanced: any answer, no crash


def test_bbox_helpers():
    m = cube(offset=(1.0, 0, 0), size=2.0)
    assert np.isclose(g.bbox_center_along(m.co, 0), 1.0)
    assert np.isclose(g.bbox_diagonal(m.co), np.sqrt(12))


def test_stats_cube():
    s = g.mesh_stats(cube())
    assert (s["quads"], s["tris"], s["ngons"]) == (6, 0, 0)
    assert s["poles_3"] == 8 and s["poles_5plus"] == 0
    assert s["border_edges"] == 0 and s["non_manifold_edges"] == 0


def test_stats_triangles_and_ngon():
    co = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 1.5, 0]]
    m = g.from_polygons(co, [(0, 1, 2, 3, 4)])          # one pentagon
    assert g.mesh_stats(m)["ngons"] == 1
    m = g.from_polygons(co, [(0, 1, 2), (0, 2, 3)])
    assert g.mesh_stats(m)["tris"] == 2


def test_non_manifold_edge_reported():
    co = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]]
    # three triangles sharing edge (0,1)
    m = g.from_polygons(co, [(0, 1, 2), (0, 1, 3), (0, 1, 4)])
    assert g.mesh_stats(m)["non_manifold_edges"] == 1


def test_empty_mesh_is_safe():
    m = g.MeshArrays(np.zeros((0, 3)), np.zeros((0, 2), int), np.zeros(1, int),
                     np.zeros(0, int), np.zeros(0, int))
    n, a = g.polygon_normals_areas(m)
    assert n.shape == (0, 3) and a.shape == (0,)
    assert g.detect_sharp_edges(m, 30).shape == (0,)


def flat_grid(n=4):
    """An n x n grid of quads lying on z = 0 (open mesh, (n+1)^2 vertices)."""
    xs, ys = np.meshgrid(np.arange(n + 1, dtype=float), np.arange(n + 1, dtype=float))
    co = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1)
    index = np.arange((n + 1) * (n + 1)).reshape(n + 1, n + 1)
    polys = [(index[j, i], index[j, i + 1], index[j + 1, i + 1], index[j + 1, i]) for j in range(n) for i in range(n)]
    return g.from_polygons(co, polys), index


def test_boundary_vertex_mask_on_open_grid():
    m, index = flat_grid(4)
    border = g.boundary_vertex_mask(m)
    assert border.sum() == 16                           # perimeter of a 5x5 vertex grid
    assert not border[index[2, 2]]                      # centre vertex is interior


def test_edge_vertex_mask_marks_only_touched_vertices():
    m = cube()
    sharp = g.detect_sharp_edges(m, 35)
    assert g.edge_vertex_mask(m, sharp).all()           # every cube corner touches a hard edge
    assert not g.edge_vertex_mask(m, np.zeros(m.n_edges, dtype=bool)).any()


def test_relax_moves_a_bumped_vertex_toward_its_neighbours():
    m, index = flat_grid(4)
    co = m.co.copy()
    centre = index[2, 2]
    co[centre, 2] = 1.0                                 # lift the centre vertex
    fixed = g.boundary_vertex_mask(m)
    relaxed = g.relax_positions(co, m.edges, fixed, strength=0.5, iterations=1)
    assert 0.0 < relaxed[centre, 2] < 1.0               # pulled down, not past the plane
    assert np.allclose(relaxed[fixed], co[fixed])       # borders never move


def test_relax_keeps_fixed_vertices_and_converges_with_iterations():
    m, index = flat_grid(4)
    co = m.co.copy()
    co[index[2, 2], 2] = 1.0
    fixed = g.boundary_vertex_mask(m)
    once = g.relax_positions(co, m.edges, fixed, 0.5, 1)[index[2, 2], 2]
    many = g.relax_positions(co, m.edges, fixed, 0.5, 30)[index[2, 2], 2]
    assert many < once < 1.0
    assert abs(many) < 0.05                             # almost flat again


def test_relax_with_zero_iterations_or_no_edges_is_identity():
    m, _ = flat_grid(2)
    assert np.allclose(g.relax_positions(m.co, m.edges, np.zeros(m.n_verts, bool), 0.5, 0), m.co)
    assert g.relax_positions(np.zeros((0, 3)), np.zeros((0, 2), int), np.zeros(0, bool)).shape == (0, 3)


def test_consistent_cube_has_no_inconsistent_edges():
    assert g.inconsistent_winding_edges(cube()) == 0
    assert g.mesh_stats(cube())["inconsistent_edges"] == 0


def test_one_flipped_face_is_detected_on_its_four_edges():
    co = cube().co
    polys = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    polys[5] = polys[5][::-1]                           # flip the +X face
    flipped = g.from_polygons(co, polys)
    assert g.inconsistent_winding_edges(flipped) == 4


def test_a_whole_flipped_half_is_detected_along_the_seam_only():
    m, index = flat_grid(4)
    polys = [tuple(m.loop_verts[m.poly_offsets[i]:m.poly_offsets[i + 1]]) for i in range(m.n_polys)]
    cx = g.polygon_centroids(m)[:, 0]
    polys = [p[::-1] if cx[i] > 2.0 else p for i, p in enumerate(polys)]   # right half inside-out
    assert g.inconsistent_winding_edges(g.from_polygons(m.co, polys)) == 4   # the 4 seam edges


def test_local_edge_length_and_quad_length():
    assert np.allclose(g.vertex_mean_edge_length(cube()), 1.0)
    assert np.isclose(g.quad_edge_length(100.0, 25), 2.0)
    assert g.quad_edge_length(0.0, 10) == 0.0


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failed else 0)
