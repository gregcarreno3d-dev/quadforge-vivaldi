import sys, json
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import numpy as np
from stlio import read_stl
from harness import run_engine, independent_checks
import glob
faces = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
extra = sys.argv[2:]
for f in sorted(glob.glob(sys.argv[0].rsplit("/",1)[0] + "/../../models/*.stl")):
    co, tris = read_stl(f)
    res, info = run_engine(co, tris, faces=faces, mode="quad", extra=extra, check=False, timeout=120)
    if res is None: print(f[-30:], "FAIL", info); continue
    S = info["S"]; v = S["valence"]
    tot = sum(v)
    print(f"{f.split('/')[-1][9:28]:20s} quads {S['quads']:5d} val4 {100*v[4]/tot:5.1f}%  poles/1k {1000*(tot-v[4])/S['faces']:6.1f} irreg {S.get('irregular_before')}->{S.get('irregular_after')}  match {S['match_s']:.2f}s total {info['wall']:.2f}s dist {S['dist_mean']:.4f}")
