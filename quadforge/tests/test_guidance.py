"""Unit tests for guidance.py (the live advice). They do not need Blender:

    python tests/test_guidance.py
"""
import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# guidance.py does "from . import planning, texts": give it a tiny fake package so it loads standalone.
package = types.ModuleType("qf_pkg")
package.__path__ = [str(ROOT)]
sys.modules["qf_pkg"] = package
texts = _load("qf_pkg.texts", "texts.py")
planning = _load("qf_pkg.planning", "planning.py")
package.texts = texts
package.planning = planning
guidance = _load("qf_pkg.guidance", "guidance.py")

Snapshot = guidance.ObjectSnapshot
Options = guidance.GuidanceOptions


def healthy_snapshot(**changes):
    values = dict(exists=True, name="Dragon", object_type="MESH", mode="OBJECT", face_count=200_000,
                  bounds_min=(-1.0, -1.0, -1.0), bounds_max=(1.0, 1.0, 1.0))
    values.update(changes)
    return Snapshot(**values)


def templates(messages):
    return [message.template for message in messages]


def test_no_object_blocks_the_button():
    messages = guidance.build_guidance(Snapshot(), Options())
    assert templates(messages) == [texts.GUIDE_SELECT_MESH]
    assert not guidance.can_remesh(messages)


def test_non_mesh_object_blocks_the_button():
    messages = guidance.build_guidance(Snapshot(exists=True, name="Cam", object_type="CAMERA"), Options())
    assert messages[0].template == texts.GUIDE_NOT_A_MESH
    assert messages[0].values["kind"] == "camera"
    assert not guidance.can_remesh(messages)


def test_empty_mesh_blocks_the_button():
    messages = guidance.build_guidance(healthy_snapshot(face_count=0), Options())
    assert templates(messages) == [texts.GUIDE_NO_FACES]
    assert not guidance.can_remesh(messages)


def test_healthy_mesh_is_ready():
    messages = guidance.build_guidance(healthy_snapshot(), Options(target_quads=4000))
    assert messages[0].level == guidance.OK and messages[0].template == texts.GUIDE_READY
    assert messages[0].values["faces"] == "200,000" and messages[0].values["target"] == "4,000"
    assert guidance.can_remesh(messages)
    assert templates(messages) == [texts.GUIDE_READY, texts.GUIDE_USES_PROXY]   # 200K > 1.5 x 80K


def test_light_mesh_does_not_mention_the_working_copy():
    messages = guidance.build_guidance(healthy_snapshot(face_count=50_000), Options(target_quads=4000))
    assert templates(messages) == [texts.GUIDE_READY]


def test_target_not_lower_than_faces_warns():
    messages = guidance.build_guidance(healthy_snapshot(face_count=3000), Options(target_quads=4000))
    assert texts.GUIDE_TARGET_NOT_LOWER in templates(messages)
    assert guidance.can_remesh(messages)                # advice only, never blocks


def test_target_is_compared_with_quad_equivalent_not_triangle_count():
    # 37,630 triangles are only ~18,815 quads: a 20,000-quad target would NOT simplify the mesh.
    triangles = healthy_snapshot(face_count=37_630, quad_equivalent=18_815)
    assert texts.GUIDE_TARGET_NOT_LOWER in templates(guidance.build_guidance(triangles, Options(target_quads=20_000)))
    assert texts.GUIDE_TARGET_NOT_LOWER not in templates(guidance.build_guidance(triangles, Options(target_quads=10_000)))


def test_heavy_mesh_without_working_copy_warns():
    messages = guidance.build_guidance(healthy_snapshot(face_count=2_000_000), Options(use_proxy=False))
    assert texts.GUIDE_HEAVY_MESH in templates(messages)
    assert texts.GUIDE_USES_PROXY not in templates(messages)


def test_heavy_mesh_with_working_copy_is_explained_not_warned():
    messages = guidance.build_guidance(healthy_snapshot(face_count=3_000_000), Options(use_proxy=True))
    found = templates(messages)
    assert texts.GUIDE_USES_PROXY in found and texts.GUIDE_HEAVY_MESH not in found
    info = [m for m in messages if m.template == texts.GUIDE_USES_PROXY][0]
    assert info.values["faces"] == "3,000,000" and info.values["proxy"] == "80,000"


