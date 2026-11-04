"""Unit tests for planning.py (level ladder and working-copy size). No Blender needed:

    python tests/test_planning.py
"""
import importlib.util
import pathlib
import sys

_path = pathlib.Path(__file__).resolve().parent.parent / "planning.py"
_spec = importlib.util.spec_from_file_location("qf_planning", _path)
planning = importlib.util.module_from_spec(_spec)
sys.modules["qf_planning"] = planning
_spec.loader.exec_module(planning)


def test_no_extra_levels_gives_only_the_base_target():
    plan = planning.parse_level_targets(20000, "")
    assert plan.targets == (20000,) and plan.ignored == () and not plan.has_extra_levels


def test_ladder_with_k_suffix_is_sorted_descending():
    plan = planning.parse_level_targets(20000, "4K, 15K, 10K, 5K")
    assert plan.targets == (20000, 15000, 10000, 5000, 4000)
    assert plan.has_extra_levels and plan.largest == 20000


def test_separators_and_plain_numbers():
    plan = planning.parse_level_targets(20000, "15000; 10000  5000,4000")
    assert plan.targets == (20000, 15000, 10000, 5000, 4000)


def test_decimal_k_values():
    assert planning.parse_level_targets(20000, "4.5K").targets == (20000, 4500)


def test_invalid_duplicate_and_too_high_levels_are_ignored():
    plan = planning.parse_level_targets(10000, "abc, 10K, 12000, 5K, 5000, 8, 3k")
    assert plan.targets == (10000, 5000, 3000)
    assert set(plan.ignored) == {"abc", "10K", "12000", "5000", "8"}


def test_proxy_size_auto_follows_four_times_the_target_with_limits():
    assert planning.choose_proxy_faces(20000, True, 1) == 80000
    assert planning.choose_proxy_faces(2000, True, 1) == planning.PROXY_MIN_FACES
    assert planning.choose_proxy_faces(500000, True, 1) == planning.PROXY_MAX_FACES


def test_proxy_size_manual_is_respected():
    assert planning.choose_proxy_faces(20000, False, 123456) == 123456


def test_needs_proxy_only_for_clearly_heavier_sources():
    assert planning.needs_proxy(3_000_000, 80_000)
    assert not planning.needs_proxy(100_000, 80_000)          # less than 1.5 x
    assert not planning.needs_proxy(3_000_000, 80_000, use_proxy=False)


def test_format_target_for_object_names():
    assert planning.format_target(20000) == "20K"
    assert planning.format_target(4000) == "4K"
    assert planning.format_target(4500) == "4.5K"
    assert planning.format_target(750) == "750"


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
