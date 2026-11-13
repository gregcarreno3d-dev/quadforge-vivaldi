# Changelog

## 0.15.5
- Chamfer and bevel detector: a flat or a ridge cut into a cylinder wall (for example a D-shaped flat inside a hole) is no longer reported as a chamfer. A band is now rejected when both of its flanks are curved about the same axis as the band itself, which means they are one wall and not two surfaces.
- Debug output (`FORGE_BANDS_DEBUG=1`) also lists the accepted bands with their measurements.

## 0.15.4 - Chamfer and bevel detector, fourth preview

- Parts with rounded vertical corners plus fillets on the top and bottom edges (a rounded post on a plate) no longer melt into one curved network: strips are separated by the axis they turn around, rounded corner patches are shared out to the neighbouring strip and touching strips of the same kind and size are reported as one band.
- Convex / concave is now judged from how the two neighbouring surfaces diverge, which no longer depends on triangle orientation.
- Big flat facets next to a bevel are never pulled into it.

## 0.15.3 - Chamfer and bevel detector, third preview

- Tiny slivers (below 0.3 % of the part size, such as a 0.05 mm land under a chamfer) are no longer reported as bands, so a chamfer is painted completely or not at all instead of in pieces.
- The detector now runs at several scales (widest band 4 %, 5.6 %, 8 % and 11 % of the part diagonal), so large soft rounds and wide chamfers that were left out are found too; finer results win where scales overlap, and wide strips must turn at least 20 degrees to count.
- New option `--min-width` of `forge bands`. Still preview only: remeshing does not use the detector yet.

## 0.15.2 - Chamfer and bevel detector, second preview

- The detector now also finds small rounded bevels (fillets of ~0.3 mm on rims) and measures each band on its own, so one model can have several chamfers and bevels of different angle and size; every band is reported with its own width, turn angle and radius.
- Width and turn are measured across the band with a distance map from its two edges, so long thin CAD triangles no longer confuse it.
- Still preview only: remeshing does not use the detector yet.

## 0.15.1 - Chamfer and bevel detector (preview)

- New engine command `forge bands`: finds chamfers (flat strips) and bevels / fillets (curved strips) on a triangulated CAD mesh, with width, length, turn angle, radius and convex/concave side. No feature marks needed.
- New panel "Chamfers and Bevels (preview)" with the operator *Detect Chamfers and Bevels*: paints chamfers orange and bevels blue and writes a table (in millimetres, unit guessed from the scene or CAD-sized parts) into a text called "QuadForge Bands".
- Remeshing does not use the detector yet; this version is for checking what it finds.

## 0.15.0 - 2026-11-10

New
- **The FORGE engine is now written in Rust.** A full port of the C++ engine, stage by stage, with the same `.fmesh` format and the same stdout protocol, so the add-on needed no change. The code is organised by work area (`engine-rs/README.md` is the map: repair, size fields, remesh, cross field, parametrization, extraction, finishing). It is built natively on Windows, Linux and both Macs. The C++ engine stays in the repository as a reference but is no longer shipped.
- **Big hole patches are cut into quad ladders** instead of centroid stars. A hole polygon with six or more sides used to become one pole of valence 6 to 12; now it is a strip of quads and no pole above 7 remains on the ant test (minimum angle 20 to 38 degrees).
- **Two marked lines closer than about three quarters of a cell** (the two edges of a narrow chamfer) are no longer both used as lattice rows; the longer one wins. On the cylinder test with thin chamfers the quads with a bad angle fell from 7,184 to 4,893 at 120,000 faces.
- **Closed loops of thin quads are collapsed** (strips that end in no triangles), which removes about 40 % of the badly shaped quads at 20,000 faces on the same test.
- Faster linear solver and a parallel conjugate-gradient team for big meshes: the 120,000-face cylinder test takes 38 s (the C++ engine took 66 s). Small meshes are 10 to 20 % slower than the C++ engine.

Fixed
- Isotropic remeshing no longer joins two border vertices with a chord, which could leave a flat triangle on the symmetry plane and an open seam after mirroring.
- A grid pass that closes far from the requested count is dropped when the next pass fails outright, so the engine retries without the thickness refinement instead of returning a mesh with a third of the faces.
- The flow-guide test compares against the mean of three sizes instead of one lucky size (the threshold is unchanged).

## 0.14.0 - 2026-11-07

New
- **Badly shaped quads are relaxed and projected back on the surface.** After the engine, only the vertices of quads with a very sharp or flat corner (corner sine below 0.55) are smoothed, then projected onto your source. Boundary and hard-edge vertices never move, and any move that makes a quad worse or folds it is reverted. On the cylinder test at 20,000 faces the skewed quads fell from 486 to 356 with the surface deviation unchanged.

Changed
- **QuadriFlow is gone from the add-on.** QuadForge now runs only on its own FORGE engine. Removed: the QuadriFlow backend and the `QUADFORGE_LEGACY_ENGINE` switch, the working copy, reprojection, polish passes, seed and corner-snapping settings, the *Speed and Detail* panel and their texts and tests. Old scenes that still store these values simply ignore them.
- Very dense meshes only ask for confirmation above 6 million faces (FORGE reads dense sculpts directly).

## 0.13.1 - 2026-11-07

Fixed
- **Odd patches around the eyes and nose of faces and creatures** (seen on the Hada and female head tests): Rounded Bevels took eyelid and nose folds for mechanical bevels. Bevels are now only looked for when at least 15 % of the surface is truly planar (no face leaning more than 3 degrees from its plane), so organic shapes behave exactly as with the option off.
- Thin chamfer strips are refined harder first (rows closer to clean); when the engine's grid method cannot follow, the add-on retries with a gentler refinement, so the gear-like and thin-plate cases still use the grid method.
- The bevel detector no longer reports bands that were discarded, which pushed some parts onto the engine's pairing fallback.

