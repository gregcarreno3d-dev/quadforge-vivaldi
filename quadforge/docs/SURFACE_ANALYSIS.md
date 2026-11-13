# Surface analysis (QuadForge 0.16, preview)

Splits a CAD-style mesh (triangulated STL, millimetres) into its **surfaces**, the way a CAD program sees the part,
and lists the **borders** between them. Chamfers and bevels (see [CHAMFER_BEVEL_DETECTOR.md](CHAMFER_BEVEL_DETECTOR.md))
are surfaces of their own. In Hard Surface mode the remesher can use the result (experimental, switchable).

## 1. What you get

| Item | Meaning |
|---|---|
| **Surface** | A connected area of the model with one geometric description: `plane`, `cylinder` (also coarse polygonal ones), `sphere`, `cone`, `revolution` (a wall turned about an axis, for example a spool), `freeform`, `chamfer`, `bevel` |
| **Border** | Edges between two surfaces with length and mean angle. Kinds: `sharp` (corner), `tangent` (smooth join), `band` (a side of a chamfer or bevel) |
| Surface data | area, perimeter, radius, sweep angle (cylinders), axis, centre, bounding box, patches, triangles, fit error |

## 2. In Blender (N > QuadForge > Surface Analysis)

1. **Analyze Surfaces** on the selected mesh. The mesh is painted and four attributes are written:
   `qf_surface` (face: surface id), `qf_surface_kind` (face: kind code), `qf_border` (edge: 0 none, 1 sharp, 2 tangent, 3 band),
   `qf_band` (face: chamfer/bevel number from 1, 0 = none).
2. **Colours:** *By Type* (plane grey-blue, cylinder green, sphere purple, cone yellow, revolution teal, free-form pink,
   chamfer orange, bevel blue) or *By Surface* (one colour per surface). Switching is instant.
3. **Isolate:** *Select Faces* by kind (all chamfers, all cylinders ...) and *Select Borders* (all, sharp, tangent, bevel sides) enter Edit Mode with the selection.
4. Tables: text datablocks **QuadForge Surfaces** (every surface and border, sizes in mm) and, from *Chamfers and Bevels Only*, **QuadForge Bands**.
5. **Hard Edges > Use Surface Analysis (experimental)**: the remesh runs the analysis first and keeps the sharp borders and
   (option *Keep Chamfer / Bevel Sides*) both sides of every band as hard edges, so the quad rows follow the real limits of
   every wall. The *Hard Surface* button turns it on; Organic turns it off. Compare with it on and off.

## 3. How it works (`forge surfaces`, `engine-rs/src/stages/surfaces.rs`)

1. Weld, triangulate and run the band detector (`bands.rs`); band triangles are set aside.
2. **Coplanar patches:** region growing on a plane (normal within 3 degrees, vertices within 0.2 % of the part diagonal), biggest triangles first.
3. **Surfaces of revolution** about an axis-aligned axis through the middle of the part: patches whose normal has no tangential component
   and that go round the axis. Radius against height decides cylinder (constant), cone (straight line), sphere (all points on one sphere) or revolution.
4. **Cylinders:** a seed is a pair of neighbouring patches whose shared edge runs along their normals' cross product. Patches are added
   while all vertices stay on one circle (least squares) and the normals stay perpendicular to the axis. Three patches are enough, so a
   14-sided polygonal hole is one cylinder and not 14 planes. Coaxial cylinders that touch merge into one.
5. **Cones, spheres, free-form** on the remaining smooth patches (four or more). A cone is fitted first, from the planes of the facets (they meet at one apex and their normals lie on a small circle); then a sphere (least squares, facets must face away from the centre); otherwise free-form. Neighbouring spheres with the same centre and radius merge.
6. Coplanar neighbours merge; fragments smaller than 0.05 % of the area join the neighbour they touch the most.
7. **Borders** between surfaces, classified by their mean angle (>= 25 degrees sharp) or as band.

Command: `forge surfaces --in part.fmesh [--json out.json] [--sharp 25] [--max-width W] [--min-width W] [--tol 0.002] [--plane-deg 3] [--smooth 50]`.
Prints `SF <json>` and `OK`. The JSON has `surfaces`, `borders`, `bands` and (file only) `face_surface`.

## 4. Known limits

- The revolution search tries the X, Y and Z axes through the bounding-box centre; a turned part with a tilted or off-centre axis is split into several cylinders.
- Faceted cones, also tilted ones, are detected by their planes; a cone made of very few facets can still be taken for planes.
- Fillets of many very thin facets and coarse fillets of a few facets are still partly detected (see the detector document).
- Detection assumes consistent triangle orientation; tolerances are relative to the part size, so a model with tiny details next to a huge body can lose them.
- The Windows and macOS binaries checked into the repository are older builds without `surfaces`; releases built by CI include them.
- Use Surface Analysis can leave a few triangles (about 2 % of the faces on real CAD parts, mostly isolated ones); neighbouring triangle pairs are joined into quads when they lie on one smooth patch. Compare with it off.

## 5. Roadmap

Field alignment to cylinder axes and plane borders, band-width driven row counts, tilted-axis revolutions, removing the isolated triangles, per-surface quality report.
