"""Global quad extraction (the default quad method on closed surfaces without marked edges).

Checks validity (closed, manifold, consistent) and the size of the result on several shapes,
and that borders / marked edges / density groups select the pairing path instead.
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import meshgen as g
from harness import *

fails = []


def check(cond, msg, extra=""):
    print(("  ok   " if cond else "  FAIL ") + msg + (f"  [{extra}]" if extra != "" else ""))
    if not cond:
        fails.append(msg)


def run(name, shape, faces, expect_method, **kw):
    print(f"\n== {name}: {faces} quads")
    r = shape
    co, tris = np.asarray(r[0], float), np.asarray(r[1])
    feats = r[2] if len(r) > 2 and kw.pop("use_features", False) else None
    res, info = run_engine(co, tris, feats=feats, faces=faces, mode="quad", check=False, timeout=120, **kw)
    check(res is not None, "engine finished", info.get("error", ""))
    if res is None:
        return
    S = info["S"]
    check(S.get("method") == expect_method, f"method is {expect_method}", S.get("method"))
    c = independent_checks(*res)
    check(abs(S["faces"] - faces) <= 0.08 * faces + 8, "face count within 8%", f"{S['faces']} vs {faces}")
    check(c["nonmanifold_edges"] == 0 and c["same_direction_edges"] == 0 and not c["nan"] and c["degenerate_faces"] == 0,
          "manifold, consistent", c)
    if expect_method == "extract" and name.startswith("hemisphere"):
        check(c["boundary_edges"] > 0 and c["euler"] == 1, "open border kept as one loop (disc)", c)
    elif expect_method == "extract":
        check(c["boundary_edges"] == 0, "closed surface (no holes)", c["boundary_edges"])
        check(c["euler"] in (2, 0, -2, -4), "Euler characteristic of a closed surface", c["euler"])
    if feats is not None and kw.get("box_check"):
        pass
    v = S["valence"]
    print("  poles/1k:", round(1000 * (sum(v) - v[4]) / S["faces"], 1), "method", S.get("method"))


run("sphere", g.icosphere(5), 3000, "extract")
run("torus", g.torus(), 3000, "extract")
run("blob", g.bumpy_blob(), 3000, "extract")
run("box with marked edges", g.box(), 1000, "extract", use_features=True)
# the 12 edges of the box must be kept: output edges lying on each edge cover its whole length
def box_edges_kept():
    co0, tr0, feats0 = g.box()[:3]
    co0 = np.asarray(co0, float)
    res, info = run_engine(co0, np.asarray(tr0), feats=feats0, faces=1500, mode="quad", check=False, timeout=120)
    P, sizes, idx = np.asarray(res[0], float), res[1], res[2]
    edges = set()
    at = 0
    for sz in sizes:
        f = idx[at:at + sz]; at += sz
        for k in range(sz):
            a, b = f[k], f[(k + 1) % sz]
            edges.add((min(a, b), max(a, b)))
    lo, hi = co0.min(0), co0.max(0)
    covered = 0.0
    for ax in range(3):
        for corner in range(4):
            o = [ (lo if (corner >> j) & 1 == 0 else hi) for j in range(2)]
            others = [a for a in range(3) if a != ax]
            tot = 0.0
            for a, b in edges:
                pa, pb = P[a], P[b]
                ok = all(abs(pa[others[j]] - o[j][others[j]]) < 1e-3 and abs(pb[others[j]] - o[j][others[j]]) < 1e-3 for j in range(2))
                if ok:
                    tot += abs(pa[ax] - pb[ax])
            covered += min(tot / (hi[ax] - lo[ax]), 1.0)
    check(covered > 11.5, "the 12 box edges are kept along their whole length", round(covered, 2))
print("\n== box edges")
box_edges_kept()
run("hemisphere with border", g.hemisphere(), 800, "extract")
# density gradients on a sphere (the integer-grid method cannot follow them with only 8 singularities, so the
# result must come from the pairing path: all quads, closed, right count, right direction)
c2, t2 = g.icosphere(5)[:2]
c2 = np.asarray(c2, float)
for top, expect in ((1.4, None), (3.0, None)):
    dens = (1.0 + (top - 1.0) * (c2[:, 2] - c2[:, 2].min()) / (c2[:, 2].max() - c2[:, 2].min())).astype(np.float32)
    print(f"\n== density gradient 1 -> {top}")
    res, info = run_engine(c2, np.asarray(t2), faces=3000, mode="quad", density=dens, check=False)
    c = independent_checks(*res)
    if expect:
        check(info["S"].get("method") == expect, f"method is {expect}", info["S"].get("method"))
    check(info["S"]["tris"] == 0 and info["S"]["quads"] > 0, "all quads", info["S"]["tris"])
    check(c["boundary_edges"] == 0 and c["nonmanifold_edges"] == 0 and c["same_direction_edges"] == 0 and c["euler"] == 2, "closed and manifold", c)
    check(abs(info["S"]["faces"] - 3000) <= 0.1 * 3000, "face count within 10%", info["S"]["faces"])
    Pq = np.asarray(res[0]); sz = np.asarray(res[1]); ix = np.asarray(res[2])
    pos = 0; elen = []
    for n in sz:
        q = Pq[ix[pos:pos + n]]; pos += n
        elen.append((np.linalg.norm(q[0] - q[1]) + np.linalg.norm(q[1] - q[2]), q[:, 2].mean()))
    elen = np.array(elen)
    lo = np.median(elen[elen[:, 1] < np.percentile(elen[:, 1], 30), 0]); hi = np.median(elen[elen[:, 1] > np.percentile(elen[:, 1], 70), 0])
    check(hi / lo > (1.0 + 0.4 * (top - 1.0) if top > 2 else 1.05), "quads are larger where the density is higher", round(hi / lo, 2))
# two separate spheres
c1, t1 = g.icosphere(4)[:2]
c1 = np.asarray(c1, float)
co = np.vstack([c1, c1 + np.array([3.0, 0, 0])])
tr = np.vstack([np.asarray(t1), np.asarray(t1) + len(c1)])
res, info = run_engine(co, tr, faces=2000, mode="quad", check=False)
c = independent_checks(*res)
print("\n== two spheres")
check(c["euler"] == 4 and c["boundary_edges"] == 0 and c["nonmanifold_edges"] == 0, "both components are closed", c)
print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
