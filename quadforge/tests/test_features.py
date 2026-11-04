"""Unit tests for core/features.py (smart hard-edge detection). No Blender needed:

    python tests/test_features.py
"""
import importlib.util
import pathlib
import sys
import types

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent / "core"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# features.py does "from . import geometry as geo": give it a tiny fake package.
package = types.ModuleType("qf_core")
package.__path__ = [str(ROOT)]
sys.modules["qf_core"] = package
geo = _load("qf_core.geometry", "geometry.py")
package.geometry = geo
features = _load("qf_core.features", "features.py")


def cube_polys(offset=0):
    return [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]


def cube_vertices(size, origin):
    h = size / 2.0
    return np.array([[x, y, z] for z in (-h, h) for y in (-h, h) for x in (-h, h)], float) + origin


def two_cubes(big=1.0, small=0.01):
    """A big cube and a tiny cube far apart, in one mesh: a long chain and a short chain of 90 degree edges."""
    co = np.vstack([cube_vertices(big, (0, 0, 0)), cube_vertices(small, (10, 0, 0))])
    polys = cube_polys() + [tuple(i + 8 for i in p) for p in cube_polys()]
    return geo.from_polygons(co, polys)


def test_connected_labels_groups_chains():
    labels = geo.connected_labels(7, np.array([0, 1, 4]), np.array([1, 2, 5]))
    assert labels[0] == labels[1] == labels[2]
    assert labels[4] == labels[5]
    assert labels[3] != labels[0] and labels[6] != labels[0] and labels[4] != labels[0]
    assert geo.connected_labels(3, np.array([], int), np.array([], int)).tolist() == [0, 1, 2]


def test_cube_edges_are_all_hard():
    m = geo.from_polygons(cube_vertices(1.0, (0, 0, 0)), cube_polys())
    result = features.detect_hard_edges(m, 35.0, quad_length=0.1)
    assert result.kept_edges == 12 and result.chains_kept == 1 and result.strong_edges == 12


def test_angle_above_every_edge_finds_nothing():
    m = geo.from_polygons(cube_vertices(1.0, (0, 0, 0)), cube_polys())
    result = features.detect_hard_edges(m, 100.0, quad_length=0.1)
    assert result.kept_edges == 0 and not result.mask.any()


def test_short_noise_chains_are_dropped_but_long_ones_stay():
    m = two_cubes()
    result = features.detect_hard_edges(m, 35.0, quad_length=0.1, keep_right_angles=False)
    assert result.chains_total == 2 and result.chains_kept == 1
    assert result.kept_edges == 12                      # only the big cube survives


def test_short_right_angle_chains_are_kept_when_requested():
    m = two_cubes()
    kept = features.detect_hard_edges(m, 35.0, quad_length=0.1, keep_right_angles=True)
    assert kept.chains_kept == 2 and kept.kept_edges == 24
    tiny_far_too_short = features.detect_hard_edges(m, 35.0, quad_length=1.0, keep_right_angles=True)
    assert tiny_far_too_short.kept_edges == 12          # below one quad length even right angles are noise


def test_min_chain_size_zero_keeps_everything():
    m = two_cubes()
    result = features.detect_hard_edges(m, 35.0, quad_length=0.1, min_chain_quads=0.0, keep_right_angles=False)
    assert result.kept_edges == 24


def test_feature_segments_return_the_marked_edges():
    m = geo.from_polygons(cube_vertices(2.0, (0, 0, 0)), cube_polys())
    result = features.detect_hard_edges(m, 35.0, quad_length=0.1)
    starts, ends = features.feature_segments(m, result.mask)
    assert starts.shape == (12, 3) and ends.shape == (12, 3)
    assert np.allclose(np.linalg.norm(ends - starts, axis=1), 2.0)


def test_edge_angles_of_a_cube_are_ninety_degrees():
    m = geo.from_polygons(cube_vertices(1.0, (0, 0, 0)), cube_polys())
    assert np.allclose(features.edge_angles(m), 90.0)


if __name__ == "__main__":
    failures = 0
    for test_name, test in sorted(globals().items()):
        if test_name.startswith("test_") and callable(test):
            try:
                test()
                print(f"PASS {test_name}")
            except Exception as error:  # noqa: BLE001
                failures += 1
                print(f"FAIL {test_name}: {error!r}")
    sys.exit(1 if failures else 0)
