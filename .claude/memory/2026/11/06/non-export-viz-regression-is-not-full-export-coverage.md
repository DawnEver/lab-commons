---
name: non-export-viz-regression-is-not-full-export-coverage
description: Exactly nine file-export nodes are omitted from a 119-pass protected regression; earlier broad no-image wording does not describe the old full viz suites.
type: decision
date: 2026-11-06
corrects: patch-holes-are-transparent-region-boundaries
---

# Non-export viz regression is not full export coverage

## Custody clarification without rewriting earlier records

The older patch-holes-are-transparent-region-boundaries entry's blanket phrase about no image
file must not be read as a claim about its entire full-viz regression. Those earlier 92-test runs,
and the later full 100-test CI/viz selection, include existing synthetic PNG/HTML export tests
using pytest temporary paths. They did not commit images or create diagnostic asset packets.
The more restrictive current ruling forbids PNG or rendered-HTML files even in temporary storage;
this run therefore explicitly excludes export I/O rather than pretending it tested it in memory.
The committed older records remain untouched; their original results/hashes are not relabelled.

## Exact current selection

The nine selected files are test_ci_method_bootstrap_changes_only_the_frozen_installer.py,
test_arch_the_ci_workflow_is_rendered.py, test_viz_patch_holes.py, test_viz.py, test_viz_mpl.py,
test_viz_bokeh.py, test_viz_adapters_are_peers.py, test_viz_gate.py and test_viz_patch_rings.py,
all under tests/. From those files only these nine exact nodes were passed as --deselect:

- tests/test_viz_mpl.py::test_the_3d_subject_reaches_the_saved_file
- tests/test_viz_mpl.py::test_saving_writes_a_file_and_opens_no_window
- tests/test_viz_mpl.py::test_every_frame_of_a_canvas_reaches_the_saved_file
- tests/test_viz_mpl.py::test_saving_and_showing_are_not_alternatives
- tests/test_viz_bokeh.py::test_the_shared_description_reaches_the_page
- tests/test_viz_bokeh.py::test_a_raster_extension_is_rewritten_to_the_format_this_library_writes
- tests/test_viz_adapters_are_peers.py::test_the_same_description_draws_through_both
- tests/test_viz_adapters_are_peers.py::test_the_same_panel_grid_draws_through_both
- tests/test_viz_adapters_are_peers.py::test_the_same_twin_axis_draws_through_both

No -k filter, marker, skip, waiver or test-body modification was used. All other independent
draw/style/scale and source controls in these files remain selected. NullRenderer's no-write
checks remain; the Bokeh overlapping-layout refusal also remains because _layout raises before
bokeh_save is called, so it creates no HTML. Agg is forced and Bokeh's retained show control is
noninteractive. Configuration tests may write their temporary TOML/metadata, not rendered output.

## Actual terminal evidence

The first attempt ran 119 passing tests but was correctly INCONCLUSIVE: the old kit could not
establish its skip census for an additional --basetemp option. It was not promoted or reported as
a passing gate. Log sha256:4d8307c052f7fbce2d46130c4e6c568d7df92872a8555e8a697671137e853d34@26.
Only that unsupported extra option was removed. Process-local TEMP/TMP/TMPDIR instead point inside
this worktree, with MPLCONFIGDIR also local. The same nine exact exclusions were retained.

The protected own-prefix explicit --rerun finished PASS: 128 collected, nine deselected,
119 selected and 119 passed in 9.64 seconds. Tree eb65d5963517a7e1, env d6fe81117561008a,
log sha256:3d87ea599eca611d88309769d8e6a7d2e51c1c7a45c4cfef975b1364c0924eee@26.
The current temporary runtime directory contains zero PNG/HTML/SVG files. This is NOT full128
or export-I/O acceptance. The earlier exact36 pass and its original log also remain valid.

No CAD, mesher, solver, native build, network/forge/credential call or dependency update occurred.
The original PR92 branch remains f12752e6; work stays on private fix/report-patch-ring-validation.
