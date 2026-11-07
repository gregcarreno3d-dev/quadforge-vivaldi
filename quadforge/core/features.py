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


# --------------------------------------------------------------------------
# Soft bevels: rounded edges made of several facets that are each below the sharp angle
# --------------------------------------------------------------------------

SOFT_FACET_ANGLE_DEGREES = 4.0      # a dihedral above this separates two facets of a bevel; below it faces are one flat patch
SOFT_BAND_NARROW = 1.0              # patches narrower than this many quad lengths are facets of a bevel band
SOFT_BAND_MIN_WIDTH = 1.2           # a band must be at least this many quad lengths wide to carry two edge loops
SOFT_BAND_MAX_TURN = 150.0          # degrees: a band turning the surface right back on itself is a closed curved surface
SOFT_BAND_MAX_WIDTH = 5.0           # wider than this many quad lengths it is a curved surface, not a bevel (a sphere is one big band)
SOFT_PLANAR_DEVIATION_DEGREES = 3.0  # a patch is a true plane when no face leans more than this from its average normal
SOFT_PLANAR_MIN_WIDTH = 2.0         # planes narrower than this many quad lengths do not count
SOFT_MIN_PLANAR_FRACTION = 0.15     # share of the surface that must be planar before bevels are looked for (organic shapes have none)
SOFT_MIN_CHAIN_QUADS = 3.0          # boundary chains shorter than this many quad lengths are noise


