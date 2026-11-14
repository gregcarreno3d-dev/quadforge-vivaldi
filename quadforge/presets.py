"""Quick-start presets: one click sets a sensible target count and hard-edge angle.

Presets set Detail on Curves (Off except the face preset) but never touch symmetry or zone options, because those depend on the
specific model, not on the kind of asset. A preset only changes the extra
levels when it defines them (``extra_levels`` is ``None`` = leave as is).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Preset:
    label: str
    description: str
    target_quads: int
    sharp_angle_degrees: float
    extra_levels: Optional[str] = None
    curvature_detail: str = "OFF"      # OFF | LOW | MEDIUM | HIGH (see 'Detail on Curves')


PRESETS = {
    "PROP": Preset(
        label="Prop 2K",
        description="Small prop or background object. Low quad count, clear silhouette.",
        target_quads=2000,
        sharp_angle_degrees=40.0,
    ),
    "CHARACTER": Preset(
        label="Character 8K",
        description="Character or creature body for animation and deformation.",
        target_quads=8000,
        sharp_angle_degrees=35.0,
    ),
    "FACE": Preset(
        label="Face 20K",
        description="Heads and faces: more faces on eyelids, nostrils, lips and ear rims (Detail on Curves = Medium).",
        target_quads=20000,
        sharp_angle_degrees=35.0,
        curvature_detail="MEDIUM",
    ),
    "HARD_SURFACE": Preset(
        label="Hard Surface 6K",
        description="Mechanical or architectural shapes. A lower angle keeps more corners.",
        target_quads=6000,
        sharp_angle_degrees=25.0,
    ),
    "HERO": Preset(
        label="Hero 20K",
        description="Close-up hero asset with more detail.",
        target_quads=20000,
        sharp_angle_degrees=30.0,
    ),
    "DETAIL_80K": Preset(
        label="Detail 80K",
        description="High quality: keeps much more of the sculpt's detail and corners. Slower and heavier.",
        target_quads=80000,
        sharp_angle_degrees=30.0,
    ),
    "DETAIL_120K": Preset(
        label="Detail 120K",
        description="Maximum quality: for close-ups and very detailed sculpts. Needs more time and memory.",
        target_quads=120000,
        sharp_angle_degrees=30.0,
    ),
    "LOD_LADDER": Preset(
        label="Ladder 20K to 4K",
        description="Builds 20K, 15K, 10K, 5K and 4K in one go. Each level comes from the previous one.",
        target_quads=20000,
        sharp_angle_degrees=30.0,
        extra_levels="15K, 10K, 5K, 4K",
    ),
}


# ---------------------------------------------------------------- surface type and target list

@dataclass(frozen=True)
class SurfaceProfile:
    label: str
    description: str
    values: dict          # property name -> value (angles in degrees as ``sharp_angle_degrees``)


# What the two buttons set. Symmetry, zones, density, output and engine are never touched: they depend on the
# model and on what the user wants to make, not on the kind of surface.
SURFACE_PROFILES = {
    "HARD": SurfaceProfile(
        label="Hard Surface",
        description="Mechanical, architectural or armour shapes. Keeps corners and creases (hard edges from 30 degrees, "
                    "right angles and edge snapping on) and uses even faces, without Detail on Curves. The result is shaded Flat, "
                    "so no smoothing changes the look of the surface.",
        values={"use_sharp_angle": True, "sharp_angle_degrees": 30.0, "smart_features": True,
                "keep_right_angles": True, "curvature_detail": "OFF", "use_surface_analysis": True, "shading": "FLAT"},
    ),
    "ORGANIC": SurfaceProfile(
        label="Soft Surface / Organic",
        description="Characters, creatures, faces and sculpts. Hard-edge detection off (your own sharp edges and seams "
                    "are still kept) and Detail on Curves = Medium, for more faces on eyelids, lips and ear rims. The result is "
                    "shaded Smooth.",
        values={"use_sharp_angle": False, "sharp_angle_degrees": 35.0, "smart_features": True,
                "keep_right_angles": True, "curvature_detail": "MEDIUM", "use_surface_analysis": False, "shading": "SMOOTH"},
    ),
}

# Face counts offered in the Target Faces list, from small to very large. Above 150K it gets slow and heavy.
TARGET_CHOICES = (2000, 3000, 4000, 5000, 6000, 8000, 10000, 15000, 20000, 30000, 40000, 50000, 60000,
                  80000, 100000, 120000, 150000, 200000, 300000, 500000)
HEAVY_TARGET = 150000


def format_target(value: int) -> str:
    """2000 -> '2K', 1500 -> '1,500'."""
    return f"{value // 1000}K" if value % 1000 == 0 else f"{value:,}"