## 0.13.0 - 2026-11-07

Changed
- **FORGE is now the only engine offered.** The *Engine* selector is gone and so are the panels that only served QuadriFlow's route (working copy, polish, seed). Scenes saved with QuadriFlow selected run FORGE. The QuadriFlow backend stays in the code for development (`QUADFORGE_LEGACY_ENGINE=1`) until it is removed.
- The "density variation was only partly followed" warning now appears only when you asked for density (vertex group or zone detail), not when the add-on refined thin strips by itself.
- If the engine file is missing, the message now says to install the zip made for your system.

## 0.12.0 - 2026-11-07

New
- **Rounded Bevels** (Detection, on by default): fillets built from several soft facets (each below the sharp angle, together turning the surface by more than it) are now recognised as bevels. Their two borders are kept as quad rows, and the facets get a finer target size so the rounded profile survives the remesh. Found with a spool/pulley model whose soft bevels with hard chamfers were flattened (deviation p99 0.106 % of the model size at 20,000 quads).
- Thin strips between hard edges (chamfers narrower than about three quads, longer than twelve) get a smaller target size, so their quad rows no longer zigzag.

Fixed
- Smooth surfaces (ellipsoids, heads) are never taken for bevels: a band must turn the surface by 150 degrees or less and be at most five quads wide.

## 0.11.1 - 2026-11-06

Fixed
- Seam corners on the centre symmetry line are only merged when the resulting quad stays well shaped, so flattened diamonds no longer appear on thin or flat regions. Quads with two corners on the same spot (a triangle in disguise) are welded into a triangle. The cleanup still runs on a trial copy and is skipped if the surface would get less closed.
- Flat-shaded imports (FBX, OBJ smoothing groups) no longer turn almost every edge into a hard edge (see 0.11.0 notes below; included in the published 0.11.0 release build).
- Test scripts no longer depend on an absolute path, so the per-Blender-version CI jobs run.

Tooling
- Tags `v*` now build the four engines, package the zips and publish the GitHub release automatically.

## 0.11.0 - 2026-11-05

New
- **Quick Preview** button: a small copy (about 3,000 faces, a few seconds; about 6 s for a 248,000-face head) made with your current settings, to check the flow before the full run. Your original is not touched; the preview is replaced by the next one and deleted when you run the full Remesh.
- **Detail by Zone**: after *Refresh Zones*, give each zone (material, Face Set, mask or vertex group) its own detail level. The engine keeps a clean quad flow, so a factor of 2 gives roughly 1.7 times more faces in the zone, blended over about eight cells at the border.
- **Subdivision Match** (Advanced, off by default): moves the vertices of a quad result so that, once Subdivision Surface is added, the smooth surface lands on the original instead of shrinking inside it (test: error after two levels 0.084 % to 0.002 % of the model size). Symmetry stays exact; creases stay put.

Engine
- Much faster on dense, round inputs with a low target: the local-thickness search started at the size of the whole model, and from the centre of a ball-like shape every triangle is equally far. It now starts at the radius that matters. An 82,000-triangle sphere to 3,000 quads went from 61 s to 0.9 s, and to 20,000 quads from 60 s to 4 s. All test results are unchanged.
- A density gradient is no longer dropped when the model already sits near the fit limit because of its many constrained borders (zones, creases).

Fixed
- Flat-shaded imports (FBX, OBJ smoothing groups) mark almost every edge sharp. When more than 25 % of the edges are already marked sharp and the sharp angle is on, those marks are now ignored (with a warning) and the angle decides. Three hard-surface cylinder FBX files went from 15,664 / 35,129 faces for a 5,000 target (55 s) to 5,120 / 5,150 (1 to 3 s in the add-on test).
- Centre symmetry line (Quick Preview and full run): the seam no longer keeps zero-area folded faces, stray wire edges or loose vertices, and the valence-2 / valence-6 vertex pairs that the half-mesh border left on the line are merged into 3 / 5 (on a real head: 2 of 6 seam poles gone, 0 broken edges). The cleanup runs on a trial copy first and is applied only if the surface does not get more open or non-manifold, so hard-surface results are unchanged (all suites and the 67-case matrix pass).
- With symmetry, a large hole on the centre seam (several quads lost together, 14 or more open edges) is closed with triangles instead of left open. A stress test that deletes 1 to 4 quads next to the seam failed in 25 of 150 cases before and in none now.

Testing
- Tested on Blender 4.2.0, 4.5.15 LTS, 5.1.2 and 5.2 through the `bpy` module (all suites and the 67-case matrix pass); CI now runs the add-on on 4.2, 4.5 and 5.1.
- Four new engine hard cases (thin plate, long rod, finned sphere, hollow double-wall shell): 29 in total. New add-on tests for the preview, Detail by Zone, Subdivision Match and a synthetic head.
- The manifest declares the `files` permission (temporary files to run the bundled engine).

Tried and not adopted
- Multigrid solver for the parametrization: 4 times slower than the conjugate gradient at 100,000 quads (141 s against 32 s).
- Local pole clean-up: only 2 to 4 adjacent 3-5 pole pairs per 20,000 quads; not worth the risk.

## 0.10.31

- Repository clean-up for the public release: the root README, no personal name in the public docs, an up-to-date publishing note, and the GitHub Actions file now uses current runners (macOS Intel and Python 3.13 for the Blender tests).
- No change in how the add-on or the engine behaves.

