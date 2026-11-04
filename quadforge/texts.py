"""Every sentence the user reads in the panel (English).

Keeping the texts here, instead of scattered through the UI code, has two benefits:
  * you can improve the wording without touching any logic;
  * ``translations.py`` uses these same constants as keys, so a translation can
    never silently drift away from the text it translates.

Texts with ``{placeholders}`` are formatted by the code that shows them.
"""

# ---------------------------------------------------------------- live guidance (top of the panel)
GUIDE_SELECT_MESH = "Step 1: click the mesh you want to remesh."
GUIDE_NOT_A_MESH = "'{name}' is a {kind} object. Select a mesh object instead."
GUIDE_NO_FACES = "'{name}' has no faces to remesh."
GUIDE_READY = "Ready: '{name}' has {faces} faces and will become about {target} {kind}."
GUIDE_TARGET_NOT_LOWER = (
    "The target ({target}) is not lower than the detail this mesh already has (about {faces} quads), "
    "so the result will not be simpler.")
GUIDE_HEAVY_MESH = (
    "This mesh is very dense ({faces} faces) and the working copy is turned off. Remeshing may "
    "take several minutes and use a lot of memory. Turn on 'Use Working Copy' or decimate it first.")
GUIDE_USES_PROXY = (
    "This sculpt is heavy ({faces} faces). QuadForge will first make a lighter working copy of about "
    "{proxy} faces (faster, much less memory), then snap the result back onto your original sculpt "
    "to keep the detail.")
GUIDE_PROXY_SMALL = (
    "The working copy ({proxy} faces) is small for a target of {target} quads. "
    "Use at least 3 to 4 times the target, or turn on 'Auto Size', to keep the detail.")
GUIDE_LEVELS_IGNORED = (
    "These extra levels were ignored (not a number, not lower than the previous level, or too small): {items}.")
GUIDE_MODIFIERS = "{count} modifier(s) will be included in the result. Your original object is not changed."
GUIDE_LEAVES_MODE = "QuadForge will switch to Object Mode before remeshing."
GUIDE_OFF_CENTER = (
    "The model is not centred on the {axis} axis (the object's origin is off-centre). "
    "Set 'Plane At' to Bounding Box Centre, or move the origin to the middle of the model.")
GUIDE_NO_MATERIAL_ZONES = "'By Material' is on, but this mesh has only one material slot: nothing to separate."
GUIDE_NO_FACE_SETS = "'By Face Set' is on, but this mesh has no sculpt Face Sets."
GUIDE_NO_MASK = "'By Sculpt Mask' is on, but this mesh has no sculpt mask."
GUIDE_NO_VERTEX_GROUPS = "'By Vertex Group' is on, but this mesh has no vertex groups."

# ---------------------------------------------------------------- main panel
LABEL_QUICK_PRESETS = "Quick presets (set target and angle in one click)"
LABEL_SURFACE = "Surface type (sets the options for you)"
LABEL_HEAVY_TARGET = "Large target: slow and heavy on memory."
LABEL_EXACT_NUMBER = "Exact number"
LABEL_ACTIVE_OPTIONS = "Active options: {options}"
LABEL_NO_OPTIONS = "none (only the target count)"
LABEL_OPTION_HARD_EDGES = "hard edges above {angle} degrees"
LABEL_OPTION_ZONES = "zones: {zones}"
LABEL_OPTION_SYMMETRY = "symmetry {axes}"
LABEL_OPTION_LEVELS = "levels: {levels}"
HINT_OPTIONAL_PANELS = "Optional refinements are in the panels below."

# ---------------------------------------------------------------- section help
HELP_HARD_EDGES = (
    "Hard edges are the sharp corners of your model. QuadForge keeps them as clean edge "
    "loops instead of rounding them off. Lower the angle to keep more edges, raise it to keep fewer.")
HELP_ZONES = (
    "Zones are areas you want to keep apart, so the quad flow does not cross from one to "
    "the other. Turn on one or more options.")
HELP_SMART_FEATURES = (
    "Smart detection looks at whole chains of sharp edges instead of single edges. It ignores tiny bumps "
    "that the quads could never follow, keeps right-angle corners, and snaps the corners of the result "
    "onto the real edge lines of your model so they stay crisp.")
HELP_SYMMETRY = (
    "Use symmetry when your model is mirrored. One half is kept and mirrored, "
    "so both sides are exactly identical. Axes are the object's local axes.")
HELP_SYMMETRY_PLANE = (
    "The mirror plane passes through the object's origin. If the origin is not in the "
    "middle of the model, choose 'Bounding Box Centre'.")
HELP_SYMMETRY_SIDE = "The half you do not keep is discarded, so keep your best side."
HELP_SPEED = (
    "Heavy sculpts are remeshed through a lighter working copy (for example 3 million faces down to "
    "80,000), which is much faster and uses far less memory. The result is then snapped back onto your "
    "original sculpt so the detail is kept. Turn it off only for light meshes.")
HELP_POLISH = (
    "Each pass evens out the size of the quads and snaps them back onto your sculpt. Corners and open "
    "borders are never moved. 2 to 4 passes give a pleasant, regular flow; 0 only snaps.")
