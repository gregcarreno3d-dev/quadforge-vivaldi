# Study: per-model-type profiles, Automatic mode, two-step workflow and safe reset

Status: **design study, nothing implemented**. Based on QuadForge 0.4.0 (which has not yet been validated in Blender).
Convention: **[verified]** checked with code or measurement · **[assumed]** reasonable but unchecked · **[to measure]** requires data.

## 1. What was requested

1. A **model type** section with *Hard Surface*, *Organic* and *Automatic* (or *Optimized*) profiles that configure the addon:
   - Hard Surface: hold right angles and flat faces, and keep the parts.
   - Organic: smooth muscle-like surfaces, prioritizing detail.
   - Automatic: examine the object and adjust the properties according to what it finds.
2. Below it, the **polygon/quad target** menu, to split the configuration into **two steps**.
3. A **reset button** for all properties to a **safe baseline**, with a single click.

## 2. Starting point (v0.4) [verified in the code]

- The current presets (Prop 2K, Character 8K, Hard Surface 6K, Hero 20K, Detail 80K/120K, ladder) **mix two different ideas**:
  the model *type* and the *size*. They also change only the target and the hard-edge angle (and the extra levels in the ladder).
- There are more than 30 properties spread across 7 panels; a preset does not touch them (corner smoothing, minimum detail size, polish...).
- A **Reset Settings** button already exists, but it is hidden in the Advanced panel and restores the default values from `properties.py`.
- There is no analysis of the object: the addon does not know what kind of model it receives.

## 3. Proposed two-step workflow

```
┌ 1 · MODEL TYPE ─────────────────────────────────────────────┐
│  ( Hard Surface )  ( Organic )  ( Automatic )   [Analyze]    │
│  One sentence: what this profile prioritizes.                │
│  Summary: "Detected: 62 % flat surfaces, right angles"       │
│  ▸ Fine tuning (collapsed): hard edges, zones, symmetry,     │
│    speed and detail, shading                                 │
├ 2 · SIZE ───────────────────────────────────────────────────┤
│  Target quads: [ 20000 ]   2K · 8K · 20K · 80K · 120K        │
│  Extra levels (optional)                                     │
│  [ Remesh to Quads ]                                         │
├──────────────────────────────────────────────────────────────┤
│  [ ⟲ Back to the safe baseline ]   (always visible, with undo)│
└──────────────────────────────────────────────────────────────┘
```

Underlying idea: the **type** decides *how* the remesh behaves (what gets protected) and the **size** decides *how much* (how many quads).
That is why today's presets are split into **type profiles** (Hard Surface, Organic, Mixed, Automatic) and **size presets**
(2K, 8K, 20K, 80K, 120K, 20K→4K ladder). Any type combines with any size.

## 4. What each profile changes

**Provisional** values, expressed with the properties that already exist. They must be calibrated with real models (section 9).

| Property | Hard Surface | Organic | Mixed (≈ current values) | Reason |
|---|---|---|---|---|
| Hard-edge angle | 25° | 55° | 35° | Hard: every corner counts. Organic: only pronounced creases |
| Minimum detail size (quads) | 1.0 | 3.0 | 2.0 | Hard: keep small parts. Organic: ignore noise |
| Keep right angles | yes | no | yes | Pegs, boxes, edges |
| Snap corners to lines | yes | no | yes | Crisp corners versus smooth shapes |
| Polish passes | 1 | 4 | 3 | Hard: do not smooth. Organic: regular quads |
| Shading | smooth with hard edges | smooth | smooth with hard edges | |
| Working copy size | 4× the target | 6× (new control) | 4× | Organic: more fidelity to the sculpt |
| UV seams as hard edges | yes | no | yes | |

**What a profile CANNOT achieve with the current engine** [verified in the design]:
- **Flat faces and separate parts** (Hard Surface): does not exist today. A new capability is needed (C1).
- **Edge flow following muscles** and **quad density adapted to curvature** (Organic): QuadriFlow does not offer that control.
  With the current engine one can only *get closer*: more fidelity (larger working copy), fewer constraints and more polish.

## 5. New capabilities that would be needed

