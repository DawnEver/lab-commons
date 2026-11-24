---
name: html-data-table-for-family-reports
description: "lab_commons.html.data_table: generic sortable/filterable report tables (#249, #250)"
metadata:
  type: project
---

# Generic sortable/filterable data table (2026-11-24)

`lab_commons.html.data_table(columns, rows, *, caption, filterable, table_id)` with `Markup`/`Column`/`Cell`, `data_table_script()`, `data_table_css()`, `html_document(..., data_tables=True)`; assets `html_assets/data_table.js`/`.css` beside navigation.js.

- Escaped by default; `Markup` is the only raw-HTML path (no XSS hole).
- Sort: any header, click or keyboard; numeric-aware (thousands separators), empty/"-" last both ways.
- Filter: space-separated terms AND, case-insensitive.
- Why here: sort/filter is a generic report capability; the first consumer is a downstream campaign delivery that had inlined its own JS (reverted).
- Status: issue #249, PR #250 (pr/html-data-table, b23b5fbf) open, ready; merge is the integrator's. Verify door passed (75 tests incl. JS on the declared engine).
