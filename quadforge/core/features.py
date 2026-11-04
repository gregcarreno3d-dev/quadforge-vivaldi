"""Smart hard-edge detection (pure NumPy, no Blender).

A plain "edge angle > threshold" rule has two weaknesses on real models:

  * Noise. Tiny bumps create many short, isolated "hard" edges that the quad mesh can
    never follow, and they only add constraints that disturb the edge flow.
  * Scale blindness. A feature shorter than about two quads of the TARGET mesh cannot
    be represented, however sharp it is.

So the detector works on CHAINS of connected hard edges and keeps a chain only if:

  1. it contains at least one edge sharper than the user's angle (the "strong" edge), and
  2. it is long enough for the target mesh: at least ``min_chain_quads`` quad edge lengths, or
  3. (option) it is a short run of near-right-angle edges (90 degrees, like the rim of a peg
     or a box corner), which are kept down to one quad length because they define the shape.

The prototype of this rule was tuned on a real model (a 37K-triangle bull ant): with a
20K-quad target the chain filter dropped 55% of the chains but only ~7% of the edges, so
real creases are untouched while isolated noise disappears.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import geometry as geo

DEFAULT_MIN_CHAIN_QUADS = 2.0           # chains shorter than this many quad lengths are noise
RIGHT_ANGLE_DEGREES = 90.0
RIGHT_ANGLE_TOLERANCE_DEGREES = 20.0    # 70 to 110 degrees counts as a right angle
RIGHT_ANGLE_MIN_CHAIN_QUADS = 1.0       # right-angle chains are kept down to one quad length
RIGHT_ANGLE_FRACTION = 0.5              # at least half of the chain must be right angles
MIN_LOW_ANGLE_DEGREES = 8.0             # lower limit for the optional weak-edge threshold


@dataclass(frozen=True)
class FeatureResult:
    mask: np.ndarray            # (E,) bool, True for edges kept as hard edges
    strong_edges: int           # edges sharper than the angle, before any filtering
    kept_edges: int
    chains_total: int
    chains_kept: int


def edge_angles(m: geo.MeshArrays, normals: np.ndarray | None = None) -> np.ndarray:
    """Dihedral angle in degrees for every edge shared by exactly two faces (0 elsewhere)."""
    first, second, counts = geo.edge_face_pairs(m)
    angles = np.zeros(m.n_edges)
    two = np.flatnonzero(counts == 2)
    if two.size:
        if normals is None:
            normals, _ = geo.polygon_normals_areas(m)
        cosine = np.einsum("ij,ij->i", normals[first[two]], normals[second[two]])
        angles[two] = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
    return angles


def detect_hard_edges(m: geo.MeshArrays, angle_deg: float, quad_length: float, *,
                      min_chain_quads: float = DEFAULT_MIN_CHAIN_QUADS,
                      keep_right_angles: bool = True,
                      low_ratio: float = 1.0,
                      normals: np.ndarray | None = None) -> FeatureResult:
    """Find the hard edges worth keeping for a target mesh whose quads are ``quad_length`` long.

    ``low_ratio`` below 1.0 turns on hysteresis: weaker edges (``low_ratio * angle_deg``)
    join a chain that already contains a strong edge. It is off (1.0) by default because on
    the test model it nearly doubled the number of constraints.
    """
    angles = edge_angles(m, normals)
    mask = np.zeros(m.n_edges, dtype=bool)
    strong = angles > angle_deg
    strong_count = int(strong.sum())
    if strong_count == 0:
        return FeatureResult(mask, 0, 0, 0, 0)

    low_angle = angle_deg if low_ratio >= 1.0 else max(MIN_LOW_ANGLE_DEGREES, low_ratio * angle_deg)
    candidates = np.flatnonzero(angles > low_angle)

    labels = geo.connected_labels(m.n_verts, m.edges[candidates, 0], m.edges[candidates, 1])
    _, chain_of_candidate = np.unique(labels[m.edges[candidates, 0]], return_inverse=True)
    chain_of_candidate = chain_of_candidate.reshape(-1)
    chain_count = int(chain_of_candidate.max()) + 1

    lengths = geo.edge_lengths(m)[candidates]
    chain_length = np.bincount(chain_of_candidate, weights=lengths, minlength=chain_count)
    chain_strong = np.bincount(chain_of_candidate, weights=strong[candidates].astype(float), minlength=chain_count)
    chain_size = np.bincount(chain_of_candidate, minlength=chain_count)

    keep = (chain_strong > 0) & (chain_length >= min_chain_quads * quad_length)
    if keep_right_angles:
        near_right = np.abs(angles[candidates] - RIGHT_ANGLE_DEGREES) <= RIGHT_ANGLE_TOLERANCE_DEGREES
        right_fraction = np.bincount(chain_of_candidate, weights=near_right.astype(float),
                                     minlength=chain_count) / np.maximum(chain_size, 1)
        keep |= ((chain_strong > 0) & (right_fraction >= RIGHT_ANGLE_FRACTION)
                 & (chain_length >= RIGHT_ANGLE_MIN_CHAIN_QUADS * quad_length))

    mask[candidates[keep[chain_of_candidate]]] = True
    return FeatureResult(mask, strong_count, int(mask.sum()), chain_count, int(keep.sum()))


def feature_segments(m: geo.MeshArrays, edge_mask: np.ndarray):
    """Start and end points ``((S, 3), (S, 3))`` of the marked edges, to snap vertices onto them later."""
    marked = m.edges[np.asarray(edge_mask, dtype=bool)]
    co = m.co.astype(np.float64, copy=False)
    return co[marked[:, 0]], co[marked[:, 1]]