| Id | Capability | Design idea | Risk |
|---|---|---|---|
| C1 | **Flat patches** (Hard Surface) | Group faces joined by nearly coplanar edges (< 2°); a group is flat if all its normals stay within 4° of the mean and it covers more than 1 % of the area. Each patch is treated as a **zone** (the addon already knows how to isolate zones and transfer them between levels) and, after the remesh, the interior vertices are projected onto the fitted plane | High: it is new research |
| C2 | Exact cylinders and square corners | Extension of C1 (cylinder fitting). Later | High |
| C3 | Organic detail fidelity | Control of the working-copy factor (today it is a constant), automatic detail transfer (Multires *reshape*) and, in the long term, an engine with adaptive quad size | Medium–high |
| C4 | **Model analysis** | Pure NumPy module (`analysis.py`) that returns a summary: % flat area, coherent hard edges, right angles, roughness, candidate symmetry, shells, whether it is closed | Medium |
| C5 | **Profile engine** | Pure module (`profiles.py`): each profile is a set of values; functions to apply, blend and recognize which profile matches the current settings | Low |

## 6. Automatic mode: what the data say

Two separate experiments were run (own NumPy code, the addon was not executed) with synthetic reference meshes and the real bull
ant (37,630 triangles). The numbers are **[verified]** for those meshes; conclusions about other models are **[assumptions]**.

### 6.1 Results

| Model | Flat area | Median angle | Hard edges (>35°) | Coherent part* | Right angles in coherent chains |
|---|---|---|---|---|---|
| Box (Hard) | 100 % | 0.0° | 4.8 % | 100 % | 100 % |
| 3×1×1 box (Hard) | 100 % | 0.0° | 4.8 % | 100 % | 100 % |
| Capped cylinder (Hard) | 100 % | 0.0° | 5.1 % | 100 % | 100 % |
| Smooth ellipsoid (Organic) | 0 % | 2.4° | 0 % | – | – |
| Lumpy blob (Organic) | 0 % | 2.6° | 3.4 % | 100 % | 51 %** |
| Rough sculpt (Organic with noise) | 0 % | 14.0° | 14.5 % | 79 % | 12 % |
| Box + ellipsoid (Mixed) | 64 % | 0.6° | 1.1 % | 100 % | 100 % |
| **Bull ant (real)** | **0 %** | **9.0°** | **10.0 %** | **75 %** | **17 %** |

\* Coherent part = proportion of the hard-edge length that lies in long chains (≥ 8 target-quad lengths).
\*\* Probably an artifact of the poles of the synthetic sphere; not verified.

### 6.2 What is learned

1. **Flat area separates** purely hard (100 %) from organic (0 %) very well, and detects mixed (64 %).
2. **"Hard-edge density" is misleading** [verified]: the rough sculpt (30 per unit of size) and the ant (16) look "harder"
   than the box (4.9), because noise also generates edges. It is not useful as a measure of hardness.
3. **Coherence alone does not separate noise from design**: the rough sculpt (79 %) and the ant (75 %) come out almost the same.
4. **The proportion of right angles** clearly distinguishes mechanical (100 %) from rough (12 %) and from the ant (17 %).
5. **The ant is ambiguous**: 0 % flat surfaces, but with real right angles (you noticed them). Its global statistics resemble
   those of a rough sculpt. **A global classifier cannot tell whether those facets are design or noise.**

### 6.3 Consequences for the design of Automatic

- **It must not feign certainty.** It must show *what it found* and *with how much confidence*, and let the user change it with one click.
- **Three possible outcomes**: Hard Surface, Organic or **Mixed**. When the data are ambiguous (like the ant) it must choose **Mixed**: organic base with the hard corners
  protected. This is essentially the current configuration.
- **It must work locally, not globally.** What protects the shape are the specific *flat patches* and *right-angle chains*; the rest is left
  free. That way a model with hard parts and organic parts is handled well regardless of its "type".
- **Continuous blending** instead of a switch: a hardness score `t` between 0 and 1 interpolates the numeric values between Organic and Hard Surface;
  checkboxes are enabled by threshold.
- **Symmetry**: the addon already knows how to measure it. It should **suggest** it ("symmetry detected in X: enable?") and enable it on its own only with a very high match (proposal: ≥ 98 %),
  because enabling it discards one half of the model.

### 6.4 Initial decision rule (provisional; section 13 updates it with real models)

```
if flat_area ≥ 50 %                                        → Hard Surface
if hard_edges < 2 % and flat_area < 5 %                    → Organic
if coherent_right_angles ≥ 60 % and coherent ≥ 80 %        → Hard Surface
in any other case                                          → Mixed  (low confidence)
t (hardness) = weighted combination of flat_area and coherent_right_angles
```
These thresholds come from 8 meshes (7 synthetic and 1 real); section 13 revisits them with three real models. **Real models of each class are needed** to tune them.