HELP_LEVELS = (
    "Build several versions in one go, from the target above down to lower counts. Write the extra "
    "levels separated by commas, for example 15K, 10K, 5K, 4K. Each level is made from the previous "
    "one and snapped to your original sculpt.")
LABEL_LEVELS_PREVIEW = "Levels to build: {levels}"
HELP_ENGINE_FORGE = (
    "FORGE is QuadForge's own engine. It reads the sculpt directly (no working copy needed), keeps hard "
    "edges and open borders, and can output quads or triangles. It runs in a separate process, so it "
    "cannot crash Blender.")
HELP_CURVATURE = (
    "Detail on Curves puts smaller faces where the surface bends sharply, like eyelids, lips, nostrils "
    "and ear rims, and makes the face rows follow those lines, without painting anything. Try Medium on "
    "faces and characters. It can add irregular vertices elsewhere, so leave it Off for plain or mechanical shapes."
)

HELP_GUIDES = (
    "Flow Guides tell the engine where you want the rows of quads to run, for example around an eye or "
    "along a jaw line. In Edit Mode select edges on your model and press Mark Flow Guide. The guides are a "
    "soft hint: the engine follows them where the surface allows it. With symmetry on, they are mirrored."
)
LABEL_GUIDE_COUNT = "{count} guide edges on this object."
LABEL_GUIDE_NONE = "No guide edges yet. Select edges in Edit Mode and press Mark Flow Guide."

HELP_DENSITY = (
    "Paint a vertex group in Weight Paint mode: the faces get smaller where the weight is high and stay "
    "normal where it is 0. Pick the group here.")
LABEL_NO_GROUPS = "This object has no vertex groups yet."

# ---------------------------------------------------------------- last result panel
LABEL_RESULT_FACES = "{faces} faces"
LABEL_RESULT_QUADS = "{quads} quads ({percent}%)"
LABEL_RESULT_TRIS = "{tris} triangles, {ngons} n-gons"
LABEL_RESULT_POLES = "{poles} poles (vertices that are not 4-way)"
LABEL_RESULT_TRI_ONLY = "Triangle mesh: {tris} triangles, evenly sized"
LABEL_RESULT_HARD_EDGES = "{count} hard edges kept"
LABEL_RESULT_TIME = "Time: {seconds} s"
LABEL_RESULT_CORNERS = "{count} corner vertices snapped onto edge lines"
LABEL_RESULT_MEMORY = "Peak memory of Blender: {memory} GB"
LABEL_RESULT_LEVELS = "All levels"
LABEL_RESULT_LEVEL_LINE = "{name}: {quads} quads ({percent}%), {poles} poles, {seconds} s"
HELP_RESULT = "Good topology: almost all quads and few poles."

# ---------------------------------------------------------------- how-to panel
GUIDE_TITLE_STEPS = "Step by step"
GUIDE_STEP_1 = "1. Select the mesh you sculpted. It can be triangles or quads, at any density."
GUIDE_STEP_2 = "2. Press Hard Surface or Soft Surface / Organic, pick the Target Faces from the list (or type an exact number) and choose Quads or Triangles in 'Output'."
GUIDE_STEP_3 = "3. Optional: open 'Hard Edges' to keep sharp corners. A lower angle keeps more edges."
GUIDE_STEP_4 = "4. Optional: open 'Isolate Zones' to keep materials, Face Sets or a sculpt mask separated."
GUIDE_STEP_5 = "5. Optional: open 'Symmetry' and enable the axes where your model is mirrored. X is the most common."
GUIDE_STEP_6 = (
    "6. Optional: 'Extra Levels' builds lower versions in one go (for example 15K, 10K, 5K, 4K). "
    "'Speed and Detail' controls the working copy and the polish passes.")
GUIDE_STEP_7 = (
    "7. Press 'Remesh'. Your original stays (hidden) and a new object named "
    "'<name>_quads' or '<name>_tris' appears (one per level).")
GUIDE_STEP_8 = (
    "8. Read 'Last Result': a high quad percentage and few poles mean clean topology. "
    "Warnings tell you what to adjust.")
GUIDE_TITLE_TIPS = "If the result is not what you expected"
GUIDE_TIP_1 = "Too many or too few edge loops on corners: change the Hard Edges angle."
GUIDE_TIP_2 = "Quad count far from the target: hard edges and zones add constraints. Raise the angle or turn zones off."
GUIDE_TIP_3 = "A visible seam in the middle of a symmetric model: check 'Plane At' matches where the model is centred."
GUIDE_TIP_4 = "Remesh fails: close holes, delete loose parts and non-manifold geometry, then try again."
GUIDE_TIP_5 = (
    "Fine sculpt detail looks lost: a quad mesh at a low count cannot hold it. Keep 'Keep Sculpt Detail' on, "
    "then bring the detail back with a Multires reshape or a normal map bake.")

# ---------------------------------------------------------------- confirmation for very dense meshes
CONFIRM_TITLE = "Very dense mesh"
CONFIRM_MESSAGE = "This may take several minutes and a lot of memory. Continue?"
CONFIRM_BUTTON = "Remesh anyway"
