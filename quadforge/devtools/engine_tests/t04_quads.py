import sys, json
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import meshgen as g
from harness import *

OUT = str(pathlib.Path(__file__).resolve().parent / 'out') + '/'
pathlib.Path(OUT).mkdir(exist_ok=True)
def quad_case(name, co, tris, feats, faces, **kw):
    res, info = run_engine(co, tris, feats, faces=faces, mode='quad', **kw)
    if res is None:
        print(f'{name}: FAILED {info.get("error")} {info.get("stderr")[:300]}'); return None
    c2, s2, i2 = res
    S = info['S']; chk = independent_checks(c2, s2, i2)
    v = S['valence']; tot = S['verts']
    h = hausdorff(co, np.asarray(tris), c2, triangulate(s2, i2), n=30000)
    diag = np.linalg.norm(co.max(0) - co.min(0))
    print(f"{name:18s} quads {S['quads']:>5} (target {faces}) tris {S['tris']} | val4 {v[4]/tot:5.1%} val3 {v[3]} val5 {v[5]} val6+ {sum(v[6:])} | angle min {S['angle_min']:.0f} mean {S['angle_mean']:.1f} bad {S['bad_angle_corners']} | leftover {S['leftover_tris']} | {S['seconds']+S['field_s']+S['match_s']+S['relax_s']:.2f}s | dist {h['max']/diag:.2%} | euler {chk['euler']} nm {chk['nonmanifold_edges']} bnd {chk['boundary_edges']}")
    render(c2, s2, i2, OUT + name + '.png', f"{name}: {S['quads']} quads")
    return c2, s2, i2, S

quad_case('q_sphere', *g.icosphere(4)[:2], None, 600)
quad_case('q_torus', *g.torus()[:2], None, 1200)
quad_case('q_box', *g.box(16), 1000)
quad_case('q_cylinder', *g.cylinder(), 1500)
quad_case('q_hemisphere', *g.hemisphere()[:2], None, 800)
quad_case('q_blob', *g.bumpy_blob(5)[:2], None, 3000)
