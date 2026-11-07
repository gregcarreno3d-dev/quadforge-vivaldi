"""Planning helpers (pure Python, no Blender): which levels to build and how to name them.

Kept free of ``bpy`` so the rules can be unit-tested and reused by other tools
(see ``tests/test_planning.py``).

**Level ladder (LODs).** The user asks for a base target (e.g. 20,000 quads)
and optionally lower levels ("15K, 10K, 5K, 4K"). Every level is built from
the original source mesh.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MIN_TARGET_QUADS = 16                 # never ask an engine for fewer quads than this

_TOKEN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kK]?)\s*$")


@dataclass(frozen=True)
class LevelPlan:
    """The levels to build, largest first, and the text fragments that were ignored."""

    targets: tuple              # e.g. (20000, 15000, 10000, 5000, 4000)
    ignored: tuple = ()         # fragments that were not valid (shown as a warning)

    @property
    def largest(self) -> int:
        return self.targets[0]

    @property
    def has_extra_levels(self) -> bool:
        return len(self.targets) > 1


def parse_level_targets(base_target: int, extra_levels_text: str) -> LevelPlan:
    """Turn ``"15K, 10000; 5k"`` into a descending list of targets below ``base_target``.

    Accepts commas, semicolons or spaces as separators, and the ``K`` suffix
    (``4K`` = 4000, ``4.5K`` = 4500; the decimal separator is a dot). Anything invalid, duplicated, not lower than the previous
    level, or smaller than ``MIN_TARGET_QUADS`` is ignored and reported.
    """
    accepted = [int(base_target)]
    ignored = []
    for fragment in re.split(r"[,;\s]+", extra_levels_text.strip()):
        if not fragment:
            continue
        match = _TOKEN.match(fragment)
        if match is None:
            ignored.append(fragment)
            continue
        number = float(match.group(1))
        value = int(round(number * 1000)) if match.group(2) else int(round(number))
        if value < MIN_TARGET_QUADS or value >= base_target or value in accepted:
            ignored.append(fragment)
            continue
        accepted.append(value)
    levels = [int(base_target)] + sorted(accepted[1:], reverse=True)
    return LevelPlan(tuple(levels), tuple(ignored))


def format_target(target: int) -> str:
    """20000 -> '20K', 4500 -> '4.5K', 750 -> '750' (used in object names)."""
    if target >= 1000:
        thousands = target / 1000.0
        return f"{thousands:g}K"
    return str(target)
