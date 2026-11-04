"""Compare FORGE and QuadriFlow (through the real add-on pipeline) on the STL models in a folder.

    python tools/compare_engines.py models [target]

Prints poles per 1000 quads, regular-vertex share, quad share and time. Uses the bpy module.
"""
import pathlib, sys, time, json
import bpy, numpy as np
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import quadforge
from quadforge.core import geometry as geo, mesh_io as mio, pipeline

folder = pathlib.Path(sys.argv[1]); target = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
quadforge.register() if False else None
out = []
for f in sorted(folder.glob("*.stl")):
    for backend in ("FORGE", "QUADRIFLOW"):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        before = set(bpy.data.objects)
        bpy.ops.wm.stl_import(filepath=str(f))
        obj = (set(bpy.data.objects) - before).pop()
        bpy.context.view_layer.objects.active = obj; obj.select_set(True)
        s = pipeline.RemeshSettings(target_quads=target, backend=backend, output="QUADS", hide_original=False)
        t = time.perf_counter()
        try:
            r = pipeline.run(bpy.context, obj, s)
        except Exception as e:
            print(f"{f.stem[9:30]:22s} {backend:10s} FAILED {e}"); continue
        dt = time.perf_counter() - t
        arrays = mio.read_mesh_arrays(r.object.data)
        st = geo.mesh_stats(arrays)
        val = geo.vertex_valence(arrays)
        interior = val > 0
        reg = float(np.count_nonzero(val == 4)) / max(1, np.count_nonzero(interior))
        poles = st["poles_3"] + st["poles_5plus"]
        row = dict(model=f.stem[9:30], backend=backend, faces=st["faces"], quads=st["quads"], poles=poles,
                   poles_per_1k=round(1000 * poles / st["faces"], 1), regular=round(100 * reg, 1), seconds=round(dt, 2))
        out.append(row); print(row, flush=True)
json.dump(out, open(ROOT / "models" / "compare.json", "w"), indent=1)