def test_small_manual_working_copy_warns():
    options = Options(target_quads=20_000, use_proxy=True, proxy_is_auto=False, proxy_faces=40_000)
    found = templates(guidance.build_guidance(healthy_snapshot(face_count=3_000_000), options))
    assert texts.GUIDE_PROXY_SMALL in found
    auto = Options(target_quads=20_000, use_proxy=True, proxy_is_auto=True, proxy_faces=80_000)
    assert texts.GUIDE_PROXY_SMALL not in templates(guidance.build_guidance(healthy_snapshot(), auto))


def test_ignored_levels_are_reported():
    options = Options(ignored_levels=("abc", "99999"))
    messages = guidance.build_guidance(healthy_snapshot(face_count=50_000), options)
    warning = [m for m in messages if m.template == texts.GUIDE_LEVELS_IGNORED][0]
    assert warning.values["items"] == "abc, 99999"


def test_confirmation_thresholds():
    assert not guidance.needs_confirmation(3_000_000, use_proxy=True)
    assert guidance.needs_confirmation(7_000_000, use_proxy=True)
    assert guidance.needs_confirmation(2_000_000, use_proxy=False)
    assert not guidance.needs_confirmation(1_000_000, use_proxy=False)


def test_mode_and_modifiers_are_explained():
    messages = guidance.build_guidance(healthy_snapshot(mode="SCULPT", active_modifier_count=2), Options())
    assert texts.GUIDE_LEAVES_MODE in templates(messages)
    assert texts.GUIDE_MODIFIERS in templates(messages)


def test_zone_options_without_data_warn():
    options = Options(use_materials=True, use_face_sets=True, use_mask=True)
    found = templates(guidance.build_guidance(healthy_snapshot(material_slot_count=1), options))
    assert texts.GUIDE_NO_MATERIAL_ZONES in found
    assert texts.GUIDE_NO_FACE_SETS in found
    assert texts.GUIDE_NO_MASK in found


def test_zone_options_with_data_do_not_warn():
    options = Options(use_materials=True, use_face_sets=True, use_mask=True)
    snapshot = healthy_snapshot(material_slot_count=3, has_face_sets=True, has_sculpt_mask=True)
    found = templates(guidance.build_guidance(snapshot, options))
    assert not {texts.GUIDE_NO_MATERIAL_ZONES, texts.GUIDE_NO_FACE_SETS, texts.GUIDE_NO_MASK} & set(found)


def test_off_centre_origin_warns_for_symmetry_axis():
    snapshot = healthy_snapshot(bounds_min=(0.5, -1.0, -1.0), bounds_max=(2.5, 1.0, 1.0))  # X is off-centre
    messages = guidance.build_guidance(snapshot, Options(symmetry_axes=(0,)))
    off_centre = [m for m in messages if m.template == texts.GUIDE_OFF_CENTER]
    assert len(off_centre) == 1 and off_centre[0].values["axis"] == "X"


def test_centred_origin_does_not_warn():
    messages = guidance.build_guidance(healthy_snapshot(), Options(symmetry_axes=(0, 1, 2)))
    assert texts.GUIDE_OFF_CENTER not in templates(messages)


def test_bounding_box_centre_mode_never_warns_about_origin():
    snapshot = healthy_snapshot(bounds_min=(0.5, -1.0, -1.0), bounds_max=(2.5, 1.0, 1.0))
    messages = guidance.build_guidance(snapshot, Options(symmetry_axes=(0,), symmetry_plane_at_origin=False))
    assert texts.GUIDE_OFF_CENTER not in templates(messages)


def test_snapshot_from_fake_mesh_object():
    class FakeAttributes:
        def __init__(self, names):
            self._names = names

        def get(self, name):
            return object() if name in self._names else None

    fake_object = types.SimpleNamespace(
        type="MESH", name="Rock", mode="OBJECT",
        data=types.SimpleNamespace(polygons=[0] * 10, loops=[0] * 30, attributes=FakeAttributes({".sculpt_mask"})),
        modifiers=[types.SimpleNamespace(show_viewport=True), types.SimpleNamespace(show_viewport=False)],
        material_slots=[1, 2],
        bound_box=[(-1, -2, -3), (1, 2, 3)] * 4,
    )
    snapshot = guidance.snapshot_from_object(fake_object)
    assert snapshot.face_count == 10 and snapshot.active_modifier_count == 1
    assert snapshot.quad_equivalent == 5          # ten triangles are five quads
    assert snapshot.material_slot_count == 2
    assert snapshot.has_sculpt_mask and not snapshot.has_face_sets
    assert snapshot.bounds_min == (-1, -2, -3) and snapshot.bounds_max == (1, 2, 3)
    assert guidance.snapshot_from_object(None).exists is False


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