### 6.5 Where and when to analyze

| Option | Advantage | Drawback |
|---|---|---|
| A. Inside the remesh, right after creating the working copy | No duplicated work; sees the mesh the engine will use | The user does not see the result before remeshing |
| B. **Analyze** button that writes the settings and shows the summary | It is what you asked for: examine first, change properties afterwards, and be able to tweak | With multi-million-face sculpts it repeats the decimation (seconds to minutes) |
| C. B with a sampled mesh or one limited to ~100K faces | Fast | May lose fine detail in the analysis [to measure] |

**Recommendation:** start with **A** (Automatic is applied when Remesh is pressed and the report says what it detected and what it used) and add **B/C** later, once the real cost
has been measured. The analysis of a 1-million-quad mesh with NumPy took ~1.5 s in a previous measurement [verified]; the connected-components part
(edges and patches) is **[to measure]**.

## 7. "Optimized": a design question

"Automatic or Optimized" admits two readings: (a) the same mode (the addon optimizes itself by analyzing the model) or (b) a distinct profile oriented to **speed**.
**Recommendation:** (a), named "Automatic (optimized)", and keep speed as a separate control (*Speed and Detail*), because
"what to protect" and "how long to take" are independent axes. Pending your confirmation.

## 8. How profiles work internally

**Write to the properties (recommended) or a separate layer.**

| Option | How it works | Pros | Cons |
|---|---|---|---|
| **Write** | Choosing a profile copies its values into the existing properties | Transparent; fine tuning remains the same as always; little code | Switching profiles overwrites your manual changes |
| Layer | Effective values = profile + user overrides | Nothing is lost | More complex interface; the user does not see the real value |

**Recommendation: write**, with a **"Custom"** indicator: the panel compares the current settings with those of the chosen profile and, if they differ, shows
"Hard Surface (modified)". That comparison is done when the panel is drawn (cheap) and **needs no update callbacks**, which are fragile.
Each profile lives as data in a pure module, so it can be tested without Blender.

## 9. Safe baseline button

**What the "safe baseline" is.** A named profile (**Balanced**) with fixed, documented values: the addon's most proven starting point. Today it would be
Mixed with a target of 4,000, no symmetry, no zones, no extra levels, with an automatic working copy and moderate polish.

**What it resets.** Everything the user can edit: type, size, levels, hard edges, zones, symmetry, speed and advanced. It does **not** touch the result of the
last remesh (*Last Result* panel) or the selected object.

**Proposed behavior:**
- A single click, always visible (not hidden in Advanced), **no confirmation dialog** but with **undo** (Ctrl+Z recovers everything). A dialog gets in the way more than it helps when the button exists precisely to get out of trouble.
- **A single source of truth**: today the default values are repeated in `properties.py`. The proposal is to define them in one place and add a test that
  fails if they change without notice.
- Later (not now): save **custom settings** with Blender's preset system, to return to a personal state and not only the factory one.

## 10. Phased plan

| Phase | Content | Risk | Tests |
|---|---|---|---|
| F0 | **Validate v0.4 in Blender** before calibrating profiles | – | Integration test and benchmark |
| F1 | Two-step interface + always-visible reset button (without changing the logic) | Low | Integration |
| F2 | `profiles.py`: profiles as data, separation of type and size presets, "Custom" indicator, safe baseline as a single source | Low–medium | Pure unit tests |
| F3 | Calibrate Hard Surface, Organic and Mixed with the user's real models | Medium | Extended benchmark |
| F4 | `analysis.py` + Automatic mode inside the remesh + summary and confidence + symmetry suggestion | Medium | Unit tests with synthetic and real meshes |
| F5 | **Analyze** button (B/C of section 6.5) | Medium | Measure cost on heavy sculpts |
| F6 | Flat patches (C1): flat zones and projection onto planes | High | Real mechanical meshes |
| F7 | Organic detail (C3): working-copy factor and detail transfer | Medium–high | Compare against the sculpt |

**Key dependency:** the profiles tune parameters whose behavior has not yet been confirmed in Blender (mirror correction, chain detection,
corner snapping). Calibrating values on an unvalidated base would produce meaningless numbers. That is why **F0 comes first**.

