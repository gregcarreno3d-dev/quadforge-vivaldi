import sys, json, time
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import meshgen as g
from harness import *

OUT = str(pathlib.Path(__file__).resolve().parent / 'out') + '/'
pathlib.Path(OUT).mkdir(exist_ok=True)
fails = []

def check(name, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + name + (f'  [{detail}]' if detail else ''))
    if not cond: fails.append(name)

def case(name, co, tris, feats=None, faces=1000, density=None, extra=(), render_it=True, expect_closed=True):
    print(f'\n== {name}: {len(tris)} tris -> {faces}')
    res, info = run_engine(co, tris, feats, faces=faces, density=density, extra=extra)
    check('engine finished', res is not None, info.get('error', ''))
    if res is None: return None
    c2, s2, i2 = res
    chk = independent_checks(c2, s2, i2)
    S = info['S']
    err = abs(S['faces'] - faces) / faces
    check(f'face count within 4% ({S["faces"]} vs {faces})', err < 0.04, f'{err:.1%}')
    check('manifold, consistent', chk['nonmanifold_edges'] == 0 and chk['same_direction_edges'] == 0 and chk['degenerate_faces'] == 0 and not chk['nan'], str(chk))
    if expect_closed: check('closed surface (no border)', chk['boundary_edges'] == 0)
    h = hausdorff(co, np.asarray(tris), c2, triangulate(s2, i2))
    diag = np.linalg.norm(co.max(0) - co.min(0))
    check(f'shape error < 1.5% of size', h['max'] < 0.015 * diag, f'max {h["max"]/diag:.3%} mean {h["mean"]/diag:.4%}')
    check('min angle > 15 deg', S['angle_min'] > 15, f'{S["angle_min"]:.1f}')
    print(f'  time {S["seconds"]:.3f}s, valence-6: {S["valence"][6]}/{S["verts"]}, angle min/mean {S["angle_min"]:.1f}/{S["angle_mean"]:.1f}, edge len {S["edge_len_min"]:.3g}..{S["edge_len_max"]:.3g}')
    if render_it: render(c2, s2, i2, OUT + name + '.png', f'{name}: {len(tris)} -> {S["faces"]} tris')
    return c2, s2, i2, info

for f in (200, 800, 3000, 20000):
    case(f'sphere_{f}', *g.icosphere(4)[:2], None, faces=f, render_it=f in (200, 20000) and False)
case('torus', *g.torus()[:2], None, faces=1500)
case('blob', *g.bumpy_blob()[:2], None, faces=3000)

# --- hard edges: box
co, tris, feats = g.box(16)
r = case('box_features', co, tris, feats, faces=700)
if r:
    c2, s2, i2, info = r
    # every true edge must be covered by a chain of output edges lying exactly on it
    ends = []
    size = np.array([1, 1, 1.0])
    corners = np.array([[x, y, z] for x in (-.5, .5) for y in (-.5, .5) for z in (-.5, .5)])
    box_edges = [(a, b) for a in range(8) for b in range(a + 1, 8) if np.isclose(np.linalg.norm(corners[a] - corners[b]), 1.0)]
    def dist_to_segments(P, A, B):
        AB = B - A
        t = np.clip(((P - A) @ AB) / (AB @ AB), 0, 1)
        return np.linalg.norm(P - (A + t[:, None] * AB), axis=1)
    on_edge = np.zeros(len(c2), bool)
    dmin = np.full(len(c2), 9.0)
    for a, b in box_edges:
        dmin = np.minimum(dmin, dist_to_segments(c2, corners[a], corners[b]))
    on_edge = dmin < 1e-5
    # output edges with both ends on a true edge and a midpoint on it
    E = set()
    for p in polygons(s2, i2):
        for k in range(3):
            a, b = int(p[k]), int(p[(k + 1) % 3]); E.add((min(a, b), max(a, b)))
    good = [(a, b) for a, b in E if on_edge[a] and on_edge[b] and
            min(dist_to_segments(((c2[a] + c2[b]) / 2)[None], corners[x], corners[y])[0] for x, y in box_edges) < 1e-5]
    # coverage: sample every true edge densely, require a chain edge within 1e-5
    worst = 0
    for a, b in box_edges:
        ts = np.linspace(0, 1, 200)
        pts = corners[a] + ts[:, None] * (corners[b] - corners[a])
        d = np.full(len(pts), 9.0)
        for (u, v) in good:
            AB = c2[v] - c2[u]
            t = np.clip(((pts - c2[u]) @ AB) / (AB @ AB), 0, 1)
            d = np.minimum(d, np.linalg.norm(pts - (c2[u] + t[:, None] * AB), axis=1))
        worst = max(worst, d.max())
    check('all 12 box edges kept as exact edge chains', worst < 1e-5, f'worst gap {worst:.2e}')
    cornerd = [np.linalg.norm(c2 - c, axis=1).min() for c in corners]
    check('all 8 corners present as vertices', max(cornerd) < 1e-5, f'{max(cornerd):.2e}')

co, tris, feats = g.cylinder()
case('cylinder_features', co, tris, feats, faces=900)

# --- open border: hemisphere
co, tris, _ = g.hemisphere(4)
r = case('hemisphere_border', co, tris, None, faces=500, expect_closed=False)
if r:
    c2, s2, i2, info = r
    chk = independent_checks(c2, s2, i2)
    ring = np.abs(c2[:, 2]) < 1e-9
    check('border stays on the cut plane', chk['boundary_edges'] > 0 and ring.sum() == chk['boundary_edges'], f'border edges {chk["boundary_edges"]}, ring verts {ring.sum()}')
    rr = np.linalg.norm(c2[ring][:, :2], axis=1)
    check('border radius preserved', np.abs(rr - 1).max() < 2e-3, f'{np.abs(rr-1).max():.2e}')

# --- density: upper half 3x finer in edge length
co, tris, _ = g.icosphere(5)
dens = np.where(co[:, 2] > 0, 0.5, 1.5).astype(np.float32)
r = case('sphere_density', co, tris, None, faces=2500, density=dens, render_it=True)
if r:
    c2, s2, i2, info = r
    up, lo = [], []
    for p in polygons(s2, i2):
        for k in range(3):
            a, b = int(p[k]), int(p[(k + 1) % 3])
            m = (c2[a] + c2[b]) / 2
            l = np.linalg.norm(c2[a] - c2[b])
            (up if m[2] > 0.3 else lo if m[2] < -0.3 else []).append(l)
    ratio = np.mean(lo) / np.mean(up)
    check('density: lower half edges ~3x longer than upper', 2.2 < ratio < 3.8, f'ratio {ratio:.2f}')

print('\nFAILS:', fails if fails else 'none')
