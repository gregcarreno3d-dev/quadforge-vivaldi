import sys, json
import pathlib
pathlib.Path(__file__).resolve().parent.joinpath('out').mkdir(exist_ok=True)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np
import meshgen as g
from harness import *

co, tris, feats = g.icosphere(4)   # 5120 faces
res, info = run_engine(co, tris, feats, faces=800)
print(json.dumps({k: v for k, v in info.items() if k != 'stderr'}, indent=1)[:1500])
print(info['stderr'][:500])
if res:
    c2, s2, i2 = res
    print(independent_checks(c2, s2, i2))
    print(hausdorff(co, tris, c2, triangulate(s2, i2)))
    render(c2, s2, i2, str(pathlib.Path(__file__).resolve().parent / 'out' / 't01_sphere.png'), 'icosphere 5120 -> 800 tris')
