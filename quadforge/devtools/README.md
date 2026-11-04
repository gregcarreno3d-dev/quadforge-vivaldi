# devtools

Scripts used to build and measure the engine. They are copied from the development workspace.

| File | Purpose |
|---|---|
| `package.py` | Builds the distributable zips (all platforms + one per platform) into `../dist/`. |
| `harness.py`, `fmesh.py`, `meshgen.py`, `stlio.py` | Run the engine on test meshes, validate results with NumPy, read STL, generate shapes. |
| `engine_tests/t01..t06` | Engine tests (spheres, shapes with features/borders/density, mesh repair fuzz, quads, quad fuzz, lattice extraction). Set `FORGE_ENGINE` to the executable to test (default `bin/linux/forge`). |
| `engine_tests/t07_robustness.py` | Success-rate suite: SDF-built shapes x sizes through the engine (`--quick`, `--full`, `--big`). |
| `exp_poles.py` | Prints valence/pole statistics of the quad output on `models/*.stl` (put models in `../models/`). |
| `compare_engines.py` | Compares FORGE with QuadriFlow through the real add-on, inside Blender (`bpy` module). |

Rebuild the engine with `python ../engine_src/build.py` (needs `pip install ziglang`).
