"""Exchange format between Blender and the Forge engine (.fmesh, little endian).

Layout (see engine/src/meshio.hpp):
    "FMSH", u32 version, u32 nV, nPoly, nIdx, nFeat, flags,
    f32 positions[3 nV], i32 polygon sizes, i32 polygon indices, i32 feature edges[2 nFeat],
    f32 density[nV] (only if flags & 1)
    u32 nGuide, f32 guides[6 nGuide] (only if flags & 4): flow guide segments
No Blender imports here, so it can be tested anywhere.
"""
from __future__ import annotations

import struct

import numpy as np

MAGIC = b"FMSH"
VERSION = 1


def write_fmesh(path, co, sizes, idx, features=None, density=None, guides=None):
    co = np.ascontiguousarray(co, dtype=np.float32).reshape(-1, 3)
    sizes = np.ascontiguousarray(sizes, dtype=np.int32).reshape(-1)
    idx = np.ascontiguousarray(idx, dtype=np.int32).reshape(-1)
    feats = np.zeros((0, 2), np.int32) if features is None else np.ascontiguousarray(features, dtype=np.int32).reshape(-1, 2)
    flags = 0
    if density is not None:
        density = np.ascontiguousarray(density, dtype=np.float32).reshape(-1)
        assert density.shape[0] == co.shape[0]
        flags |= 1
    guide_array = None
    if guides is not None and len(guides) > 0:
        guide_array = np.ascontiguousarray(guides, dtype=np.float32).reshape(-1, 6)   # x0 y0 z0 x1 y1 z1 per segment
        flags |= 4
    with open(path, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<6I", VERSION, co.shape[0], sizes.shape[0], idx.shape[0], feats.shape[0], flags))
        f.write(co.tobytes())
        f.write(sizes.tobytes())
        f.write(idx.tobytes())
        f.write(feats.tobytes())
        if flags & 1:
            f.write(density.tobytes())
        if flags & 4:
            f.write(struct.pack("<I", guide_array.shape[0]))
            f.write(guide_array.tobytes())


def read_fmesh(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:4] == MAGIC, "not an FMSH file"
    version, nv, npoly, nidx, nfeat, flags = struct.unpack_from("<6I", data, 4)
    assert version == VERSION
    at = 28
    co = np.frombuffer(data, np.float32, nv * 3, at).reshape(nv, 3).copy(); at += nv * 12
    sizes = np.frombuffer(data, np.int32, npoly, at).copy(); at += npoly * 4
    idx = np.frombuffer(data, np.int32, nidx, at).copy(); at += nidx * 4
    feats = np.frombuffer(data, np.int32, nfeat * 2, at).reshape(nfeat, 2).copy(); at += nfeat * 8
    density = None
    if flags & 1:
        density = np.frombuffer(data, np.float32, nv, at).copy()
    return co, sizes, idx, feats, density
