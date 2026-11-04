# Notes for AI coding assistants and contributors

Read `docs/ARCHITECTURE_FOR_AI.md` first. It has the file map, the data flow between the Python add-on and the C++
engine, the `.fmesh` format, the engine's decision flow, the invariants below in more detail and how to run every test.

## Rules

1. The add-on never modifies the user's original object; all work is on temporary copies that are always cleaned up.
2. The engine (`engine/src`) runs as a separate process. Keep it dependency-free C++17. `quadforge/engine_src` is a
   copy of it and must stay identical.
3. Every result must be a valid mesh: closed when the input was closed, manifold, consistently oriented, no open seam
   after symmetry. Fallbacks must report their reason instead of changing behaviour silently.
4. User-facing text is English and lives in `quadforge/texts.py` (Spanish strings only in `translations.py`).
5. Pure-Python modules (`features.py`, `geometry.py`, `planning.py`, `guidance.py`, `mesh_io.py`, `core/backends/forge.py`)
   must not import `bpy` at module level.
6. Clean-room policy: write code from published papers and observable behaviour. Do not copy code from proprietary
   tools; keep the notice of any GPL code that is reused.
7. Tests must pass before a change is proposed (commands in `docs/ARCHITECTURE_FOR_AI.md`, section 8). Do not weaken a
   test to make it pass; if a test is flaky, say why.
8. Version numbers and releases are chosen by the maintainer: do not bump the version or tag a release in a normal change.

## Quick commands

```bash
python3 tools/build.py linux                                   # build the engine
export FORGE_ENGINE=$PWD/engine/bin/linux/forge
python3 quadforge/devtools/engine_tests/t08_hard_cases.py      # engine hard cases
python3 quadforge/devtools/engine_tests/t07_robustness.py --quick
python3 tools/quality_metrics.py result.fmesh --source model.stl
```
