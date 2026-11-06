# Testing guide for QuadForge Vivaldi 0.11.1

Thank you for testing. The engine passes its automated tests on Linux, Windows and macOS in continuous integration, and the
add-on was run on Blender 4.2, 4.5, 5.1 and 5.2 on Linux. What has **not** had real use is Windows and macOS on real
machines and your own models. That is what this guide is for. Plan about 30 minutes.

## Install

1. Download the zip for your system from the release (`windows-x64`, `macos-arm64` for Apple Silicon, `macos-x64` for
   Intel, `linux-x64`).
2. Blender: `Edit > Preferences > Get Extensions > ▾ > Install from Disk…`, pick the zip, enable it.
3. 3D Viewport, press **N**, tab **QuadForge**.

If the engine does not start, the panel shows a message that says why; please copy it into your report.

## What to try

Use your own models where you can: a character or creature sculpt, a hard-surface part, and one messy mesh.

1. **Basic run.** Select a mesh, *Soft Surface (Organic)*, target 5,000 quads, *Remesh*. Does it finish, and does the flow look sensible?
2. **Quick Preview.** Press *Quick Preview* on a heavy sculpt (100K faces or more). It should take a few seconds and give a small copy. Then press *Remesh*: the preview should disappear.
3. **Hard Surface.** *Hard Surface*, a part with sharp edges. Are the edges kept?
4. **Symmetry.** Turn on X symmetry on a symmetric model. Is there an open seam in the middle? (Edit Mode, select non-manifold.)
5. **Detail by Zone.** Give two parts different materials, turn on *Isolate Zones > Materials*, press *Refresh Zones*, set one zone to 2. Does that zone get more faces, and is the border clean?
6. **Density.** Paint a vertex group on a face, choose it under *Density*. Do the painted areas get smaller faces?
7. **Subdivision Match** (Advanced, quads only). Remesh with it on, then add a Subdivision Surface modifier (level 2). Compare with the original: does the smooth result sit on the sculpt?
8. **Esc.** Start a large remesh (100K quads) and press Esc. Does it stop cleanly?
9. **Your worst model.** Open surfaces, loose parts, very thin parts, very dense (1M faces). Anything that fails or looks wrong is the most valuable report.

## How to report

Open an issue at the repository, or write to the person who sent you the zip, with:

- your operating system and Blender version (Help > About Blender);
- the QuadForge version (0.11.1) and the settings you used (a screenshot of the panel is enough);
- what you expected and what happened; the **Last Result** box and any yellow warning text, copied exactly;
- if you can share it, the model (or a cut-down version) that fails.

You do not need to find a problem to be useful: "tested on Windows 11, Blender 4.5, items 1 to 8 worked" is a good report too.

## Releasing

Releases are automatic. Update `version` in `quadforge/blender_manifest.toml` and add a matching `## <version>` section to `quadforge/CHANGELOG.md`, push, then push a tag `v<version>` (or run the *Release* workflow by hand with that tag). The workflow builds the engine on Linux, Windows, macOS arm64 and macOS x64, runs the 29 engine hard cases on each, packages the five zips, checks that the tag matches the manifest and publishes the GitHub release with the changelog section as its notes.
