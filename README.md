# QuadForge Vivaldi

A Blender add-on for fast quad and triangle remeshing, powered by **FORGE**, its own C++ engine.

![Examples: an organic head at 20K quads, a hard-surface flange at 5K quads with creases kept, and the same flange without hard-edge detection](docs/images/examples.png)

- Quads or triangles, from 2K to 500K faces, with a one-click **Hard Surface** / **Soft Surface (Organic)** setup
- Reads heavy sculpts directly, keeps hard edges, symmetry, UV maps, Face Sets and materials
- Isolate zones by material, Face Set, mask or vertex group
- Paintable density through a vertex group; smaller faces on thin parts and, optionally, on strongly curved details
- Quick Preview before the full run, detail per zone, and an option that makes the result match your sculpt after Subdivision Surface
- Exact symmetry (no open seam), several levels of detail in one run, cancel with Esc
- English and Spanish interface
- Blender 4.2 or newer (developed and tested on Blender 5.2). GPL-3.0-or-later.

## Install

Download the zip for your system from the **Releases** page, then in Blender:
`Edit > Preferences > Get Extensions > ▾ > Install from Disk…`

The panel appears in the 3D Viewport sidebar (**N**), **QuadForge** tab.

## How it works

Many people ask how a quad remesher actually works, because the tools rarely explain themselves. FORGE follows the
standard modern recipe for field-aligned quad meshing, written from scratch in about 5,500 lines of dependency-free
C++17. In short:

![FORGE pipeline](docs/images/pipeline.svg)

1. **Prepare (Python, in Blender).** The add-on reads the mesh and decides the constraints: hard edges found as
   *chains* of sharp edges (so noise is ignored and real creases are kept), zone borders, the symmetry plane, painted
   density and flow guides. It writes everything to a small binary file and starts the engine as a separate process, so
   a failure can never crash Blender and Esc simply stops it.
2. **Repair and resample.** The engine makes the mesh manifold, closes open borders with temporary caps, and remeshes it
   into a uniform triangle mesh (isotropic remeshing, after Botsch and Kobbelt) with about six triangles per future quad
   cell. A size field makes the triangles smaller where the shape is thin or strongly curved.
3. **Choose where the rows run: the cross field.** A smooth field of four orthogonal directions is computed on every
   face by minimising a Dirichlet-like energy on the complex number *z = exp(4iθ)*. Creases and borders fix it; the
   smoothest field is found by inverse iteration. The singularities of this field are exactly where the irregular
   vertices (poles) of the final mesh will be.
4. **Unfold into a square grid: integer-grid parametrization.** The surface is cut along a graph that ends at the
   singularities and mapped to the plane so that the field becomes the grid directions. The map is found by a sparse
   least-squares solve; the translations across cuts and the singular points are then rounded to integers gradually,
   re-solving as it goes. The integer lines *u = k* and *v = k* are the quad edges.
5. **Extract and refine.** Each vertex snaps to its nearest lattice node, vertices that land on the same node merge
   into a cell, and faces are traced around the cell graph. Every cell is split 2×2, relaxed on the surface, snapped to
   the marked creases, and the worst quads are repaired. The lattice spacing is corrected up to three times to land
   within a few percent of the requested count.
6. **Finish (Python).** Sliver quads on thin bevels are collapsed, the symmetry cut is mirrored and welded, and UV maps,
   Face Sets, mask and materials are carried over.

If the grid method fails for a shape (an unclosable border, a size gradient the surface cannot follow, the time limit),
the engine falls back to a **triangle-pairing** method and tells you why. FORGE is a heuristic: it prefers to report a
problem or fall back rather than return an invalid mesh.

### Read more

| Document | For |
|---|---|
| [**Technical report**](docs/TECHNICAL_REPORT.md) | The method in depth: equations, algorithms, evaluation against Blender's QuadriFlow and Decimate, limitations, how FORGE differs from the published methods, and references |
| [**Architecture for AI assistants**](docs/ARCHITECTURE_FOR_AI.md) | A dense, exact map of the code for any AI (or contributor) that needs to understand or change it: file map, data flow, formats, decision flow, invariants, tests |
| [User guide](quadforge/docs/USER_GUIDE.md) | Using the add-on |
| [Testing guide](docs/TESTING_GUIDE.md) | A 30-minute checklist for people trying the add-on on their own machine and models |
| [Status and limits](quadforge/docs/STATUS_AND_LIMITS.md) | What is tested, measured and not done yet |
| [Changelog](quadforge/CHANGELOG.md) | Version history |

This is not a peer-reviewed paper and makes no claim of a new algorithm: it is an engineering implementation of
published ideas ([references in the report](docs/TECHNICAL_REPORT.md#11-references)). If you use it in research,
GitHub's **Cite this repository** button uses [`CITATION.cff`](CITATION.cff).

## Measured quality

Against Blender's built-in QuadriFlow at 20,000 faces on five sculpts (two CPU cores): better squares (scaled
Jacobian) on four of five models, the requested face count hit more closely, and faster on three of five. QuadriFlow
still produces fewer irregular vertices at 80,000 faces and above. Triangle output follows the surface about twice as
closely as Decimate, with near-equilateral triangles. The full tables, the metric definitions and a reproducible check
on meshes you can regenerate are in the [technical report](docs/TECHNICAL_REPORT.md#7-evaluation).

## Status

The FORGE engine is validated most on Linux with Blender 5.2. Engines for Windows and macOS (Intel and Apple Silicon)
are built by the CI workflow and pass the engine tests there, but have had little real-world use: please open an issue
if something fails.

## Folders

- `quadforge/` the add-on (Python) and its documentation
- `engine/` the FORGE engine source (C++17)
- `docs/` technical report, architecture for AI assistants, figures
- `tests/`, `tools/` tests, build scripts and quality metrics

## License

GPL-3.0-or-later. See `LICENSE`.
