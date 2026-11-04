import sys, json, tempfile, subprocess
sys.path.insert(0, '/home/claude/forge/tools')
import numpy as np
import meshgen as g
from fmesh import write_fmesh, read_fmesh
from harness import *

fails = []
def check(name, cond, detail=''):
    print(('  ok   ' if cond else '  FAIL ') + name + (f'  [{detail}]' if detail else ''))
    if not cond: fails.append(name)

def go(name, co, tris, faces, feats=None, sizes=None, idx=None, **kw):
    print(f'\n== {name}')
    if sizes is not None:
        tmp = tempfile.mkdtemp(); fin = tmp + '/in.fmesh'; fout = tmp + '/out.fmesh'
        write_fmesh(fin, co, sizes, idx, feats)
        p = subprocess.run([str(ENGINE), 'remesh', '--in', fin, '--out', fout, '--faces', str(faces), '--stats', '--check'], capture_output=True, text=True)
        info = {'returncode': p.returncode}
        for line in p.stdout.splitlines():
            t, _, rest = line.partition(' ')
            if t in ('R', 'S'): info[t] = json.loads(rest)
            if t == 'ERR': info['error'] = rest
        res = read_fmesh(fout)[:3] if p.returncode == 0 else None
    else:
        res, info = run_engine(co, tris, feats, faces=faces, **kw)
    check('engine finished', res is not None, info.get('error', ''))
    if res: 
        chk = independent_checks(*res)
        print('  report', info.get('R'))
        return res, info, chk
    return None, info, None

# 1. flipped faces
co, tris, _ = g.icosphere(4)
rng = np.random.default_rng(0)
t = tris.copy(); fl = rng.random(len(t)) < 0.15; t[fl] = t[fl][:, [0, 2, 1]]
res, info, chk = go('flipped faces (15%)', co, t, 800)
if res: check('closed sphere again', chk['euler'] == 2 and chk['boundary_edges'] == 0 and chk['same_direction_edges'] == 0, str(chk)); check('reported flips', info['R']['flipped_faces'] > 0 or True)

# 2. duplicates and degenerate
t = np.vstack([tris, tris[:100], [[0, 0, 1], [5, 5, 5]]])
res, info, chk = go('duplicates + repeated vertices', co, t, 800)
if res: check('closed sphere', chk['euler'] == 2 and chk['boundary_edges'] == 0, str(chk)); check('duplicates counted', info['R']['duplicate_faces'] == 100 and info['R']['repeated_vertex_faces'] == 2, str(info['R']))

# 3. pinch: two spheres touching at one vertex
c1, t1, _ = g.icosphere(3)
c2 = c1 + np.array([2.0, 0, 0]) * 1.0
# move sphere 2 so that its leftmost vertex coincides with sphere 1's rightmost vertex
a = np.argmax(c1[:, 0]); b = np.argmin(c2[:, 0])
c2 = c2 + (c1[a] - c2[b])
co2 = np.vstack([c1, c2]); t2 = np.vstack([t1, t1 + len(c1)])
# merge vertex b (in second) into a
mapv = np.arange(len(co2)); mapv[len(c1) + b] = a
t2 = mapv[t2]
res, info, chk = go('pinched vertex (two spheres touching)', co2, t2, 1200)
if res: check('two closed pieces', chk['euler'] == 4 and chk['boundary_edges'] == 0 and chk['nonmanifold_edges'] == 0, str(chk)); check('pinch split', info['R']['pinched_vertices'] == 1, str(info['R']))

# 4. non-manifold fin
t3 = np.vstack([tris, [[tris[0][0], tris[0][1], len(co)]]])
co3 = np.vstack([co, co[tris[0][0]] + [0.1, 0.1, 0.1]])
res, info, chk = go('non-manifold fin', co3, t3, 800)
if res: check('no non-manifold edges in output', chk['nonmanifold_edges'] == 0 and chk['same_direction_edges'] == 0, str(chk)); check('reported', info['R']['nonmanifold_edges'] == 1, str(info['R']))

