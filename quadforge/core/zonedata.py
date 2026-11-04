"""Carry the sculpt polygroups (Face Sets) and the sculpt mask of the original onto a remeshed result.

The engine rebuilds the mesh, so per-face and per-vertex sculpt data is gone afterwards. Face Sets are copied
from the nearest original face, the mask is interpolated from the nearest original vertices, so the result can
go straight back into Sculpt Mode with the same groups.
"""
from __future__ import annotations

import numpy as np

from . import geometry as geo
from . import mesh_io as mio

FACE_SET_ATTRIBUTE = ".sculpt_face_set"
MASK_ATTRIBUTE = ".sculpt_mask"


def has_sculpt_data(mesh) -> bool:
    return mesh.attributes.get(FACE_SET_ATTRIBUTE) is not None or mesh.attributes.get(MASK_ATTRIBUTE) is not None


def copy_sculpt_data(source, target) -> int:
    """Copy Face Sets and mask from ``source`` onto ``target``. Returns how many of the two were copied."""
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    from mathutils.kdtree import KDTree

    reference = mio.read_mesh_arrays(source)
    face_sets = mio.read_attribute(source, FACE_SET_ATTRIBUTE, reference.n_polys)
    mask = mio.read_attribute(source, MASK_ATTRIBUTE, len(source.vertices))
    if (face_sets is None and mask is None) or len(target.polygons) == 0:
        return 0
    result = mio.read_mesh_arrays(target)
    copied = 0

    if face_sets is not None:
        flat, offsets = reference.loop_verts.tolist(), reference.poly_offsets.tolist()
        polygons = [flat[offsets[i]:offsets[i + 1]] for i in range(reference.n_polys)]
        tree = BVHTree.FromPolygons(reference.co.tolist(), polygons)
        values = np.zeros(result.n_polys, dtype=np.int32)
        for index, centre in enumerate(geo.polygon_centroids(result)):
            hit = tree.find_nearest(Vector(centre))
            if hit[2] is not None:
                values[index] = face_sets[hit[2]]
        mio.write_int_face_attribute(target, FACE_SET_ATTRIBUTE, values)
        copied += 1

    if mask is not None:
        kd = KDTree(len(reference.co))
        for index, co in enumerate(reference.co.tolist()):
            kd.insert(Vector(co), index)
        kd.balance()
        values = np.zeros(len(result.co), dtype=np.float32)
        for index, co in enumerate(result.co.tolist()):
            near = kd.find_n(Vector(co), 3)
            weights = np.array([1.0 / (distance + 1e-9) for _p, _i, distance in near])
            values[index] = float((weights * np.array([mask[i] for _p, i, _d in near])).sum() / weights.sum())
        attribute = target.attributes.get(MASK_ATTRIBUTE)
        if attribute is None or attribute.domain != "POINT" or attribute.data_type != "FLOAT":
            if attribute is not None:
                target.attributes.remove(attribute)
            attribute = target.attributes.new(name=MASK_ATTRIBUTE, type="FLOAT", domain="POINT")
        attribute.data.foreach_set("value", values)
        copied += 1
    target.update()
    return copied
