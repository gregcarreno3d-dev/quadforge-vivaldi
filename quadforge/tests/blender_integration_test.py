"""Integration test: runs the real add-on inside Blender with generated test meshes.

How to run it (from the folder that contains the ``quadforge`` folder):

    blender --factory-startup -b --python quadforge/tests/blender_integration_test.py

``--factory-startup`` keeps your installed copy of the add-on out of the way.
(You can also run it with the ``bpy`` Python module: pip install bpy numpy, Python 3.11.)

Every scenario prints PASS or FAIL with the reason. Send me the full output if anything fails.
"""
import math
import pathlib
import sys
import traceback

import bpy  # must come first: it makes bmesh and mathutils importable when used as a module
import bmesh
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

# Make the add-on importable as a normal package: <folder containing quadforge>/quadforge
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))
import quadforge  # noqa: E402
from quadforge.core import geometry as geo  # noqa: E402
from quadforge.core import mesh_io as mio  # noqa: E402
from quadforge.core import pipeline  # noqa: E402

RESULTS = []


# --------------------------------------------------------------------------- helpers

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def make_object(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    blender_object = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(blender_object)
    bpy.context.view_layer.objects.active = blender_object
    blender_object.select_set(True)
    return blender_object


def triangulate(bm):
    bmesh.ops.triangulate(bm, faces=bm.faces[:])


def squash_into_blob(bm):
    """Symmetric, sculpt-like shape: an ellipsoid with a gentle bump."""
    for vertex in bm.verts:
        vertex.co.x *= 1.3
        vertex.co.z *= 0.8 + 0.15 * math.cos(3.0 * vertex.co.y)


def dense_ellipsoid(name="Blob", segments=96, rings=48):
    """About 9,000 triangles."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=1.0)
    squash_into_blob(bm)
    triangulate(bm)
    return make_object(name, bm)


def heavy_blob(name="HeavyBlob", subdivisions=8):
    """An icosphere blob: subdivisions=8 gives 327,680 triangles (a small stand-in for a real sculpt)."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1.0)
    squash_into_blob(bm)
    return make_object(name, bm)


def subdivided_box(name="Box", cuts=10):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    triangulate(bm)
    return make_object(name, bm)


def run_pipeline(blender_object, **setting_overrides):
    settings = pipeline.RemeshSettings()
    for key, value in setting_overrides.items():
        setattr(settings, key, value)
    return pipeline.run(bpy.context, blender_object, settings)


def largest_mirror_error(coordinates, axis=0):
    """Largest distance from a vertex to the nearest mirrored vertex (small meshes only)."""
    mirrored = coordinates.copy()
    mirrored[:, axis] *= -1.0
    return max(float(np.min(np.linalg.norm(coordinates - point, axis=1))) for point in mirrored)


def distances_to_original(original, result_object):
    """Distance of every result vertex to the ORIGINAL surface (both share local coordinates)."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    tree = BVHTree.FromObject(original.evaluated_get(depsgraph), depsgraph)
    coordinates = mio.read_vertex_coordinates(result_object.data).astype(np.float64)
    distances = []
    for point in coordinates:
        hit = tree.find_nearest(Vector(point))
        distances.append(hit[3] if hit[3] is not None else np.inf)
    return np.array(distances)


def edge_length_variation(result_object):
    """Coefficient of variation of the edge lengths (lower = more even quads)."""
    arrays = mio.read_mesh_arrays(result_object.data)
    lengths = np.linalg.norm(arrays.co[arrays.edges[:, 0]] - arrays.co[arrays.edges[:, 1]], axis=1)
    return float(lengths.std() / lengths.mean())


def outward_fraction_by_half(result_object, axis=0):
    """Share of faces that point away from the centre, for the +axis half and the -axis half.

    A healthy closed blob gives about 100% for both. The bug found in the first real test (a mirrored half
    that came out inside-out, shown as a dark line along the seam) gives ~0% for one half.
    """
    arrays = mio.read_mesh_arrays(result_object.data)
    normals, _ = geo.polygon_normals_areas(arrays)
    centers = geo.polygon_centroids(arrays)
    outward = np.einsum("ij,ij->i", normals, centers - arrays.co.mean(axis=0)) > 0
    positive, negative = centers[:, axis] > 0.05, centers[:, axis] < -0.05
    return float(outward[positive].mean()), float(outward[negative].mean())


def distance_to_box_edge_lines(coordinates, half_size=1.0, near=0.25):
    """Distance of vertices that run along the edges of a box to the nearest box edge line."""
    a = np.sort(np.abs(coordinates), axis=1)[:, ::-1]            # largest coordinate first
    distance = np.sqrt((half_size - a[:, 0]) ** 2 + (half_size - a[:, 1]) ** 2)
    return distance[distance < near]


def scenario(function):
    """Each scenario starts from an empty scene with the add-on freshly registered."""
    def run_scenario():
        reset_scene()
        quadforge.register()
        try:
            function()
            RESULTS.append((function.__name__, True))
            print(f"PASS  {function.__name__}")
        except Exception as error:  # noqa: BLE001
            RESULTS.append((function.__name__, False))
            print(f"FAIL  {function.__name__}: {error!r}")
            traceback.print_exc()
        finally:
            try:
                quadforge.unregister()
            except Exception:  # noqa: BLE001
                pass
    run_scenario.__name__ = function.__name__
    return run_scenario


# --------------------------------------------------------------------------- basic behaviour

@scenario
def target_count_is_respected():
    original = dense_ellipsoid()
    result = run_pipeline(original, target_quads=1000)
    faces = result.stats["faces"]
    print(f"      target 1000 -> {faces} faces ({result.stats['quads']} quads, {result.stats['tris']} tris)")
    assert abs(faces - 1000) / 1000 < 0.25, faces
    assert result.stats["quads"] / faces > 0.9
    assert original.hide_get() is True
    assert result.object.name != original.name


@scenario
def original_object_is_not_modified():
    original = dense_ellipsoid()
    face_count = len(original.data.polygons)
    run_pipeline(original, target_quads=800, hide_original=False)
    assert len(original.data.polygons) == face_count


@scenario
def symmetry_x_is_exact_and_closed():
    result = run_pipeline(dense_ellipsoid(), target_quads=1200, sym_x=True)
    stats = result.stats
    coordinates = mio.read_mesh_arrays(result.object.data).co.astype(np.float64)
    error = largest_mirror_error(coordinates, axis=0)
    print(f"      faces={stats['faces']} border_edges={stats['border_edges']} mirror_error={error:.2e}")
    assert stats["border_edges"] == 0, "the centre seam did not weld"
    assert error < 1e-4, error
    assert stats["inconsistent_edges"] == 0, "faces along the seam point in different directions"


@scenario
def symmetry_on_three_axes():
    result = run_pipeline(dense_ellipsoid(), target_quads=1600,
                          sym_x=True, sym_y=True, sym_z=True, sym_side="POSITIVE")
    coordinates = mio.read_mesh_arrays(result.object.data).co.astype(np.float64)
    errors = [largest_mirror_error(coordinates, axis) for axis in (0, 1, 2)]
    print(f"      faces={result.stats['faces']} border_edges={result.stats['border_edges']} "
          f"mirror_errors={[f'{e:.1e}' for e in errors]}")
    assert result.stats["border_edges"] == 0
    assert max(errors) < 1e-4


@scenario
def hard_edges_are_kept_on_a_box():
    result = run_pipeline(subdivided_box(), target_quads=600, sharp_angle_deg=35.0)
    print(f"      hard edges marked: {result.hard_edge_count}")
    assert result.hard_edge_count > 0
    coordinates = mio.read_mesh_arrays(result.object.data).co.astype(np.float64)
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    worst = max(float(np.min(np.linalg.norm(coordinates - corner, axis=1))) for corner in corners)
    print(f"      worst distance to a box corner: {worst:.4f}")
    assert worst < 0.15


@scenario
def smooth_surface_has_no_hard_edges_with_a_high_angle():
    result = run_pipeline(dense_ellipsoid(), target_quads=800, sharp_angle_deg=60.0, use_seams=False)
    assert result.hard_edge_count == 0, result.hard_edge_count


@scenario
def materials_are_isolated_and_transferred():
    original = dense_ellipsoid()
    original.data.materials.append(bpy.data.materials.new("A"))
    original.data.materials.append(bpy.data.materials.new("B"))
    for polygon in original.data.polygons:
        polygon.material_index = 1 if polygon.center.z > 0 else 0
    result = run_pipeline(original, target_quads=1000, use_materials=True)
    assert len(result.object.material_slots) == 2
    indices = mio.read_material_indices(result.object.data)
    print(f"      faces per material: {np.bincount(indices).tolist()}  hard edges: {result.hard_edge_count}")
    assert set(np.unique(indices)) == {0, 1}
    centers_z = geo.polygon_centroids(mio.read_mesh_arrays(result.object.data))[:, 2]
    wrong = np.count_nonzero((indices == 1) & (centers_z < -0.15)) + np.count_nonzero((indices == 0) & (centers_z > 0.15))
    assert wrong == 0, wrong


@scenario
def asymmetric_mesh_gives_a_warning():
    original = dense_ellipsoid()
    for vertex in original.data.vertices:
        if vertex.co.x > 0:
            vertex.co.x *= 1.6
    result = run_pipeline(original, target_quads=800, sym_x=True)
    print(f"      warnings: {result.warnings}")
    assert any("not symmetric" in warning for warning in result.warnings)


@scenario
def transform_is_copied_to_the_result():
    original = dense_ellipsoid()
    original.location = (3.0, 1.0, 2.0)
    original.rotation_euler = (0.1, 0.2, 0.3)
    original.scale = (1.0, 2.0, 1.0)
    result = run_pipeline(original, target_quads=500)
    bpy.context.view_layer.update()   # world matrices are refreshed lazily
    assert np.allclose(result.object.matrix_world, original.matrix_world)


@scenario
def temporary_data_is_cleaned_up():
    original = dense_ellipsoid()
    objects_before, meshes_before = len(bpy.data.objects), len(bpy.data.meshes)
    run_pipeline(original, target_quads=500, sym_x=True)
    assert len(bpy.data.objects) == objects_before + 1, "a temporary object was left behind"
    assert len(bpy.data.meshes) == meshes_before + 1, "a temporary mesh was left behind"


@scenario
def empty_mesh_gives_a_clear_error():
    mesh = bpy.data.meshes.new("Empty")
    empty = bpy.data.objects.new("Empty", mesh)
    bpy.context.scene.collection.objects.link(empty)
    try:
        run_pipeline(empty, target_quads=500)
    except pipeline.PipelineError as error:
        assert "no faces" in str(error).lower()
    else:
        raise AssertionError("expected a PipelineError")


# --------------------------------------------------------------------------- v0.3: working copy, detail, levels

@scenario
def dense_mesh_is_remeshed_directly_and_keeps_the_detail():
    original = heavy_blob(subdivisions=8)                       # 327,680 triangles
    faces_in = len(original.data.polygons)
    result = run_pipeline(original, target_quads=2000, sym_x=True)
    print(f"      source {faces_in} faces -> result {result.stats['faces']} faces")
    assert abs(result.stats["faces"] - 2000) / 2000 < 0.3
    distances = distances_to_original(original, result.object)
    diagonal = geo.bbox_diagonal(mio.read_vertex_coordinates(original.data))
    print(f"      distance to the original surface: max {distances.max() / diagonal:.2e} of the diagonal")
    assert distances.max() / diagonal < 2e-3


@scenario
def level_ladder_builds_every_level():
    original = dense_ellipsoid()
    result = run_pipeline(original, target_quads=4000, extra_levels="2K, 1K")
    names = [level.object.name for level in result.levels]
    faces = [level.stats["faces"] for level in result.levels]
    print(f"      levels: {names} -> faces {faces}")
    assert names == ["Blob_quads_4K", "Blob_quads_2K", "Blob_quads_1K"]
    assert faces[0] > faces[1] > faces[2]
    for target, face_count in zip((4000, 2000, 1000), faces):
        assert abs(face_count - target) / target < 0.35, (target, face_count)


@scenario
def level_ladder_stays_symmetric():
    result = run_pipeline(dense_ellipsoid(), target_quads=2400, extra_levels="1200", sym_x=True)
    for level in result.levels:
        coordinates = mio.read_mesh_arrays(level.object.data).co.astype(np.float64)
        error = largest_mirror_error(coordinates, axis=0)
        print(f"      level {level.target_quads}: border_edges={level.stats['border_edges']} mirror_error={error:.2e}")
        assert level.stats["border_edges"] == 0
        assert error < 1e-4


@scenario
def zones_survive_a_dense_source():
    original = heavy_blob(subdivisions=8)
    original.data.materials.append(bpy.data.materials.new("A"))
    original.data.materials.append(bpy.data.materials.new("B"))
    for polygon in original.data.polygons:
        polygon.material_index = 1 if polygon.center.z > 0 else 0
    result = run_pipeline(original, target_quads=2000, use_materials=True)
    indices = mio.read_material_indices(result.object.data)
    print(f"      faces per material {np.bincount(indices).tolist()}, "
          f"hard edges {result.hard_edge_count}, warnings {result.warnings}")
    assert set(np.unique(indices)) == {0, 1}
    assert result.hard_edge_count > 0


@scenario
def profile_reports_stage_times_and_memory():
    result = run_pipeline(dense_ellipsoid(), target_quads=800, extra_levels="400")
    profile = result.profile
    names = [stage["name"] for stage in profile["stages"]]
    print(f"      total {profile['total_seconds']} s, peak memory {profile['peak_memory_mb']} MB, stages {names}")
    assert profile["total_seconds"] > 0
    assert "Read source" in names and any(name.startswith("Level 800: engine") for name in names)
    assert any(name.startswith("Level 400: engine") for name in names)


# --------------------------------------------------------------------------- the panel buttons

@scenario
def panel_title_shows_the_manifest_version():
    import re, os
    from quadforge import ui
    manifest = open(os.path.join(os.path.dirname(ui.__file__), "blender_manifest.toml"), encoding="utf-8").read()
    version = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.M).group(1)
    assert ui.QUADFORGE_PT_main.bl_label == f"QuadForge {version}", ui.QUADFORGE_PT_main.bl_label

@scenario
def remesh_button_works_and_stores_the_last_result():
    dense_ellipsoid()
    settings = bpy.context.scene.quadforge
    settings.target_quads = 700
    settings.extra_levels = "350"
    assert bpy.ops.quadforge.remesh() == {"FINISHED"}
    assert settings.last_has_report is True
    assert settings.last_faces > 0 and settings.last_quads > 0 and settings.last_seconds > 0
    assert len(settings.last_levels_text.split("\n")) == 2
    print(f"      stored report: {settings.last_faces} faces, {settings.last_quads} quads, "
          f"{settings.last_poles} poles, {settings.last_seconds:.1f} s, levels={settings.last_levels_text!r}")


@scenario
def preset_buttons_set_target_angle_and_levels():
    settings = bpy.context.scene.quadforge
    assert bpy.ops.quadforge.apply_preset(preset="HERO") == {"FINISHED"}
    assert settings.target_quads == 20000
    assert abs(math.degrees(settings.sharp_angle) - 30.0) < 1e-3
    assert settings.extra_levels == ""                         # presets without levels leave them alone
    assert bpy.ops.quadforge.apply_preset(preset="LOD_LADDER") == {"FINISHED"}
    assert settings.target_quads == 20000 and settings.extra_levels == "15K, 10K, 5K, 4K"


@scenario
def reset_button_restores_defaults():
    settings = bpy.context.scene.quadforge
    settings.target_quads = 123
    settings.sym_x = True
    settings.extra_levels = "1K"
    assert bpy.ops.quadforge.reset_settings() == {"FINISHED"}
    assert settings.target_quads == 4000 and settings.sym_x is False and settings.extra_levels == ""



# --------------------------------------------------------------------------- v0.4: fixes found in the first real test

@scenario
def mirrored_half_is_not_inside_out():
    result = run_pipeline(dense_ellipsoid(), target_quads=1500, sym_x=True)
    positive, negative = outward_fraction_by_half(result.object, axis=0)
    print(f"      outward-facing faces: x>0 half {positive:.1%}, x<0 half {negative:.1%}, "
          f"inconsistent edges {result.stats['inconsistent_edges']}")
    assert positive > 0.95 and negative > 0.95
    assert result.stats["inconsistent_edges"] == 0


@scenario
def seam_has_no_gaps_with_a_noisy_engine_border():
    result = run_pipeline(dense_ellipsoid(), target_quads=1500, sym_x=True, sym_tolerance=1e-6)
    print(f"      border edges {result.stats['border_edges']}, non-manifold {result.stats['non_manifold_edges']}")
    assert result.stats["border_edges"] == 0 and result.stats["non_manifold_edges"] == 0


@scenario
def snapping_does_not_collapse_quads():
    original = heavy_blob(subdivisions=7)
    result = run_pipeline(original, target_quads=1500, sym_x=True)
    arrays = mio.read_mesh_arrays(result.object.data)
    lengths = geo.edge_lengths(arrays)
    print(f"      shortest edge {lengths.min():.4f}, mean {lengths.mean():.4f}, non-manifold {result.stats['non_manifold_edges']}")
    assert lengths.min() > 0.05 * lengths.mean()
    assert result.stats["non_manifold_edges"] == 0


@scenario
def smart_detection_ignores_tiny_noise_but_keeps_the_box():
    original = subdivided_box()
    smart = run_pipeline(original, target_quads=600, smart_features=True, hide_original=False)
    simple = run_pipeline(original, target_quads=600, smart_features=False, hide_original=False)
    print(f"      hard edges: smart {smart.hard_edge_count}, simple {simple.hard_edge_count}")
    assert smart.hard_edge_count > 0 and simple.hard_edge_count > 0
    assert smart.hard_edge_count <= simple.hard_edge_count


@scenario
def shading_modes_are_applied():
    original = subdivided_box()
    auto = run_pipeline(original, target_quads=600, shading="AUTO", hide_original=False)
    flat = run_pipeline(original, target_quads=600, shading="FLAT", hide_original=False)
    smooth_auto = np.array([p.use_smooth for p in auto.object.data.polygons])
    smooth_flat = np.array([p.use_smooth for p in flat.object.data.polygons])
    sharp_auto = mio.read_edge_flag(auto.object.data, "use_edge_sharp")
    print(f"      AUTO: {smooth_auto.mean():.0%} smooth faces, {int(sharp_auto.sum())} sharp edges; FLAT: {smooth_flat.mean():.0%} smooth")
    assert smooth_auto.all() and not smooth_flat.any()
    assert sharp_auto.sum() > 0                                  # corners are crisp even with smooth faces


@scenario
def high_quality_presets_exist_and_work():
    settings = bpy.context.scene.quadforge
    assert bpy.ops.quadforge.apply_preset(preset="DETAIL_80K") == {"FINISHED"}
    assert settings.target_quads == 80000
    assert bpy.ops.quadforge.apply_preset(preset="DETAIL_120K") == {"FINISHED"}
    assert settings.target_quads == 120000


ALL_SCENARIOS = (
    panel_title_shows_the_manifest_version,
    target_count_is_respected, original_object_is_not_modified, symmetry_x_is_exact_and_closed,
    symmetry_on_three_axes, hard_edges_are_kept_on_a_box,
    smooth_surface_has_no_hard_edges_with_a_high_angle, materials_are_isolated_and_transferred,
    asymmetric_mesh_gives_a_warning, transform_is_copied_to_the_result,
    temporary_data_is_cleaned_up, empty_mesh_gives_a_clear_error,
    dense_mesh_is_remeshed_directly_and_keeps_the_detail, level_ladder_builds_every_level,
    level_ladder_stays_symmetric, zones_survive_a_dense_source, profile_reports_stage_times_and_memory,
    remesh_button_works_and_stores_the_last_result, preset_buttons_set_target_angle_and_levels,
    reset_button_restores_defaults,
    mirrored_half_is_not_inside_out, seam_has_no_gaps_with_a_noisy_engine_border,
    snapping_does_not_collapse_quads,
    smart_detection_ignores_tiny_noise_but_keeps_the_box, shading_modes_are_applied,
    high_quality_presets_exist_and_work,
)

if __name__ == "__main__":
    for run_scenario in ALL_SCENARIOS:
        run_scenario()
    failed = [name for name, passed in RESULTS if not passed]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} scenarios passed")
    sys.exit(1 if failed else 0)
