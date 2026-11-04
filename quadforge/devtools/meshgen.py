"""Synthetic test meshes. Every generator returns (co, tris, feature_edges)."""
from __future__ import annotations

import numpy as np


def icosphere(subdiv=3, radius=1.0):
    t = (1 + 5 ** 0.5) / 2
    v = np.array([[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t],
                  [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]], float)
    f = np.array([[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2],
                  [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5],
                  [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]])
    v = v / np.linalg.norm(v, axis=1, keepdims=True)
    for _ in range(subdiv):
        cache = {}
        verts = list(v)
        nf = []

        def mid(a, b):
            k = (min(a, b), max(a, b))
            if k not in cache:
                m = (verts[a] + verts[b]) / 2
                verts.append(m / np.linalg.norm(m))
                cache[k] = len(verts) - 1
            return cache[k]
        for a, b, c in f:
            ab, bc, ca = mid(a, b), mid(b, c), mid(c, a)
            nf += [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]]
        v, f = np.array(verts), np.array(nf)
    return v * radius, f, np.zeros((0, 2), int)


def torus(R=1.0, r=0.35, nu=64, nv=32):
    u = np.arange(nu) * 2 * np.pi / nu
    w = np.arange(nv) * 2 * np.pi / nv
    U, W = np.meshgrid(u, w, indexing="ij")
    x = (R + r * np.cos(W)) * np.cos(U)
    y = (R + r * np.cos(W)) * np.sin(U)
    z = r * np.sin(W)
    co = np.stack([x, y, z], -1).reshape(-1, 3)
    tris = []
    for i in range(nu):
        for j in range(nv):
            a = i * nv + j
            b = ((i + 1) % nu) * nv + j
            c = ((i + 1) % nu) * nv + (j + 1) % nv
            d = i * nv + (j + 1) % nv
            tris += [[a, b, c], [a, c, d]]
    return co, np.array(tris), np.zeros((0, 2), int)


def box(n=16, size=(1.0, 1.0, 1.0)):
    """Closed box with n x n x n subdivision; its 12 edges are returned as feature edges."""
    verts = {}
    co = []

    def vid(p):
        k = tuple(int(x) for x in p)
        if k not in verts:
            verts[k] = len(co)
            co.append([k[0] / n * size[0] - size[0] / 2, k[1] / n * size[1] - size[1] / 2, k[2] / n * size[2] - size[2] / 2])
        return verts[k]
    tris = []
    for axis in range(3):
        for side in (0, n):
            a1, a2 = [i for i in range(3) if i != axis]
            for i in range(n):
                for j in range(n):
                    def p(ii, jj):
                        q = [0, 0, 0]
                        q[axis] = side; q[a1] = ii; q[a2] = jj
                        return vid(q)
                    q00, q10, q11, q01 = p(i, j), p(i + 1, j), p(i + 1, j + 1), p(i, j + 1)
                    t = [[q00, q10, q11], [q00, q11, q01]]
                    if (side == 0) != (axis == 1):
                        t = [[a, c, b] for a, b, c in t]
                    tris += t
    co = np.array(co)
    tris = np.array(tris)
    # feature edges: edges whose two endpoints lie on the same box edge (2 coordinates at extremes)
    ext = np.isclose(np.abs(co) * 2, np.array(size))
    feats = set()
    for a, b, c in tris:
        for p, q in ((a, b), (b, c), (c, a)):
            shared = ext[p] & ext[q]
            if shared.sum() >= 2:
                feats.add((min(p, q), max(p, q)))
    return co, tris, np.array(sorted(feats), int)


def cylinder(radius=0.5, height=1.5, nseg=48, nrings=12, ncap=6):
    """Closed cylinder; the two rim circles are feature edges."""
    co = []
    tris = []
    feats = []

    def ring(z, r):
        base = len(co)
        for i in range(nseg):
            a = 2 * np.pi * i / nseg
            co.append([r * np.cos(a), r * np.sin(a), z])
        return base

    def band(r0, r1, flip):
        for i in range(nseg):
            a, b = r0 + i, r0 + (i + 1) % nseg
            c, d = r1 + (i + 1) % nseg, r1 + i
            t = [[a, b, c], [a, c, d]]
            tris.extend([[x, z_, y] for x, y, z_ in t] if flip else t)

    side = [ring(-height / 2 + height * k / nrings, radius) for k in range(nrings + 1)]
    for k in range(nrings):
        band(side[k], side[k + 1], False)
    for top in (False, True):
        z = height / 2 if top else -height / 2
        chain = [side[-1] if top else side[0]]
        for s in range(1, ncap):
            chain.append(ring(z, radius * (1 - s / ncap)))
        for s in range(len(chain) - 1):
            band(chain[s], chain[s + 1], not top)
        centre = len(co)
        co.append([0, 0, z])
        for i in range(nseg):
            a, b = chain[-1] + i, chain[-1] + (i + 1) % nseg
            tris.append([centre, a, b] if top else [centre, b, a])
        feats.extend((chain[0] + i, chain[0] + (i + 1) % nseg) for i in range(nseg))
    return np.array(co), np.array(tris), np.array(feats)


def hemisphere(subdiv=4, nseg=64, nrings=24):
    """Upper unit hemisphere with an exact, smooth equator border (UV layout)."""
    co = [[0, 0, 1.0]]
    for r in range(1, nrings + 1):
        phi = (np.pi / 2) * r / nrings
        for i in range(nseg):
            a = 2 * np.pi * i / nseg
            co.append([np.sin(phi) * np.cos(a), np.sin(phi) * np.sin(a), np.cos(phi)])
    tris = []
    for i in range(nseg):
        tris.append([0, 1 + i, 1 + (i + 1) % nseg])
    for r in range(1, nrings):
        for i in range(nseg):
            a, b = 1 + (r - 1) * nseg + i, 1 + (r - 1) * nseg + (i + 1) % nseg
            c, d = 1 + r * nseg + (i + 1) % nseg, 1 + r * nseg + i
            tris += [[a, d, c], [a, c, b]]
    return np.array(co), np.array(tris), np.zeros((0, 2), int)


def bumpy_blob(subdiv=5, seed=3):
    co, tris, _ = icosphere(subdiv)
    rng = np.random.default_rng(seed)
    freqs = rng.normal(size=(6, 3)) * 2.2
    phase = rng.uniform(0, 6.28, 6)
    r = 1 + 0.12 * sum(np.sin(co @ freqs[i] + phase[i]) for i in range(6)) / 3
    return co * r[:, None], tris, np.zeros((0, 2), int)