## 0.10.30

- Fixed: the clean-up of squeezed quads from 0.10.28 also ran on shapes that had none and added a few triangles (40 on the test plate at 120K). It now runs only when tiny edges are at least 1.5% of all edges, which is what real thin-bevel strips look like. Plates and similar parts are 100% quads again; bevelled flanges are still cleaned.

## 0.10.29

- **New: Isolate Zones by Vertex Group.** Paint areas in different vertex groups (weight paint, like ZBrush polygroups) and turn on By Vertex Group: the quad flow stops at the border between groups with a clean edge loop. Each face goes with the group that has the most weight on it; Group Cutoff sets the minimum weight. It combines with materials, Face Sets and mask. Zones too small for one face are reported by group name.
- New test `tests/blender_vgzones_test.py`.

## 0.10.28

- **Hard Surface: no more squeezed quads.** Thin bevels (narrower than a face) used to leave strips of quads squashed to almost zero width, up to 25% of the faces on a bevelled flange. They are now collapsed after the engine runs (only edges are collapsed, so the mesh stays closed), and if more than 5% of the faces disappeared the engine runs once more with a higher target so the final count still matches what you asked for.
- Hard Surface keeps creases about 10 times closer than with hard-edge detection off (measured on a flange, a bracket and a nut).
- More robust on Blender 4.2: sharp-edge and seam flags fall back to the attribute route if the old property is missing.
- Clean-install test: the zip installs, registers, remeshes and unregisters in a fresh Blender.
- New test `tests/blender_hard_test.py`. The zone test no longer fails by chance when a tiny zone survives.

## 0.10.27

- Fixed: with symmetry on, half of the result stayed selected in Edit Mode (it looked like a different, stretched mesh). The result now has nothing selected.

## 0.10.26

- **New top of the panel.** Two buttons, **Hard Surface** and **Soft Surface / Organic**, configure the add-on for that kind of shape. The button you used stays marked (like Quads / Triangles), and it unmarks by itself if you change one of the values it set. Hard Surface: hard edges from 30 degrees, right angles and snapping on, Detail on Curves Off. Soft Surface / Organic: hard-edge detection off (your own sharp edges and seams are still kept), Detail on Curves Medium. They never touch symmetry, zones, density, output or engine.
- **Target Faces list** from 2K to 500K (2K, 3K, 4K, 5K, 6K, 8K, 10K, 15K, 20K, 30K, 40K, 50K, 60K, 80K, 100K, 120K, 150K, 200K, 300K, 500K), with the current size marked, plus the exact-number field. Above 150K the panel says it is slow and heavy.
- Below that, Engine and Output as before. The old quick-preset grid is gone; the Ladder 20K to 4K button moved to the Extra Levels section.
- New test `tests/blender_surface_test.py`.

## 0.10.25

- **Zones (materials, Face Sets, mask) polished.** Tested on the fairy with a large zone, a small one and ones smaller than a quad: the zone borders are very faithful (median distance to the original border 0.01 of a quad, wrong-zone faces 0.07 %) and a strip narrower than a quad stays as a row of quads.
- **Copy Face Sets and Mask (new, on by default, Advanced panel):** the sculpt Face Sets (polygroups) and the sculpt mask are copied onto the result, quads and triangles, with or without symmetry, so the result goes straight back into Sculpt Mode with the same groups. Before, the materials survived but Face Sets and mask were lost.
- **Zones that vanish are reported:** a zone smaller than one quad at the chosen size has no faces in the quad result. The panel now says so, by name when one zone option is on ("1 of 4 zones has no faces ... (material eyes)") and suggests raising the target faces. Triangle results keep tiny zones, so nothing is reported there.
- New test `tests/blender_zones_test.py`.

## 0.10.24