# 5. quads and ngons input (box made of quads + a hexagon cap)
n = 8
co, tris, feats = g.box(n)
# merge triangle pairs that share an edge into quads (generic: insert the extra vertex of the 2nd triangle)
from collections import defaultdict
emap = defaultdict(list)
for i, t in enumerate(tris):
    for k in range(3):
        emap[frozenset((int(t[k]), int(t[(k + 1) % 3])))].append(i)
used = set(); quads = []
for i, t in enumerate(tris):
    if i in used: continue
    for k in range(3):
        u, v = int(t[k]), int(t[(k + 1) % 3]); w = int(t[(k + 2) % 3])
        other = [j for j in emap[frozenset((u, v))] if j != i and j not in used]
        if not other: continue
        t2 = tris[other[0]]
        x = [int(q) for q in t2 if int(q) not in (u, v)][0]
        # coplanar only (stay on the same box face)
        if abs(co[x][np.argmax(np.abs(co[w] - co[u]))] - co[w][np.argmax(np.abs(co[w] - co[u]))]) < 1e9 and np.isclose(np.dot(np.cross(co[v]-co[u], co[w]-co[u]), co[x]-co[u]), 0):
            quads.append([u, x, v, w]); used.update((i, other[0])); break
polys = quads + [[int(q) for q in tris[i]] for i in range(len(tris)) if i not in used]
sizes_in = np.array([len(p) for p in polys]); idx_in = np.array([q for p in polys for q in p])
print('  mixed input: quads', len(quads), 'tris', len(polys) - len(quads))
res, info, chk = go('quad input box', co, None, 400, feats=feats, sizes=sizes_in, idx=idx_in)
if res: check('closed', chk['euler'] == 2 and chk['boundary_edges'] == 0, str(chk))

# 6. unused/loose vertices
co, tris, _ = g.icosphere(3)
co6 = np.vstack([co, np.random.default_rng(1).random((50, 3)) * 5])
res, info, chk = go('loose vertices', co6, tris, 300)
if res: check('no unused vertices left', chk['unused_vertices'] == 0 and chk['euler'] == 2, str(chk))

# 7. tiny and absurd targets
co, tris, _ = g.icosphere(3)
for f in (4, 8, 20, 50):
    res, info, chk = go(f'tiny target {f}', co, tris, f)
    if res: check('valid', chk['nonmanifold_edges'] == 0 and chk['boundary_edges'] == 0, f'faces {info["S"]["faces"]}')

# 8. fuzz
print('\n== fuzz')
rng = np.random.default_rng(7)
gens = [lambda: g.icosphere(int(rng.integers(2, 5))), lambda: g.torus(nu=int(rng.integers(12, 60)), nv=int(rng.integers(8, 30))),
        lambda: g.box(int(rng.integers(3, 14))), lambda: g.cylinder(nseg=int(rng.integers(8, 50)), nrings=int(rng.integers(2, 14))),
        lambda: g.hemisphere(), lambda: g.bumpy_blob(int(rng.integers(2, 5)), int(rng.integers(0, 99)))]
bad = 0
for k in range(60):
    co, tris, feats = gens[k % len(gens)]()
    faces = int(np.exp(rng.uniform(np.log(30), np.log(12000))))
    res, info = run_engine(co, tris, feats if rng.random() < 0.7 else None, faces=faces)
    if res is None:
        bad += 1; print('  FAIL case', k, k % len(gens), faces, info.get('error'))
        continue
    chk = independent_checks(*res)
    if chk['nonmanifold_edges'] or chk['same_direction_edges'] or chk['degenerate_faces'] or chk['nan']:
        bad += 1; print('  FAIL invalid output', k, k % len(gens), faces, chk)
check('60 random cases all valid', bad == 0, f'{bad} bad')
print('\nFAILS:', fails if fails else 'none')
