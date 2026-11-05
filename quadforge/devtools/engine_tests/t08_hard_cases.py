"""Hard cases for the engine: open surfaces, several parts, dirty input, odd scales, dense scans, density, triangles.

    FORGE_ENGINE=/path/to/forge python t08_hard_cases.py [--save DIR]    (--save writes the inputs as STL for manual tests)
"""
import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from harness import run_engine, independent_checks  # noqa: E402
import t07_robustness as T  # noqa: E402
import meshgen  # noqa: E402


def write_stl(path, co, tris):
    tri = co[tris].astype("<f4")
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-30
    with open(path, "wb") as f:
        f.write(b"quadforge test model".ljust(80, b"\0"))
        f.write(struct.pack("<I", len(tris)))
        rec = np.zeros(len(tris), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
        rec["n"], rec["v"] = n, tri
        f.write(rec.tobytes())


def merge(parts):
    cos, tris, off = [], [], 0
    for co, t in parts:
        cos.append(co); tris.append(t + off); off += len(co)
    return np.vstack(cos), np.vstack(tris)


def sphere(sub=4, r=1.0, c=(0, 0, 0)):
    co, t, _ = meshgen.icosphere(sub, r)
    return co + np.array(c), t


def cut_faces(co, tris, keep):
    t = tris[keep]
    u, inv = np.unique(t.ravel(), return_inverse=True)
    return co[u], inv.reshape(-1, 3)


def open_cap():                      # sphere without its top
    co, t = sphere(5)
    return cut_faces(co, t, co[t].mean(1)[:, 2] < 0.55)


def open_tube():
    co, t, _ = meshgen.cylinder(0.5, 1.5, 48, 12, 6)
    top = co[:, 2].max(); bot = co[:, 2].min()
    keep = (np.abs(co[t][:, :, 2] - top) < 1e-6).sum(1) < 3
    keep &= (np.abs(co[t][:, :, 2] - bot) < 1e-6).sum(1) < 3
    keep &= co[t][:, :, 2].max(1) < top + 1e-6
    # keep only the side wall: drop faces lying on the caps
    flat_top = np.all(np.abs(co[t][:, :, 2] - top) < 1e-6, axis=1)
    flat_bot = np.all(np.abs(co[t][:, :, 2] - bot) < 1e-6, axis=1)
    return cut_faces(co, t, ~(flat_top | flat_bot))


def open_disc():
    n = 40
    xs = np.linspace(-1, 1, n)
    X, Y = np.meshgrid(xs, xs)
    co = np.c_[X.ravel(), Y.ravel(), 0.1 * np.sin(3 * X.ravel()) * np.cos(2 * Y.ravel())]
    idx = np.arange(n * n).reshape(n, n)
    t = []
    for i in range(n - 1):
        for j in range(n - 1):
            a, b, c, d = idx[i, j], idx[i, j + 1], idx[i + 1, j + 1], idx[i + 1, j]
            t += [[a, b, c], [a, c, d]]
    return co, np.array(t)


def holey_sphere():
    co, t = sphere(5)
    cen = co[t].mean(1)
    keep = np.ones(len(t), bool)
    for d in ([1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, 0, 1], [0.6, 0.6, -0.5]):
        d = np.array(d, float); d /= np.linalg.norm(d)
        keep &= (cen @ d) < 0.93
    return cut_faces(co, t, keep)


def three_balls():
    return merge([sphere(4, 0.8, (-2, 0, 0)), sphere(4, 0.6, (0, 0, 0)), sphere(4, 1.0, (2.5, 0, 0))])


def nested_spheres():
    a = sphere(4, 1.0)
    b = sphere(4, 0.6)
    return merge([a, (b[0], b[1][:, ::-1])])      # inner one facing inward: a hollow shell


def dirty():
    co, t = sphere(5)
    rng = np.random.default_rng(0)
    t = t.copy()
    flip = rng.random(len(t)) < 0.10
    t[flip] = t[flip][:, ::-1]                      # 10% flipped faces
    dup = t[rng.choice(len(t), 40, replace=False)]  # duplicate faces
    deg = np.array([[0, 0, 1], [5, 5, 5], [7, 8, 8]])  # degenerate triangles
    co2 = np.vstack([co, co[:3] + 5.0])             # unused vertices
    return co2, np.vstack([t, dup, deg])


def fin_nonmanifold():
    co, t = sphere(4)
    extra_v = np.array([[0, 0, 1.0], [0.3, 0.3, 1.5], [-0.3, 0.3, 1.5]])
    n = len(co)
    # three triangles hanging off one edge (non-manifold fin)
    e = t[0]
    fin = [[e[0], e[1], n], [e[0], e[1], n + 1], [e[0], e[1], n + 2]]
    return np.vstack([co, co[e[0]] + 0.3 * np.array([[0, 0, 1], [0, 1, 1], [1, 0, 1]])]), np.vstack([t, fin])


def lumpy_dense():
    P, h = T.grid(190)
    rng = np.random.default_rng(5)
    d = np.linalg.norm(P * np.array([1.0, 0.8, 1.2]), axis=-1) - 0.9
    for _ in range(10):
        d = d + 0.04 * np.sin(P @ (rng.normal(size=3) * 3.0) + rng.uniform(0, 6))
    return T.mesh_from(d, h)


def thin_plate():                    # closed slab, thickness 2 % of its width
    co, t, _ = meshgen.box(24, (1.0, 1.0, 0.02))
    return co, t


def long_rod():                      # closed cylinder, length 12 x diameter
    co, t, _ = meshgen.cylinder(0.1, 2.4, 32, 60, 4)
    return co, t


def finned_sphere():                 # sphere with four thin closed fins
    parts = [sphere(4, 0.6)]
    for ax in range(3):
        size = [1.6, 1.6, 1.6]; size[ax] = 0.03
        co, t, _ = meshgen.box(12, tuple(size))
        parts.append((co, t))
    return merge(parts)


def two_wall_shell():                # two concentric spheres very close together (a hollow shell)
    a = sphere(4, 1.0); b = sphere(4, 0.94)
    return merge([a, (b[0], b[1][:, ::-1])])


CASES = []


def add(name, builder, faces, mode="quad", open_ok=False, scale=1.0, offset=(0, 0, 0), density=False, min_quad=0.98, count_tol=0.10, comps=1):
    CASES.append(dict(name=name, builder=builder, faces=faces, mode=mode, open_ok=open_ok, scale=scale,
                      offset=np.array(offset, float), density=density, min_quad=min_quad, count_tol=count_tol))


for nm, fn in (("open-cap", open_cap), ("open-tube", open_tube), ("open-disc", open_disc), ("holey-sphere", holey_sphere)):
    for t in (1500, 8000):
        add(nm, fn, t, open_ok=True, min_quad=0.95)
add("three-balls", three_balls, 3000)
add("three-balls", three_balls, 12000)
add("nested-spheres", nested_spheres, 3000)
add("thin-plate", thin_plate, 3000, open_ok=True, min_quad=0.9, count_tol=0.15)
add("long-rod", long_rod, 3000, open_ok=True, min_quad=0.9, count_tol=0.15)
add("finned-sphere", finned_sphere, 4000, open_ok=True, min_quad=0.9, count_tol=0.15)
add("two-wall-shell", two_wall_shell, 4000, open_ok=True, min_quad=0.9, count_tol=0.15)
add("dirty-sphere", dirty, 3000, open_ok=True)
add("fin-nonmanifold", fin_nonmanifold, 3000, open_ok=True)
add("sphere x0.001 (mm->m)", lambda: sphere(5), 4000, scale=0.001)
add("sphere x1000", lambda: sphere(5), 4000, scale=1000.0)
add("sphere far from origin", lambda: sphere(5), 4000, offset=(5000, -3000, 800))
add("creature far+tiny", lambda: T.creature(2), 6000, scale=0.01, offset=(40, 40, 40))
add("lumpy 20K from 500K tris", lumpy_dense, 20000)
add("creature density", lambda: T.creature(4), 6000, density=True)
add("block-holes density", lambda: T.block_holes(0), 6000, density=True)
for nm, fn in (("creature-1", lambda: T.creature(1)), ("donut-arms", lambda: T.donut_arms(0)), ("block-holes", lambda: T.block_holes(0)),
               ("open-cap", open_cap), ("three-balls", three_balls)):
    add(nm + " TRIS", fn, 4000, mode="tri", open_ok=True, min_quad=0.0)


def run(c):
    co, tris = c["builder"]()
    co = co.astype(np.float64) * c["scale"] + c["offset"]
    dens = None
    if c["density"]:
        z = (co[:, 2] - co[:, 2].min()) / max(1e-9, np.ptp(co[:, 2]))
        dens = (1.0 - 0.6 * z).astype(np.float32)          # density attribute: 1 at the bottom, 0.4 at the top
    res, info = run_engine(co, tris, faces=c["faces"], mode=c["mode"], density=dens, check=False, timeout=1200)
    if res is None:
        return False, "engine error: " + str(info.get("error") or info.get("stderr", ""))[:100], {}, (co, tris)
    oc, sizes, idx = res
    chk = independent_checks(oc, sizes, idx)
    in_chk = independent_checks(co, np.full(len(tris), 3), tris.reshape(-1))
    quads = int((np.asarray(sizes) == 4).sum())
    qfrac = quads / max(1, len(sizes))
    err = abs(len(sizes) - c["faces"]) / c["faces"]
    st = info.get("S", {})
    q = dict(faces=len(sizes), qfrac=round(qfrac, 3), err=round(err, 3), poles_k=round(1000 * T.valence_poles(sizes, idx) / max(1, len(sizes)), 1),
             method=st.get("method"), sec=round(info["wall"], 1))
    bad = []
    if chk["nonmanifold_edges"] or chk["same_direction_edges"] or chk["degenerate_faces"] or chk["nan"]:
        bad.append("topology " + json.dumps({k: v for k, v in chk.items() if v and k not in ("euler", "unused_vertices", "boundary_edges")}))
    if chk["boundary_edges"] and not (c["open_ok"] or in_chk["boundary_edges"]):
        bad.append(f"open edges {chk['boundary_edges']}")
    if c["mode"] == "quad" and qfrac < c["min_quad"]:
        bad.append(f"quads {qfrac:.1%}")
    if c["mode"] == "quad" and err > c["count_tol"]:
        bad.append(f"count off {err:.0%}")
    if c["mode"] == "quad" and st.get("method") != "extract":
        bad.append("fallback: " + str(st.get("fallback")))
    if c["mode"] == "tri" and err > 0.15:
        bad.append(f"count off {err:.0%}")
    # size stays where it was (scale/offset must not matter)
    if c["scale"] != 1.0 or np.abs(c["offset"]).max() > 0:
        lo, hi = co.min(0), co.max(0)
        olo, ohi = oc.min(0), oc.max(0)
        span = np.ptp(co, axis=0).max()
        if np.abs(olo - lo).max() > 0.03 * span or np.abs(ohi - hi).max() > 0.03 * span:
            bad.append("shape moved or shrank")
    return not bad, "; ".join(bad), q, (co, tris)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", default="")
    ap.add_argument("--save-all", action="store_true")
    a = ap.parse_args()
    ok = 0
    for c in CASES:
        try:
            good, why, q, mesh = run(c)
        except Exception as e:  # noqa: BLE001
            good, why, q, mesh = False, "exception " + repr(e)[:100], {}, None
        ok += good
        print(f"{'ok  ' if good else 'FAIL'} {c['name']:26s} {c['faces']:6d} {json.dumps(q)} {why}", flush=True)
        if a.save and mesh is not None and (a.save_all or not good):
            Path(a.save).mkdir(parents=True, exist_ok=True)
            write_stl(Path(a.save) / (c["name"].replace(" ", "_").replace("/", "-") + f"_{c['faces']}.stl"), *mesh)
    print(f"\nSUCCESS {ok}/{len(CASES)} = {100.0 * ok / len(CASES):.1f}%")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