## 11. Risks and open questions

- **Wrong classification.** Mitigation: Automatic always shows what it detected, with confidence, and can be overridden with one click.
- **Models with hard and organic parts** (like the ant): a global profile is insufficient; local treatment (flat patches and chains) is the long-term solution. A possible intermediate step: a profile **per zone** (by material or Face Set).
- **Switching profiles overwrites settings.** Mitigation: "modified" indicator and reset with undo.
- **Calibration data.** Real examples of each class are missing.
- **Compatibility.** Scenes already saved with v0.4 will have new properties at their default value (no loss).
- **Translation.** Every new text is added in `texts.py` and `translations.py`.

**To move forward we need from you:**
1. Your confirmation of the reading of "Optimized" (section 7).
2. More example models. There are already three real ones (cube, ant and cat, section 13); with **5 to 10** we avoid tuning the thresholds only to those three.
3. The result of the v0.4 test (F0), with the ant and with the cat (see the model health warning in section 13.3).

## 12. Method and limits of the experiments

- Synthetic meshes: box, elongated box, capped cylinder, ellipsoid, lumpy ellipsoid, rough ellipsoid and box with ellipsoid. Three real meshes (cube, ant and cat; see section 13).
- The synthetic meshes are simple (the poles of the spheres generate artifacts) and the thresholds were tested with only one real mesh: **the values are indicative**.
- The numbers come from standalone NumPy scripts; neither the addon nor anything from Blender was run.

## 13. Calibration with three real models (cube, ant, cat)

The three real models were measured with the script `tests/research_model_analysis.py` (NumPy only; it uses neither the addon nor Blender).
All numbers in this section are **[verified]** for those three models; what is inferred for other models is **[assumed]**.

### 13.1 Results

| Datum | Hard surface cube | Bull ant (organic) | Robot cat (smooth + hard parts) |
|---|---|---|---|
| Triangles (quad equivalent) | 544 (272) | 37,630 (18,815) | 657,494 (328,747) |
| Mean edge, % of the diagonal | 9.0 % | 0.63 % | 0.20 % |
| Shells / non-manifold edges | 1 / 0 | 1 / 0 | **3 / 11** |
| Flat area (2° / 4° / patches ≥ 1 %) | **63.4 %** (23 patches) | 0 % | 20.1 % (6 patches) |
| Edges with angle > 35° | 38.2 % | 10.0 % | 0.7 % |
| Long edge chains / total | 15 / 16 | 86 / 477 | 3 / 1,281 |
| Edge length in long chains | 100 % | 75 % | 74 % |
| 90° edges (and % within long chains) | 24 (8 %) | 790 (17 %) | 869 (27 %) |
| **Roughness** (area whose sharpest edge measures 10°–35°) | 4.3 % | **49.0 %** | 2.4 % |
| Local classes (flat / next to hard edge / smooth / other) | 63 / 34 / 0 / 3 % | 0 / 13 / 38 / 49 % | 20 / 8 / 70 / 2 % |
| Symmetry about the box center (X / Y / Z) | 53 / 0 / 1 % | **96** / 12 / 0 % | **100** / 11 / 12 % |
| Rule from section 6.4 | Hard Surface | Mixed | Mixed |

### 13.2 What was learned

1. **The flat-surface detector is very stable**: for edge angles between 1° and 5° and normal deviations between 3° and 10° the result barely changes
   (cube 62–63.5 %, ant 0 %, cat 20.1 %). **The only sensitive parameter is the minimum patch area**: lowering it from 1 % to 0.5 % raises the cube to 80 %, the cat to 27.5 %
   and makes 1.7 % appear in the ant (2 patches). Proposal: define that minimum **in target quads** (for example, equivalent to a few dozen quads) and not as a percentage of the area.
2. **Right angles are NOT useful for recognizing a hard part.** The cube is a **beveled** block: its corners are resolved with ~45° edges, so only 24 edges measure 90° and only 8 % of its
   long chains are right-angled. The "coherent right angles ≥ 60 %" rule from the original study would have failed with it; the flat-area rule saved it.
   **We must talk about "coherent edges" (long and clean), not just 90°.**
3. **Roughness cleanly separates the ant from the rest**: 49 % versus 4.3 % (cube) and 2.4 % (cat). It confirms that the ant's facets are a rough/faceted finish and not crisp design edges.
   It is the right measure for deciding how much noise to filter (minimum detail size and polish passes).
