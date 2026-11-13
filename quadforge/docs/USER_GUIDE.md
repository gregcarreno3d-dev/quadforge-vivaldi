# User guide — QuadForge Vivaldi 0.14.0

## 1. Basic flow (3 steps)
1. Select the mesh (triangles or quads, any density). The add-on switches to Object Mode by itself.
2. In the **QuadForge** panel (sidebar, N key): press **Hard Surface** or **Soft Surface / Organic** (it sets the options for you and stays marked), pick the **Target Faces** from the list (2K to 500K) or type an exact number, then choose **Engine** and **Output**.
3. Press **Remesh**. A progress bar shows in the status bar; **press Esc to cancel** (the original is untouched). The original is hidden (never deleted) when it finishes and `<name>_quads` or `<name>_tris` appears.

Above about 6 million faces (FORGE) Blender asks for confirmation.

## 2. Main options
| Option | What it does |
|---|---|
| **Engine** | *FORGE*, the add-on's own engine (quads or triangles, reads heavy sculpts directly, paintable density). It is the only engine. |
| **Output** (FORGE only) | *Quads* (animation, subdivision, sculpting) or *Triangles* (games, 3D printing, simulation; even, well-shaped triangles). |
| **Target Faces** | Approximate face count of the result (the real count varies by a few percent). |
| **Surface type** | Hard Surface: hard edges from 30°, right angles and snapping on, Detail on Curves Off. Soft Surface / Organic: hard-edge detection off (your own sharp edges and seams still count), Detail on Curves Medium. Neither touches symmetry, zones, output or engine. Changing one of those values by hand unmarks the button. |
| **Target Faces** | A list from 2K to 500K (above 150K it is slow and heavy) plus an exact-number field. The Ladder 20K→4K button is in Extra Levels. |

## 3. Optional panels
- **Flow Guides:** tell the engine where you want the rows of quads to run. In Edit Mode, select edges on your model (for example a loop around an eye or the mouth) and press *Mark Flow Guide*; remesh as usual. It is a soft hint, so it follows your lines where the surface allows it. The minus button unmarks the selected edges, *Clear Flow Guides* removes all of them, and the checkbox in the panel header turns them off without deleting them. With symmetry on the guides are mirrored. FORGE quads only.
- **Hard Edges:** keeps marked corners as clean edge chains. Lower *Angle* = more edges. *Smart Detection* ignores noise smaller than two quads and keeps right angles. Sharp edges and UV seams can also be respected. *Rounded Bevels* also keeps fillets made of several soft facets (each below the angle) and refines them; turn it off if a smooth organic surface gets unwanted edge rows. *Keep Open Borders* keeps outlines of open meshes.
- **Surface Analysis (preview, 0.16):** *Analyze Surfaces* splits a CAD mesh into planes, cylinders, spheres, cones, revolution and free-form surfaces and finds the chamfers (orange) and bevels (blue); colours by type or by surface; *Select Faces* by kind and *Select Borders* isolate parts of it; tables in the texts *QuadForge Surfaces* / *QuadForge Bands*. In Hard Edges, *Use Surface Analysis (experimental)* lets the remesh keep the surface borders (the Hard Surface button turns it on). See `SURFACE_ANALYSIS.md`.
- **Chamfers and Bevels only:** for CAD parts and STL files. Select the mesh and press *Detect Chamfers and Bevels*: chamfers (flat) are painted orange and bevels or fillets (curved) blue. A table with the width, length, turn angle and radius of every band appears in the text *QuadForge Bands*, and each face gets the attribute `qf_band` (0 = none). Sizes are in millimetres. It only analyses: it changes nothing on the mesh and the remesher does not use it yet. Works on the Linux engine for now. Details and limits: `CHAMFER_BEVEL_DETECTOR.md`.
- **Isolate Zones:** separates by material, Face Set, sculpt mask or vertex group (paint areas in different groups, like ZBrush polygroups) with clean loops.
- **Symmetry:** local X/Y/Z axes. One half is remeshed, then mirrored and welded. Check *Plane At* if the origin is not centred.
- **Extra Levels:** `15K, 10K, 5K, 4K` builds several levels in one run (each level is built from the source).
- **Density (FORGE):** pick a *vertex group*; where the weight is 1 the edges are *Detail Ratio* times shorter (4 ⇒ about 16× more faces there). Paint it in Weight Paint mode.
- **Detail by Zone (FORGE):** turn on an option in *Isolate Zones*, press *Refresh Zones*, then give each zone a number: above 1 gives that zone more faces, below 1 fewer. The engine keeps a clean quad flow, so the change is gradual (a 2 gives roughly 1.7 times more faces in the zone) and it is blended over about eight cells next to the zone border.
- **Quick Preview:** makes a small copy (about 3,000 faces, usually a few seconds, about 6 s for a 248,000-face head) with your current settings so you can check the flow before the full run. It never touches your original and is deleted when you run the full Remesh.
- **Subdivision Match (Advanced):** for quads that you will subdivide. A Subdivision Surface modifier shrinks a mesh; this option moves the vertices so the smooth result lands on your original surface (in a test the error after two levels fell about 40 times). Made for smooth, organic shapes: sharp creases stay where they are and no point moves more than a third of an edge.
- **Last Result:** faces, quad %, poles, time, warnings. Poles are not shown for triangles.
- **Advanced:** shading, clean mesh, hide original, copy UV maps, copy Face Sets and mask, .

## 4. Recipes
- **Character for animation:** FORGE, Quads, Character preset, X symmetry.
- **Game or print piece:** FORGE, Triangles, target by budget.
- **More detail on the face:** paint a vertex group on the face, Density, Detail Ratio 3–4. Or give the head its own material and use *Detail by Zone*.
- **Check before waiting:** press *Quick Preview* first; if the flow is right, press *Remesh*.
- **Model for subdivision:** Quads, then turn on *Subdivision Match*.
- **Hard surface:** Hard Edges on, angle 30–40°, Smart Detection.

## 5. When something goes wrong
| Symptom | What to do |
|---|---|
| "The FORGE engine for this system is not inside the add-on" | The executable is missing from `bin/<system>/`; reinstall the zip made for your system (the all-platforms zip has every engine). |
| macOS blocks the executable | It is an unsigned binary: allow it in Settings > Privacy & Security (or `xattr -d com.apple.quarantine bin/macos-*/forge`). |
| Count far from the target | Hard edges and zones add constraints: raise the angle or turn zones off. |
| Many poles in quads | Known limit of 0.5 (see `STATUS_AND_LIMITS.md`). Try Triangles. |
| Slow result | Lower the target faces or use Quick Preview first. |
| Engine error | The message appears in Blender; Blender does not close. Report the message and the mesh size. |
