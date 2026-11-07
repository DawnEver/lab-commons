---
name: patch-ring-validation-is-shared-data-not-topology-proof
description: One NumPy-only ring boundary feeds both adapters, while MPL compound-path encoding stays private and polygon topology remains unproven.
type: decision
date: 2026-11-06
---

# Patch ring validation is shared data, not topology proof

The coordinator authorized the two LOW review follow-ups on a new unpublished private branch
fix/report-patch-ring-validation, based exactly on f12752e6eaa234ec69c0565e1cf0ad6095bcdce2.
The original pr/report-patch-holes ref remains unchanged; the external PR92 auto-sync claim was
not appropriated, and no PR, push, credential operation or dependency update was performed here.

Patch.rings is the NumPy-only owner of float coercion, (n, 2) shape with at least three rows and
finite-coordinate validation. Both drawing adapters call it once per patch before attaching any
artists. Mutable input arrays are revalidated on reuse, not cached. Validation is not moved into
the dataclass constructor: a description can still be constructed to inspect its neutral options.
The new method reports the malformed boundary's index uniformly for outer and interior rings.

MPL's opposite-winding and closed compound Path encoding moved unchanged in meaning to the
backend-private _compound_path helper. The drawing method retains its original style, value scale,
alpha, hatch, edge, legend and collection behavior. Bokeh keeps its own MultiPolygons encoding and
reads the same checked arrays, without a second ring coercion.

This does NOT certify polygon topology. Holes outside an exterior, self-crossing rings and overlap
are not validated by numeric shape/finite checks; producers still own valid topology. No topology
acceptance, geometry-kernel equivalence or physical/solver evidence is claimed by these tests.

## Actual protected lightweight TDD

The coordinator approved exactly:

- tests/test_viz_patch_rings.py
- tests/test_viz_patch_holes.py
- tests/test_viz.py::test_the_primitives_default_to_the_neutral_choice

The unimplemented source produced 28 failures and eight passes in 5.05 seconds: log SHA256
c99796cc76c708944268e194175c292fc636a864b7df887200b20b9def64c483. Missing rings and the old
backend malformed-input behavior were genuine RED evidence. The first implementation passed all
36 runtime tests but failed four lint checks; their semantic-preserving fixes retained every test.
The final own-prefix official verify passed all 36 in 3.80 seconds, tree eb65d5963517a7e1,
env d6fe81117561008a, log sha256:38db422be5fdd2af382cfe46e921593ae978eb1c35330eabce9024bf7fa69d5b@19.

Pure controls cover valid ring order/data, both boundary positions, empty/short/rank/width/ragged
data, NaN/infinities/coercion errors, mutations after a previous read, shared one-call adapter use
and batch refusal before any artist is attached. Existing reversed-winding, scalar/category style,
transparent-hole and independent-island controls remain intact. Agg draws only to an in-memory
buffer: no image file, CAD, mesher, native build, solver, network or credential access occurs.
MPLCONFIGDIR stays inside this worktree. The old locked 2ef kit was used only for its protected
local verify and normal commit guards, never forge/network operations. Lock files are unchanged.