def detect_soft_bevels(m: geo.MeshArrays, angle_deg: float, quad_length: float, *,
                       normals: np.ndarray | None = None, areas: np.ndarray | None = None,
                       band_vertices: bool = False):
    """Edges that bound a soft bevel (a fillet or rounded chamfer whose facets are each flatter than ``angle_deg``).

    A fillet of four facets turning 90 degrees shows no single edge above the sharp angle, so the normal detector
    sees nothing and the remesh lets quads cut across it. Here the faces are grouped into flat patches (faces joined
    by dihedrals under a few degrees). The facets of a bevel are narrow patches chained together between two wide
    patches; when those two wide patches differ by at least ``angle_deg`` the band is a bevel, and the edges between
    the band and the wide patches are returned, so the quad rows run along both sides of it. Bands that are too
    narrow for two edge loops at this quad size are left alone. Returns an (E,) bool mask; with ``band_vertices`` a
    tuple ``(mask, (V,) bool mask of the vertices lying on the kept bands)``.
    """
    mask = np.zeros(m.n_edges, dtype=bool)
    nothing = (mask, np.zeros(m.n_verts, dtype=bool)) if band_vertices else mask
    if m.n_polys == 0 or quad_length <= 0.0:
        return nothing
    if normals is None or areas is None:
        normals, areas = geo.polygon_normals_areas(m)
    first, second, counts = geo.edge_face_pairs(m)
    angles = edge_angles(m, normals)
    interior = counts == 2
    flat = interior & (angles <= SOFT_FACET_ANGLE_DEGREES)
    soft = interior & (angles > SOFT_FACET_ANGLE_DEGREES) & (angles <= angle_deg)
    if not soft.any():
        return nothing

    patch = geo.connected_labels(m.n_polys, first[flat], second[flat])
    _, patch = np.unique(patch, return_inverse=True)
    patch = patch.reshape(-1)
    n_patch = int(patch.max()) + 1
    lengths = geo.edge_lengths(m)
    patch_area = np.bincount(patch, weights=areas, minlength=n_patch)
    # perimeter of a patch: every edge that is not interior to it (borders, hard and soft edges)
    inside = np.zeros(m.n_edges, dtype=bool)
    inside[interior] = patch[first[interior]] == patch[second[interior]]
    outer = np.flatnonzero(~inside & (counts >= 1))
    perimeter = np.bincount(patch[first[outer]], weights=lengths[outer], minlength=n_patch)
    two_sided = outer[counts[outer] == 2]
    perimeter += np.bincount(patch[second[two_sided]], weights=lengths[two_sided], minlength=n_patch)
    width = 2.0 * patch_area / np.maximum(perimeter, 1e-30)
    narrow = width < SOFT_BAND_NARROW * quad_length

    # Organic shapes (faces, creatures) chain their gentle dihedrals into drifting 'flat' patches and show bands
    # everywhere; a mechanical part has real planes. Only look for bevels when enough of the surface is truly planar.
    drift_normal = np.zeros((n_patch, 3))
    np.add.at(drift_normal, patch, normals * areas[:, None])
    drift_normal /= np.maximum(np.linalg.norm(drift_normal, axis=1), 1e-30)[:, None]
    face_cosine = np.einsum("ij,ij->i", normals, drift_normal[patch])
    worst_cosine = np.ones(n_patch)
    np.minimum.at(worst_cosine, patch, face_cosine)
    planar = (worst_cosine >= np.cos(np.radians(SOFT_PLANAR_DEVIATION_DEGREES))) & (width >= SOFT_PLANAR_MIN_WIDTH * quad_length)
    if patch_area[planar].sum() < SOFT_MIN_PLANAR_FRACTION * max(float(areas.sum()), 1e-30):
        return nothing

    soft_edges = np.flatnonzero(soft)
    pa, pb = patch[first[soft_edges]], patch[second[soft_edges]]
    both_narrow = narrow[pa] & narrow[pb]
    band_of_patch = geo.connected_labels(n_patch, pa[both_narrow], pb[both_narrow])
    # boundary edges: soft edges between a narrow patch and a wide one
    edge_narrow_side = narrow[pa] != narrow[pb]
    boundary = soft_edges[edge_narrow_side]
    if boundary.size == 0:
        return nothing
    narrow_patch = np.where(narrow[pa[edge_narrow_side]], pa[edge_narrow_side], pb[edge_narrow_side])
    wide_patch = np.where(narrow[pa[edge_narrow_side]], pb[edge_narrow_side], pa[edge_narrow_side])
    band = band_of_patch[narrow_patch]

    patch_normal = np.zeros((n_patch, 3))
    np.add.at(patch_normal, patch, normals * areas[:, None])
    patch_normal /= np.maximum(np.linalg.norm(patch_normal, axis=1), 1e-30)[:, None]

    keep_band = {}
    for band_id in np.unique(band):
        select = band == band_id
        wides = np.unique(wide_patch[select])
        if wides.size < 2:
            continue
        cosine = patch_normal[wides] @ patch_normal[wides].T
        turn = np.degrees(np.arccos(np.clip(cosine.min(), -1.0, 1.0)))
        if turn < angle_deg or turn > SOFT_BAND_MAX_TURN:
            continue
        band_patches = np.flatnonzero(narrow & (band_of_patch == band_id))
        band_area = patch_area[band_patches].sum()
        boundary_length = lengths[boundary[select]].sum()
        band_width = 2.0 * band_area / max(boundary_length, 1e-30)
        if band_width < SOFT_BAND_MIN_WIDTH * quad_length or band_width > SOFT_BAND_MAX_WIDTH * quad_length:
            continue
        keep_band[band_id] = True
    if not keep_band:
        return nothing
    chosen = boundary[np.isin(band, list(keep_band))]
    # drop short, isolated pieces
    labels = geo.connected_labels(m.n_verts, m.edges[chosen, 0], m.edges[chosen, 1])
    _, chain = np.unique(labels[m.edges[chosen, 0]], return_inverse=True)
    chain = chain.reshape(-1)
    chain_length = np.bincount(chain, weights=lengths[chosen])
    chosen = chosen[chain_length[chain] >= SOFT_MIN_CHAIN_QUADS * quad_length]
    mask[chosen] = True
    if not band_vertices:
        return mask
    if not chosen.size:
        return nothing
    survivors = np.zeros(m.n_edges, dtype=bool)
    survivors[chosen] = True
    kept_bands = np.unique(band[survivors[boundary]])         # only bands that still have a kept border
    band_patch_ids = np.flatnonzero(narrow & np.isin(band_of_patch, kept_bands))
    face_in_band = np.isin(patch, band_patch_ids)
    on_band = np.zeros(m.n_verts, dtype=bool)
    on_band[m.loop_verts[face_in_band[m.loop_poly]]] = True
    return mask, on_band
