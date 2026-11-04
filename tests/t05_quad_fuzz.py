import sys
sys.path.insert(0, '/home/claude/forge/tools')
import numpy as np, meshgen as g
from harness import *
rng = np.random.default_rng(11)
gens = [lambda: g.icosphere(int(rng.integers(2, 5))), lambda: g.torus(nu=int(rng.integers(12, 60)), nv=int(rng.integers(8, 30))),
        lambda: g.box(int(rng.integers(3, 14))), lambda: g.cylinder(nseg=int(rng.integers(8, 50)), nrings=int(rng.integers(2, 14))),
        lambda: g.hemisphere(), lambda: g.bumpy_blob(int(rng.integers(2, 5)), int(rng.integers(0, 99)))]
bad = 0; worst = 0
for k in range(60):
    co, tris, feats = gens[k % 6]()
    faces = int(np.exp(rng.uniform(np.log(40), np.log(15000))))
    res, info = run_engine(co, tris, feats if rng.random() < 0.7 else None, faces=faces, mode='quad', check=True)
    if res is None:
        bad += 1; print('FAIL', k, k % 6, faces, info.get('error'), info['stderr'][-200:]); continue
    c2, s2, i2 = res
    chk = independent_checks(c2, s2, i2)
    if chk['nonmanifold_edges'] or chk['same_direction_edges'] or chk['degenerate_faces'] or chk['nan'] or (s2 != 4).any():
        bad += 1; print('INVALID', k, k % 6, faces, chk)
print('quad fuzz: bad =', bad)
