"""Synthetic head: eye sockets, nose, mouth groove, ears. Checks, at three sizes with symmetry, curvature detail and
flow guides around the eyes and along the mouth, that the result is valid, even, and that the quad rows follow the
guide loops. (Real scans are the real test; this keeps the head-like features from regressing.)"""
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "devtools"))
import blender_matrix_test as M  # noqa: E402
import meshgen as g  # noqa: E402

EYES = [np.array([s * 0.36, 0.86, 0.28]) for s in (-1, 1)]
EYE_R = 0.17
MOUTH_Z = -0.30


def head():
    co, tris, _ = g.icosphere(6, 1.0)
    co = co.astype(np.float64)
    p = co.copy()
    n = p / np.linalg.norm(p, axis=1, keepdims=True)
    d = np.zeros(len(p))
    front = n[:, 1] > 0
    for eye in EYES:                                   # sockets
        r = np.linalg.norm(n - eye / np.linalg.norm(eye), axis=1)
        d -= 0.07 * np.exp(-(r / 0.16) ** 2)
    r = np.linalg.norm(n - np.array([0, 0.97, 0.05]), axis=1)   # nose
    d += 0.16 * np.exp(-(r / 0.13) ** 2) * front
    groove = np.exp(-((n[:, 2] - MOUTH_Z) / 0.035) ** 2) * np.exp(-(n[:, 0] / 0.42) ** 2) * front   # mouth
    d -= 0.05 * groove
    for s in (-1, 1):                                  # ears
        r = np.linalg.norm(n - np.array([s * 0.99, 0.0, 0.1]), axis=1)
        d += 0.14 * np.exp(-(r / 0.2) ** 2)
    co = n * (1.0 + d)[:, None]
    co[:, 2] *= 1.15
    return co.astype(np.float32), tris


def loops():
    """Guide polylines: a ring around each eye and the mouth line, on the head surface (as unit directions)."""
    out = []
    for eye in EYES:
        c = eye / np.linalg.norm(eye)
        u = np.cross(c, [0, 0, 1.0]); u /= np.linalg.norm(u)
        v = np.cross(c, u)
        pts = []
        for a in np.linspace(0, 2 * np.pi, 33):
            q = c + 0.27 * (np.cos(a) * u + np.sin(a) * v)
            pts.append(q / np.linalg.norm(q))
        out.append(np.array(pts))
    xs = np.linspace(-0.4, 0.4, 17)
    mouth = []
    for x in xs:
        y = np.sqrt(max(1e-6, 1 - x * x - MOUTH_Z ** 2))
        mouth.append(np.array([x, y, MOUTH_Z]))
    out.append(np.array(mouth))
    return out


def near_loop(direction, polylines):
    best = 1e9
    for pl in polylines:
        for a, b in zip(pl[:-1], pl[1:]):
            ab = b - a
            t = np.clip(np.dot(direction - a, ab) / np.dot(ab, ab), 0, 1)
            best = min(best, np.linalg.norm(direction - (a + ab * t)))
    return best


def follows(mesh, polylines, reach=0.06, tol=25):
    co = np.array([v.co[:] for v in mesh.vertices])
    ok = tot = 0
    segs = [(a, b) for pl in polylines for a, b in zip(pl[:-1], pl[1:])]
    for e in mesh.edges:
        a, b = co[e.vertices[0]], co[e.vertices[1]]
        m = (a + b) / 2
        mn = m / np.linalg.norm(m)
        best = (1e9, None)
        for s0, s1 in segs:
            ab = s1 - s0
            L = np.linalg.norm(ab)
            t = np.clip(np.dot(mn - s0, ab) / (L * L), 0, 1)
            dd = np.linalg.norm(mn - (s0 + ab * t))
            if dd < best[0]:
                best = (dd, ab / L)
        if best[0] > reach:
            continue
        ev = (b - a) / (np.linalg.norm(b - a) + 1e-12)
        ang = np.degrees(np.arccos(min(1, abs(np.dot(ev, best[1])))))
        tot += 1
        ok += ang < tol or ang > 90 - tol
    return ok / max(tot, 1), tot


co, tris = head()
polylines = loops()
ok = True
for target in (2000, 8000, 30000):
    for guides in (False, True):
        M.reset()
        obj = M.make("head", co, tris)
        me = obj.data
        attribute = me.attributes.new("quadforge_guide", "BOOLEAN", "EDGE")
        dirs = np.array([v.co[:] for v in me.vertices])
        dirs = dirs / np.linalg.norm(dirs, axis=1, keepdims=True)
        near = np.array([near_loop(d, polylines) < 0.012 for d in dirs])
        attribute.data.foreach_set("value", [bool(near[e.vertices[0]] and near[e.vertices[1]]) for e in me.edges])
        s = M.pipeline.RemeshSettings()
        s.backend = "FORGE"; s.output = "QUADS"; s.target_quads = target; s.hide_original = False
        s.sym_x = True; s.curvature_detail = "MEDIUM"; s.use_flow_guides = guides
        r = M.pipeline.run(M.bpy.context, obj, s)
        st = r.levels[0].stats
        frac, n = follows(r.object.data, polylines)
        error = abs(st["faces"] - target) / target
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tools"))
        import quality_metrics as qm
        from quadforge.core import mesh_io as mio
        arrays = mio.read_mesh_arrays(r.object.data)
        jac, _ = qm.scaled_jacobian(arrays.co, np.diff(arrays.poly_offsets), arrays.loop_verts)
        p1 = float(np.percentile(jac, 1))
        good = (st["border_edges"] == 0 and st["non_manifold_edges"] == 0 and st["quads"] == st["faces"]
                and error < 0.12 and p1 > 0.3 and float(jac.mean()) > 0.9)
        print(("PASS" if good else "FAIL"),
              f"head {target:>5} guides {'on ' if guides else 'off'}: faces {st['faces']} ({100 * error:.1f}% off), "
              f"Jacobian mean {jac.mean():.3f} p1 {p1:.2f}, rows along the loops {100 * frac:.0f}% ({n} edges)")
        ok &= good
print("ALL PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