- **Copy UV Maps (new, on by default, Advanced panel):** the UV maps of the original are copied onto the result, for quads and for triangles, with or without symmetry. Each new corner is projected onto the original surface and takes the UV of that point; the projection is limited to the UV island under the face centre, and faces that straddle a seam inside one island (a sphere, a tube) pick the side of the seam that matches the face, so they are not smeared across the map. Before, the result had no UVs at all.
- Measured: UV sphere (one seam) 0 % of faces spanning the seam, latitude error 0.002 (p99); Suzanne with Smart UV Project (many islands): median error 0.0002 % of the size, 90 % of corners within 0.3 % of the size, 99 % within about 1 % (only faces on old seams). Cost on a 300K-face source with 800K UV corners at 20K quads: about 6 seconds.
- Faces that cross an old seam are an approximation: for final texturing bake from the original or unwrap the result again (said in the option's tooltip).
- New test `tests/blender_uv_test.py`.

## 0.10.23

- Speed on dense sculpts: the engine measured the local thickness of the shape (the check that makes thin parts get smaller faces) at every vertex of your original mesh, with up to 14 searches each. On a 200K-vertex helmet that was 44 of the 51 seconds of a small remesh. It now measures a sample of about 40,000 vertices and spreads the values along the surface, which is the same result on a smooth field. Measured (2 cores): helmet to 8K faces 51 s to 8 s, head to 20K 28 s to 17 s, helmet to 60K 39 s to 30 s. Meshes under 40,000 vertices are unchanged. Irregular vertices are equal or lower on every model tested (helmet 51 to 34, head 44 to 35).
- The panel message when Detail on Curves is left out now says how much more irregular it was ("about 7x") instead of numbers that depended on the size of the run.
- Tried and not kept: deciding about Detail on Curves with a small test run before the real one. The test is reliable, but the time of a run depends on the size of the input mesh, not on the target, so it cost as much as it saved.

## 0.10.22

- Detail on Curves on mechanical shapes (found with the helmet): at 120K faces Low/Medium gave 1,700-2,100 irregular vertices against 376 with the option Off, because the finer faces on every rivet and edge break the grid into pole pairs. The engine now checks its own result: if it has many irregular vertices (more than 0.9 % of all vertices) it runs again without Detail on Curves and keeps that result when it has clearly fewer (under 60 %). The panel then says so. Faces and characters (head, fairy, skull) keep their detail; the check only costs time when it triggers.
- Measured on the helmet (120K, 2 cores): Off 376 poles, Low 1,727, Medium 2,122, High 908; aspect ratio of the worst quads is also much worse with High (603 quads above 3:1 against 223).
- Clearer help text: Detail on Curves is for faces and characters; leave it Off on hard-surface shapes.

## 0.10.21

- **Flow Guides (new):** in Edit Mode select edges on your model and press *Mark Flow Guide* (panel QuadForge > Flow Guides). The quad rows are pulled along those edges, for example around an eye or along a jaw line. It is a soft hint: the engine follows it where the surface allows it, never at the price of a broken result. With symmetry on, guides are mirrored. Stored on the mesh as an edge attribute (`quadforge_guide`), so it survives saving. *Clear Flow Guides* removes them; the checkbox in the panel header switches them off without deleting them. FORGE quads only.
- Engine: new optional guide section in the .fmesh exchange file (flag 4), guides become segments in 3D, so they do not depend on vertex numbers. `FORGE_GUIDE` (strength, default 8) and `FORGE_GUIDER` (reach in edge lengths, default 4) are test switches.
- Symmetry seam: small open loops that Blender's hole filler refuses (flat slivers on the plane) are now closed by hand (found with random guides on the fairy).
- New test `tests/blender_guides_test.py`: on a sphere with a diagonal guide, rows along the guide go from 57 % to 79 % (no symmetry) and from 42 % to 76 % (symmetry X), and the result stays closed.
- Engine experiments left off by default: `FORGE_POLEMOVES`, `FORGE_RING`, `FORGE_RINGR` (see docs/STATUS_AND_LIMITS.md).

## 0.10.20

- Edge flow on faces and characters: with Detail on Curves on, the quad rows now follow creases and grooves (brows, eyelids, lips, nose wings, ear rims). The engine measures how the surface bends across every edge, finds the direction along which it does not bend (the line of the crease) and pulls the cross field towards it where that direction is clear. Rows run along the lips instead of in a zig-zag, and the forehead and cheeks get cleaner flow. Round or flat areas are not affected.
- Off by default: only active when Detail on Curves is Low, Medium or High (Face 20K preset uses Medium). New engine switches for tests: FORGE_CALIGN (strength, default 4), FORGE_CGATE (bending angle that counts as a crease).
- Includes the 0.10.19 symmetry seam fix for open models.

## 0.10.19

- Symmetry on models with open borders (a neck, a cut, a mask): the whole model is now remeshed and cut afterwards, like closed models. Before, the half-first route could leave a few open edges and a non-manifold edge on the centre seam.
- Seam repair also runs on open models, limited to edges lying on the symmetry plane; seam edges shared by three faces keep the two biggest.

## 0.10.18
- Engine: the star clean-up now also replaces vertices with 6 spokes (before only 7 or more). These were the small "stars" visible on cheeks and temples. Fewer irregular vertices at 20K with the same face count: woman head 93 -> 79, fairy head 211 -> 178, hand 58 -> 49, skull 99 -> 80, bull ant 223 -> 216, plate unchanged. Switch: `FORGE_STARMIN` (default 6, use 7 for the old behaviour).
- Engine experiment kept off: a narrow-gap size field (`FORGE_GAP`) did not change the fairy chin, which is a shallow fold and not a gap.
- All batteries: add-on matrix 67/67, engine hard cases 25/25, robustness 114/115 (same known gear miss), add-on suites pass. Symmetry X at 20K with Detail on Curves = Medium: woman head 63 poles, fairy 183, skull 116, all closed with no non-manifold edges.

## 0.10.17
- New quick preset "Face 20K": 20K faces with Detail on Curves = Medium for heads and faces. Every preset now sets Detail on Curves explicitly (Off for all the others), so switching presets never leaves it on by accident.
- Symmetry seam repair: any n-gon left by filling a seam hole is triangulated, so the result only has quads and triangles.
- Why Detail on Curves stays Off by default (20K, no hard edges): hand 58 -> 58 poles, bull ant 223 -> 306, plate 72 -> 373. It pays off on faces and characters, not on mechanical parts.
- Robustness on the two sculpted heads with shuffled vertex and face order (4 orders each, Medium, 20K): always the grid method, 65-90 poles (woman) and 171-207 poles (fairy), 0-10 skewed quads out of 20K, face count within 4% of the target.

## 0.10.16
- "Detail on Curves" is much stronger and now also follows thin rims: Low / Medium / High allow a face edge to turn the surface by at most 0.35 / 0.22 / 0.15 rad and shrink the faces down to 30% of the normal size (before: 0.5 / 0.35 / 0.25 and 50%). On the sculpted woman head and the fairy head the eyelids, nostrils, lips and the ear rims now get visibly more faces. Cost: more irregular vertices (head at 20K: 64 -> 93 poles, fairy: 143 -> 211), so it stays Off by default; Medium is the recommended setting for faces and characters.
- Symmetry X checked with Medium and High on both heads: closed, no non-manifold edges, 0-4 triangles.

## 0.10.15
- New option "Detail on Curves" (Density panel; Off / Low / Medium / High, FORGE quads): smaller faces where the surface bends sharply, such as eyelids, lips, nostrils and ear rims, without painting anything. Off by default because it adds a few irregular vertices elsewhere (ant: 223 -> 298 at 20K). Engine flag `--curv-adapt <radians>`, env `FORGE_CURV`.
- Fix: symmetry on a closed model could leave one non-manifold edge or a slit at the seam when a thin flap folded onto the mirror plane (fairy head). Flaps are dropped, near-duplicate seam vertices merged and the leftover hole filled.
- Tested on two sculpted heads (woman 248K tris, fairy with long ears 298K tris) at 5K/20K/60K: 100% quads, grid method, 64 / 143 poles at 20K, symmetry X clean.

## 0.10.14
- Fix: symmetry on a closed model with real hard edges could leave one non-manifold edge on the seam (pinched quads with three vertices on the plane). They are now dropped before the mirror and the tiny hole is filled.
- Fix: a strong density vertex group (4x size ratio) made the engine give up the grid method and use pairing (3-10x more irregular vertices). The grid result is now kept and a note says the density was only partly followed.
- New add-on test matrix `tests/blender_matrix_test.py` (64 cases: 6 shapes x quads/tris/hard edges/rotated+scaled object/symmetry X, XY, XYZ/levels/zones/density group): 64/64. Engine suites: t07 114/115, t08 25/25, t07 quick 41/41; add-on suites 29/29 and forge tests all pass.

## 0.10.13
- Engine: when the grid method fails on a mesh with thin parts, the engine now runs again without the thickness-based refinement before using the pairing fallback. Fixes the 6 failures of the new robustness suite (thin plates, gears, a sphere with a cut).
- New `engine_tests/t07_robustness.py`: 13 SDF-built shapes (creatures, lumpy blobs, genus 1-2, hard-surface parts) x sizes + your models; checks closed/manifold, >=98% quads, count within 10%, grid method used. Result: 114/115 (99.1%); the miss is a 10-tooth gear asked for 600 faces (372 returned).

## 0.10.12
- Engine: the retry of the grid solve (another cell size when cut junctions do not close) is now off for every input. Measured on the plate with hard edges: identical result, 175 s -> 62 s on a 2-core test machine; on the hand and the ant it gave no gain either. `--retry` / `FORGE_RETRY=1` turn it back on for experiments.

## 0.10.11
- Symmetry: when only the symmetry planes are marked (no real hard edges) the engine skips its second grid solve (`--no-retry`). Bull ant with symmetry X: 166 s → 60 s on a 2-core test machine, same quality (258 poles, 0 open edges, target hit exactly).

## 0.10.10
- Symmetry (FORGE, quads, closed models): the whole model is remeshed with the symmetry planes marked as guide lines, then cut at the planes and mirrored. Removes the flat cap that disturbed the engine. Bull ant: poles 570 → 324, open seam edges 40 → 0, mirror exact. Also drops zero-area slivers the cut can create.
- New test: symmetry on one, two and three axes.

## 0.10.9
- Engine: the second grid solve (retry with a slightly different cell size) now runs only for hard-edge input. Measured on the hand: same or better quality without it, remesh time roughly halved. On the plate it still reduces poles, so it stays there.

## 0.10.8
- Engine: virtual subdivision of stretched triangles before lattice cell extraction; removes most holes and high-valence stars at finger tips (hand 118K: 280 → ~60 poles).

## 0.10.7 "Vivaldi" — Finger tips: no more many-spoke stars
- **Reported**: on the hand at 120 000 quads the finger tips showed star points with many lines meeting in one vertex.
- **Cause**: small holes the grid method could not close (thin, curved regions at the tips) were filled with a fan, which makes one vertex with 12 to 38 spokes (valence 8 in the best case).
- **Fix**: every vertex with 7 or more spokes is replaced by a regular quad patch with the same outline. The patch shape and its corners are chosen to keep the neighbouring valences close to 4 and the patch sides about square (not thin ladders); new points are relaxed on the surface. Hand at 120 000 quads: no vertex above valence 8 remains except one case, quads below the quality threshold 287 -> 169, bad corners 1078 -> 905. The replaced stars turn into several 5-valent vertices spread around the patch, which look much calmer than one star.
- The dead-end merge now repeats until nothing is left to merge (it left some pinched fans), and refuses merges that would give a quad with a repeated corner.
- Not solved yet: why the grid method leaves holes at the tips at this size (at 20 000 quads the tips are clean 3-valent points). Switch `FORGE_NOSTAR=1` turns the new step off for comparison.

## 0.10.7 "Vivaldi" — Plate with Hard Edges at 30 degrees
- **Reported**: the plate with Hard Edges (30 degrees) at 120 000 quads still came out with about 70 irregular vertices per 1000 (pairing method). Thanks to the fallback message added in 0.10.4 the reason was visible: "grid solve failed: extraction produced no faces".
- **Cause**: the isotropic remesh left two triangles of zero area on that model and size; the grid solve divided by their area and every number became NaN. Zero-area triangles are now skipped in the grid solve and its error measure (they carry no information). The same job now stays on the grid method (about 2 irregular vertices per 1000).
- Also: the "density fit" check could trigger needless retries when its measure was NaN; it no longer does.

## 0.10.7 "Vivaldi" — Meshes with flat sliver triangles and tiny holes
- **Cause of the reported pairing-path result** (hand about 70 irregular vertices per 1000): the hand file contains two isolated flat triangles (all three vertices on a line, 0.1 unit edges) that belong to no surface. They looked like tiny open holes, which sent the whole job to the pairing method. The engine now removes isolated flat slivers before anything else (`removed_slivers` in the statistics). The same file now stays on the grid method: 2.4 irregular vertices per 1000 at 120 000 quads with Hard Edges on (full add-on run, about 59 s on the 2-core test machine).
- Meshes whose only open borders are short loops (small holes) are now closed and remeshed on the grid method too (`closed_holes`), instead of silently using the pairing method. Long borders still use the capping path from 0.10.0.
- The fallback reason is reported for every pairing result, including open borders and disabled alignment.

## 0.10.7 "Vivaldi" — Fallback diagnostics
- **Reported**: with 0.10.3 the hand and the plate (quads, about 120 000, Hard Edges) came out with about 70 irregular vertices per 1000, which is the old pairing method; the same jobs give 2-2.5 per 1000 in the test machine. The cause on the reporting machine is not known yet.
- The engine now says why it left the grid method (`fallback` field in its statistics) and the panel shows it as a warning ("Reason: ..."). Please send that text when you see it.
- No change to the results when the grid method works.

## 0.10.7 "Vivaldi" — Version shown in the panel
- The panel title now reads `QuadForge 0.10.7`, so a screenshot always tells which build made the result. The number is read from `blender_manifest.toml` at load time (single source of truth, cannot drift). No engine change.

## 0.10.2 "Vivaldi" — Multi-core grid solver
- **The grid solve now uses all CPU cores** (it was single-threaded and is the longest stage on big models). Same results as before, bit for bit on the checked models; on the 2-core test machine the hand at 60 000 quads went from 30.5 s to 22.4 s, and a desktop CPU with more cores gains more. Models under about 40 000 unknowns still use the single-thread path.
- **Time limit** raised to 900 s (`--time-limit`): it is only a guard against runaway jobs now, because a fallback after minutes of waiting gave the worst of both worlds. Seen in the reported case: 0.9.0 spent 216 s on the plate at 120 000 quads and ended on the old pairing path (68 poles per 1000); 0.10.x finishes the same job on the good path (2.3 poles per 1000 in about 90 s on the 2-core machine, less with more cores).
- Checked: a model that is already a quad mesh of about the target size is remeshed on the good path (hand 118 829 -> 120 000 quads: 3 poles per 1000). If the target is not lower than the detail the add-on may return the input unchanged (the panel warns about it); that is the add-on's decision, not the engine's.

## 0.10.1 "Vivaldi" — Large models with hard edges no longer look frozen
- **Reported problem**: with Hard Edges on and a large quad count (plate), the progress bar sat at about 65% for minutes. The engine was not stuck; the grid solve produced no progress messages and took long.
- **Progress** is now reported through every phase of the grid solve (preparing, solving, singular points, seams, hard edges, finishing), so the bar keeps moving.
- **Faster**: the first size estimate was about 3% low and triggered a second full solve at almost every size; the estimate is now corrected and large jobs accept a 6% count error. Plate with marked edges at 100 000 quads: 78 s (about 145 s before). Typical sizes (4000–20 000) are unchanged or a little faster.
- **Time limit**: if one grid solve takes longer than 240 s (`--time-limit`, 0 = none) the engine falls back to the pairing path, which finishes in seconds, instead of working for minutes. Esc still cancels at any time.
- The dead-end vertex merge (two quads around a valence-2 vertex become one) is documented as on by default (`FORGE_NOMERGE2=1` disables it); it was active in 0.10.0 already.

## 0.10.0 "Vivaldi" — Open borders and quad quality
- **Meshes with open borders now use the integer-grid method** (wrists, cut models, half models for symmetry, clothes). The border loops are closed with temporary caps, aligned like hard edges, and the caps are removed after extraction. Checked on the hand with an open wrist, the ant cut in half and the plate with its top removed: all give a clean border loop, a few hundred quads per thousand away from poles are regular, and only a handful of bad quads. Loops shorter than 6 quad edges are closed.
- **Quad shape optimiser**: vertices next to badly shaped quads are moved to maximise the worst Jacobian. Inverted quads on the ant disappear; hand and plate have no quad below Jacobian 0.5.
- Border triangles can appear where very short edges had to be welded (open meshes only).
- Tests: border cases added to the engine tests (hemisphere stays one loop), fuzz test accepts border triangles on open meshes; Blender tests 28/28.
- Honest notes: pole counts of the ant move a few points up or down between sizes (34–43 per 1000 at 4000 quads) depending on tiny numerical differences; the geometric field weights are neutral on average. Experimental switches are documented in `docs/FORGE_ENGINE.md`.

## 0.9.0 "Vivaldi" — Adaptive size and density in the integer-grid method
- **Thin, curved parts get smaller quads** (thickness by the shrinking-ball method, only where the surface is curved like a tube). Measured on the bull ant: poles stay the same (4000 quads: 34.7 per 1000 vs 37.0 without; 20 000: 11.6 vs 10.9), leg tips and silhouettes get more quads. Honest: this is a modest gain, the ragged thin legs are not solved at 4000 quads. `--no-adaptive` turns it off.
- **Density groups now use the integer-grid method** (before, any density went to the pairing path). On the hand with a 3:1 density the quads grow smoothly from 1.8 to 4.4 units edge length with about 12 poles per 1000 quads and fluid loops. On shapes with few singularities (a sphere) a strong gradient cannot be followed; the engine detects that and uses the pairing path.
- **Bug fix**: when the integer-grid method failed and the engine fell back to pairing, it returned triangles instead of quads. Fixed; results with open edges are now rejected too.
- Cross field: edge weights follow the geometry, so uneven triangles no longer twist the field.
- Plate with marked edges at 20 000 quads: 3.8 poles per 1000 (4.7 in 0.8). Hand 20 000: 3.9 (3.6 in 0.8, within noise).
- Tests: density gradients on a sphere (valid quads in every case), engine tests t01–t06, Blender tests 28/28.

## 0.8.0 "Vivaldi" — Hard-edge alignment in the integer-grid method
- **Marked sharp edges now go through the integer-grid method** (before, any model with marked edges fell back to the pairing path). The cross field is aligned to the marked edges, each feature edge gets a constraint that keeps one lattice coordinate constant along it, those coordinates are rounded to integers, and cells are snapped to the feature lines and corners. Only chains of at least 6 edge lengths count as features; tiny marked edges are ignored. `--no-align` switches this off.
- **Measured**: hard-surface plate with marked edges, 4000 quads: 17.9 poles per 1000 (QuadriFlow 20, and it ignores the edges). Hand at 20 000: 2.7–3.6; ant 11–12; plate without alignment 4.7.
- **Holes**: the hole filler after extraction now closes holes up to 64 edges and no longer skips holes near borders, so large models (120 K) stay on the integer-grid path instead of dropping to pairing.
- **Box corners** no longer drift along edges during the subdivision stage.
- **Experimental**: a smoothed-aggregation multigrid preconditioner (`amg.hpp`) is included but OFF by default because it was slower than the plain Jacobi-CG solver (hand 20 K: 5.7 s vs 3.3 s). Enable with env `FORGE_AMG=1`.
- Honest limits (unchanged): thin parts below the quad size (ant legs) are ragged; some fans appear at the base of bosses; open borders and density groups still use pairing; 120 K quads takes 40–150 s on 2 cores depending on the model.

## 0.7.0 "Vivaldi" — Integer-grid parametrization: QuadriFlow-grade poles at FORGE speed
- **New quad method inside FORGE**: a seamless integer-grid parametrization replaces the free-floating lattice of 0.6. The cross field is matched between faces, singular vertices are found, a cut graph is built (tree/cotree), a least-squares system makes the (u, v) gradients follow the field with exact seam transitions (rotations of 90° plus translations), and singular vertices and seam translations are rounded to integers step by step. Quads come from the integer iso-lines. Source: `param.hpp`.
- **Measured** (poles per 1000 quads, lower is better; QuadriFlow in brackets):

  | Model | 4000 quads | 20 000 quads | 80 000 quads |
  |---|---|---|---|
  | Hand | 11–14 (17) | 2.7 | 2.8 (~1) in 35 s |
  | Bull ant | 40 (54) | 12 | 6.7 (~1) in 67 s |
  | Hard-surface plate, no marked edges | 15 (20) | 3.3 | — |

  0.6.0 had hand 40 / 22 / 11, ant 109 / – / 38. FORGE now beats QuadriFlow at 4000 quads and reaches 2–3 poles per 1000 at 20 000–80 000 quads (QuadriFlow needed 100 s for the hand at 117 K). Times are for 2 CPU cores.
- Works on closed surfaces of any genus and on several components (checked: spheres, torus, two spheres, your hand, ant and plate). Open borders, density groups and models with many marked hard edges still use the pairing path (hard edges are kept exactly there); aligning the grid to hard edges is the next step (0.8).
- Lattice density default is now 6 triangles per cell (was 8): about 2x faster for a small pole increase. `--ex-density` changes it, `--no-param` returns to the 0.6 position-field method.
- Warm starts between the size-correction passes; seam penalty continuation (soft first, stiff for the final solves).
- Checked beyond `t06_extract.py` (which has a sphere, a box, a hemisphere and two spheres): the 120-case sweep with torus (genus 1), bumpy blob, cylinder and box; all closed, manifold, correct Euler characteristic.
- Honest limits: thin parts below the quad size (ant legs) are still ragged; the hard-surface plate with its marked edges is not yet better; the solver is a plain conjugate gradient, so very large counts take tens of seconds.

## 0.6.0 "Vivaldi" — Global quad lattice: far fewer poles, still fast
- **New quad method in FORGE** (automatic): a global position-field lattice replaces triangle pairing on closed surfaces. Measured at 4000 quads: hand 94 → 40 poles per 1000 quads (96 % regular vertices), 22 at 20 000 quads and 17 at 40 000 quads (6.4 s from 119 K triangles). QuadriFlow: 17 at 4000 quads in ~5 s.
- Honest limits: the bull ant improves little (103 → 109 is within noise; thin legs are below the quad size) and models with open borders, many marked hard edges (the hard-surface plate) or a density group keep using the 0.5.1 pairing path, because a fixed-spacing grid cannot hold closed border/crease loops exactly.
- The face count now lands within a few percent of the target (the lattice spacing is corrected up to twice).
- The cross field is solved on a coarser copy and transferred, which cuts big-mesh times (40 K quads from 13 s to 6 s).
- Small holes in the cell graph are filled; long chains of marked edges are snapped onto at the end.
- Engine statistics report which method ran (`method`). New engine test `t06_extract.py`; the engine tests create their output folder.
- New experimental `quadopt.hpp` (pole optimisation by chord rotations), off by default.

## 0.5.1 "Vivaldi" — Validated inside Blender, cancel with Esc, packages per platform
- **Validated in Blender 5.2.2** (through the `bpy` module): all 28 original scenarios and 9 new FORGE scenarios pass, including your three real models (hand, bull ant with peg, hard-surface plate), the operator, density groups, symmetry, levels and the installation of the real extension package.
- **Cancel with Esc:** the Remesh button now runs a modal operator with a live progress bar. Esc kills the engine process and deletes every temporary datablock; the original is never touched. Scripts can still call the blocking version.
- **Pole-aware pairing:** after pairing triangles, a simulated-annealing search over alternating cycles reduces irregular vertices (about 10 % fewer poles, ~0.15 s). Honest note: the real limit is the triangle base, so quad quality is still below QuadriFlow (see `docs/STATUS_AND_LIMITS.md` for the measured numbers).
- **Fixed:** a stale `None` entry in the selection list after a scene reset could break the QuadriFlow route and the final selection.
- **Packaging:** `devtools/package.py` builds an all-platforms zip and one zip per platform (`platforms` in the manifest), keeping the executable bit. Blender's installer drops the executable bit; the add-on restores it on first use (verified).
- **macOS:** engine binaries for Apple silicon (ad-hoc code signature present) and Intel are included; the add-on clears the quarantine flag when it can. Proper signing and notarization are postponed (see `docs/PUBLISHING.md`).
- **Developer tools:** `devtools/` (engine tests, comparison against QuadriFlow, STL reader, packager) and `tests/blender_forge_test.py`.

## 0.5.0 "Vivaldi" — Own FORGE engine: quads or triangles (not validated in Blender)
- **New FORGE engine** (dependency-free C++17, separate program in `bin/<system>/`): uses neither QuadriFlow nor anything from Blender. It runs as a separate process, so a failure in it cannot close Blender.
  - Input mesh repair (duplicate faces, flipped normals, pinched vertices, non-manifold).
  - Isotropic remeshing (Botsch–Kobbelt) reprojected onto the original sculpt: no working copy needed.
  - Hard edges and open edges are kept as exact chains; corners are preserved.
  - **Selectable output: Quads or Triangles.** Quads: 4-RoSy cross field + triangle pairing + subdivision + field-aligned relaxation.
  - **Paintable density:** a vertex group requests smaller faces where the weight is high (*Density* panel).
- QuadriFlow is still available as an alternative engine (*Engine* selector).
- With FORGE no working copy, corner snapping or polish is used (the engine already projects); each extra level comes directly from the source.
- Results `<name>_quads` or `<name>_tris`; the result panel adapts its metrics to triangles.
- Texts and Spanish translation updated ("Target Faces", "Remesh").
- Engine source code included in `engine_src/` (GPL). New tests: `tests/test_forge_backend.py` (without Blender, with the real engine).
- Known limitations: quads still have scattered poles (~91–93 % regular vertices), not Quad Remesher quality; cannot be cancelled midway (blocking operator); macOS binaries compiled but not run.

## No new version — research (documentation and tools, no addon changes)
- `docs/PRESETS_AND_WORKFLOW_STUDY.md`: study of profiles by model type, Automatic mode, two-step workflow and reset to a safe baseline; calibration with three real models (cube, ant, cat).
- `tests/research_model_analysis.py`: research script to measure STL models (flatness, edges, roughness, symmetry and health).

## 0.4.0 — Fixes from the first real test and better hard-edge detection (not validated in Blender)
Based on the analysis of the bull ant (see `docs/CASE_BULL_ANT.md`).
- **Fixed:** the mirrored half could end up with inverted faces (a marked line in the center with smooth shading).
- **Fixed:** gaps next to the symmetry seam (now welded with a tolerance relative to the quad size).
- **Fixed:** fitting to the sculpt could collapse neighboring vertices into a single point.
- **New:** smart hard-edge detection by chains, with a quad-size filter and preservation of straight corners.
- **New:** the result's corners snap exactly onto the model's hard-edge lines.
- **New:** automatic shading (smooth with hard edges marked), with Smooth and Flat options.
- **New:** presets **Detail 80K** and **Detail 120K**; the automatic working copy supports up to 600 K faces.
- **New:** the target warning compares against the quad equivalent (a triangle counts as half a quad).
- **New:** inconsistent-orientation face metric in the quality report.
- New module `core/features.py`; new tests (`test_features.py` and 7 integration scenarios).

## 0.3.0 — Heavy sculpts, levels and detail (not validated in Blender)
- **Working copy:** heavy sculpts are reduced to ~4× the largest target (40 K–600 K faces) before the engine; less CPU and RAM.
- **Fit to the original sculpt:** each level is refitted to the original surface (BVH tree), preserving shape detail.
- **Polish:** quad relaxation with hard edges and open edges fixed, followed by refitting.
- **Extra levels (LOD):** `15K, 10K, 5K, 4K` in a single run; each level starts from the previous one. Objects `<name>_quads_20K`, ...
- **Robust zones:** materials, Face Sets and mask are computed on the full sculpt and transferred through decimation and the levels.
- **Built-in profiling:** time per stage and Blender's peak memory; shown in "Last Result".
- **Interface:** new "Extra Levels" and "Speed and Detail" panels, "Ladder 20K to 4K" preset, per-level result.
- **Pipeline** reorganized into named stages; new modules `planning.py`, `core/detail.py`, `core/profiling.py`.
- Complete **documentation** in `docs/` and `tests/benchmark.py` script.

## 0.2.0 — Guided interface
- Live tips, presets, help in every section, step-by-step guide, Spanish translation, quality report.

## 0.1.0 — First prototype
- Quad target, hard edges by angle, isolation by materials/Face Sets/mask, exact symmetry, QuadriFlow engine.
