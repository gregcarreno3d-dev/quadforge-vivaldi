# QuadForge Vivaldi

A Blender add-on for fast quad and triangle remeshing, powered by **FORGE**, its own C++ engine.

- Quads or triangles, from 2K to 500K faces, with a one-click **Hard Surface** / **Soft Surface (Organic)** setup
- Reads heavy sculpts directly, keeps hard edges, symmetry, UV maps, Face Sets and materials
- Isolate zones by material, Face Set, mask or vertex group
- Paintable density through a vertex group
- English and Spanish interface
- Blender 4.2 or newer (developed and tested on Blender 5.2)

## Install

Download the zip for your system from the **Releases** page, then in Blender:
`Edit > Preferences > Get Extensions > ▾ > Install from Disk…`

The panel appears in the 3D Viewport sidebar (**N**), **QuadForge** tab.

## Status

The FORGE engine is validated on Linux with Blender 5.2. The Windows and macOS engines are built but have had little testing: please open an issue if something fails.

## Folders

- `quadforge/` the add-on (Python) and its documentation
- `engine/` the FORGE engine source (C++17)
- `tests/`, `tools/` tests and build scripts

## License

GPL-3.0-or-later. See `LICENSE`.
