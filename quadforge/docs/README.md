# QuadForge documentation

Index, in the recommended reading order:

| Document | What it is for |
|---|---|
| [TECHNICAL_STUDY.md](TECHNICAL_STUDY.md) | The study: problem, existing tools, state of the art, design, CPU/RAM/GPU budget, languages, limits |
| [CASE_BULL_ANT.md](CASE_BULL_ANT.md) | Real case: data measured in a test with a bull ant, defects found and changes made |
| [PRESETS_AND_WORKFLOW_STUDY.md](PRESETS_AND_WORKFLOW_STUDY.md) | Study (not implemented): Hard Surface / Organic / Automatic profiles, two-step workflow and reset to a safe baseline |
| [DECISIONS.md](DECISIONS.md) | Each design decision with its reason and its status ("to validate" or "current") |
| [BENCHMARK.md](BENCHMARK.md) | How to measure (time, memory, detail, symmetry, quality) and where to record results |
| [OPEN_RESEARCH.md](OPEN_RESEARCH.md) | Open questions, roadmap and how to contribute |
| [REFERENCES_AND_LICENSES.md](REFERENCES_AND_LICENSES.md) | Sources (verified and to be verified) and license notes |

Convention used in all documents: **[verified]** = checked against a source or a measurement ·
**[assumed]** = reasonable but unchecked · **[to measure]** = resolved with the benchmark.

Related code: `tests/blender_integration_test.py` (checks that it works), `tests/benchmark.py` (measures),
`tests/test_*.py` (pure rules, no Blender), `tests/research_model_analysis.py` (STL model analysis with NumPy, used in the studies; does not need Blender).
