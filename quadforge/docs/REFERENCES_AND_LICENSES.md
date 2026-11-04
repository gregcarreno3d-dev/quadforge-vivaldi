# References and licenses

Sources are separated by how far they can be trusted: some were **consulted during this work** (real links),
others come from the **author's knowledge** and must be verified before being cited in formal work.
Titles and descriptions are paraphrased; consult each source for the original text.

## 1. Sources consulted during the study (October 2026)

### Problems with Blender's engine (QuadriFlow)
| Topic | Link |
|---|---|
| Performance with very dense meshes, RAM usage and cancellation (#70358) | https://projects.blender.org/blender/blender/issues/70358 |
| Blender crash with QuadriFlow and preserving curvature (T69866) | https://developer.blender.org/T69866 |
| Regression: crash with QuadriFlow (#105953) | https://projects.blender.org/blender/blender/issues/105953 |
| User feedback on QuadriFlow (speed, symmetry) | https://devtalk.blender.org/t/quadriflow-feedback/9605 |

### Research
| Topic | Link |
|---|---|
| Quads guided by feature lines (QuadWild / BiMDF line) | https://dl.acm.org/doi/10.1145/3450626.3459941 |
| Interactively controlled quad remeshing of high-resolution models (multiresolution) | https://dl.acm.org/doi/10.1145/2980179.2982413 |
| General survey: quad mesh generation and processing | https://www.researchgate.net/publication/261849625_Quad-Mesh_Generation_and_Processing_A_Survey |
| PaMO: parallel mesh optimization on the GPU (triangle decimation) | https://arxiv.org/pdf/2509.05595 |
| GPU-accelerated surface remeshing (restricted tangent face, triangles) | https://www.sciencedirect.com/science/article/abs/pii/S0167839623000481 |

### Tools
| Topic | Link |
|---|---|
| Quad Remesher quick guide for Blender (Exoside) | https://exoside.com/?p=5402 |
| Quad Remesher announcement (2019) | https://www.cgchannel.com/?p=106258 |
| AutoRemesher and its Blender bridge (July 2026) | https://www.cgchannel.com/2026/07/get-free-retopology-tool-autoremesher-and-its-blender-bridge/ |

### Mentioned in search results, no verified link
- **SQuadGen** (diffusion for simple quad layouts, ACM TOG, 2026) and **Dmsh** (multi-agent reinforcement learning
  for quad-only meshes, 2026): they appeared in a results list; **the exact title and link still need to be located**
  before citing them.

## 2. References from the author's knowledge (verify before citing)

- QuadriFlow: Huang, Zhou, Nießner, Shewchuk, Guibas. *QuadriFlow: A Scalable and Robust Method for Quadrangulation*, 2018.
- Instant Meshes: Jakob, Tarini, Panozzo, Sorkine-Hornung. *Instant Field-Aligned Meshes*, 2015.
- QuadWild: Pietroni, Nuvoli, Tarini, Livesu and others (repository and associated papers).
- Repositories: QRemeshify (https://github.com/ksami/QRemeshify, as stated in its manifest).

## 3. Licenses and what they imply for this project

| Component | License | Note |
|---|---|---|
| QuadForge | GPL-3.0-or-later | Own code. Intended to be reused and improved. |
| Quad Remesher bridge for Blender (v1.4) | GPL v2 or later | The **bridge** only. Its workflow and options were studied; no code was copied. |
| Quad Remesher engine (Exoside) | Proprietary | **Not studied or decompiled.** It is not part of this work. |
| QRemeshify | GPL-3.0-or-later (per its manifest) | Its workflow and symmetry technique were studied; no code was copied. |
| QuadWild (libraries) | **Verify** in its repository before redistributing binaries | Some variants depend on components with their own licenses. |
| AutoRemesher | MIT (per the cited press note) | Backend candidate; **verify** in its repository. |
| QuadriFlow built into Blender | Part of Blender (GPL) | Used through the public operator `object.quadriflow_remesh`. |

**Clean development policy:** QuadForge's code was written from **observable behavior** and general ideas
(cutting by symmetry, marking hard edges, per-zone constraints). If GPL code from another project is reused in the future,
its authorship notice must be preserved and declared in the file header.

*Note: this is not legal advice. Before redistributing third-party binaries, check each one's license.*