4. **The cat is clearly mixed**: 70 % of its area is smooth, 20 % is flat (a bottom face of 12 %, four faces inclined at 45° of ~1.6–1.9 % each, which look like chamfers of the base, and a top face of 1.3 %) and only 0.7 % of its edges are hard edges.
   Its "hard parts" are mostly flat, not creases.
5. **The ant confirms the ambiguity of the original study**: 0 % flat, but 10 % hard edges and 790 edges at 90°. It is a faceted organic with real hard details; that is why it needs edge protection **and** noise filtering at the same time.
6. **Symmetry**: measuring with the box center gives 96 % (ant) and 100 % (cat) in X, and low (0–12 %) in the other axes: it detects the candidates well. The cube (53 %) is not symmetric.
7. **Very different densities**: the cube has only 272 equivalent quads (any larger target *increases* detail; the v0.4 warning flags this) and the cat 328,747 (it needs the working copy: 657 K triangles → ~80 K).

### 13.3 Model health: a practical finding (the cat)

The cat has **3 shells**: the body (657,394 triangles) and two tiny ones of **70 and 30 triangles** near (0, −10.7, 4), with **11 non-manifold edges** (shared by 4 triangles):
10 in that area and 1 at (8.59, −6.55, 4.0). They are tiny details stuck to the body.

- **Risk [assumed]:** QuadriFlow may fail or produce artifacts with non-manifold geometry. v0.4 does **not** clean this: it only merges duplicate vertices and removes loose vertices.
- **What to do today, by hand:** in Edit Mode, `Select > Select All by Trait > Non Manifold` locates those edges; delete or separate the two tiny shells before remeshing.
- **What the study proposes:** the analysis should include a **health report** (tiny shells, non-manifold edges, open edges, degenerate triangles) and offer to clean them or, at the least, warn before starting.

### 13.4 Revised framework: three pieces of evidence governing three groups of parameters

With these data, a single global "type" turns out to be too coarse. It is more faithful to measure three **independent** things and have each govern its own group of settings (the Hard Surface / Organic / Mixed label remains as a summary for the user):

| Evidence | Measured with | Governs | Cube | Ant | Cat |
|---|---|---|---|---|---|
| **Flatness** | % of area in flat patches | Treatment of flat patches (capability C1, future) and less polish in those zones | high | none | medium (base and chamfers) |
| **Edges** | % of hard edges and long, clean chains | Angle, minimum detail size and corner snapping | very high | high but noisy | few |
| **Roughness** | % of area with edges of 10°–35° | Noise filter: minimum size and polish passes | low | **high** | low |

Settings that would result for each model (**provisional**, with the existing properties):

| Property | Cube | Ant | Cat |
|---|---|---|---|
| Hard-edge angle | 25–30° | 35° | 35° |
| Minimum detail size | 1 | **3** (a lot of noise) | 2 |
| Snap corners to lines | yes | yes | yes |
| Polish passes | 1 | **4** | 3 |
| Flat patches (C1) | **yes** | no | **yes** (base and chamfers) |
| Suggested symmetry | no | X (96 %) | X (100 %) |

### 13.5 Decision rule, version 2 (provisional)

```
Hard Surface  if  flat_area ≥ 50 %
              or  (hard_edges ≥ 5 %  and  roughness ≤ 10 %  and  coherent_part ≥ 80 %)
Organic       if  flat_area < 5 %  and  (hard_edges < 2 %  or  roughness ≥ 25 %)
Mixed         in any other case
```
With the three real models it gives: cube → **Hard Surface**, ant → **Organic** (faceted), cat → **Mixed**. It matches the labels you gave them.
**Warning:** those thresholds were tuned by looking at these three models; with so few examples it is very easy for them to fail on others. They must be validated with more models before being trusted.

### 13.6 Consequences for the phased plan

- **F4 (analysis and Automatic)** must include: flatness, edges, roughness, **model health** and symmetry suggestion, with visible confidence.
- **F6 (flat patches)** becomes more valuable: 63 % of the cube and 20 % of the cat are flat. The minimum patch area must be expressed in target quads.
- **New task F2b:** health report and cleanup of tiny shells and non-manifold edges, **before** the remesh. It is independent of the rest and reduces the risk of failures with real models such as the cat.
- The v0.4 "right angles" detection remains useful for preserving corners, but must **not** be used to decide the model type.
