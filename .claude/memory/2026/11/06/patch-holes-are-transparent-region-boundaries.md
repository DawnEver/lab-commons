---
title: Patch holes are transparent region boundaries
date: 2026-11-06
status: active
---

The migration coordinator approved the minimal plotting-owner extension: a Patch carries one outer
boundary and optional holes in the same coordinates. Existing scalar, colour, edge, label, alpha and
hatch fields retain their meanings and positions. Matplotlib uses a compound Path with opposite
outer/interior winding; Bokeh uses one MultiPolygons polygon with all its rings. Holes are transparent,
not white overpaint; an independently drawn nested region remains visible.

Work is isolated on fix/report-patch-holes, canonical origin/main and origin/integrate/main base
541edb19f4898e67f861a57ef53e40b986d9b6b1. Existing owner worktrees and untracked files were not edited.
No dependency or lock change: uv.lock SHA256
8381a5b62c7b44804a6d72eade02245af2bd1f99ba4d6abf7841fa7cbe77c9ee.

An empty Python 3.15 prefix correctly refused unavailable PyYAML wheels. It is preserved as
.venv315-cold. Independent .venv313 remains preserved. The standard .venv now uses existing Python
3.13.16. The official frozen/all-extras cold install used registry wheels only, then the project
bootstrap door installed only the two pure-Python projects without dependencies. This initial cold
stage had no kit/box lock, explicitly authorized and never represented as protected. Bootstrap's
same-process post-install import refused because new editable .pth files were not yet loaded; a new
own-prefix process successfully imported the kit. No native build, foreign environment mutation,
credential operation, image file or push occurred. The locked dev-lab direct_url is
2ef3d35b84d142324b4cddc2ecf27fe4f9f848db; 51 installed distributions are compatible.

Actual protected own-Python TDD: absent holes field produces six TypeError failures, log
sha256:9279dba1b13d70f01f12e5f769c52bd8456d785ad6b67ce54af43c647c6e8e19@135.
After implementation, six selected viz files passed 92 tests in 11.42 seconds, including seven new
controls for two holes, reversed winding, value scales and categorical styling, and an in-memory Agg
pixel control showing underlying regions through both holes and a separate island. Log
sha256:c09f33d209373fd787ba4d2b9d50820d6616033fe8b0408b61f6c14a5c0585d7@22.
The standard-prefix explicit --rerun also passed all 92 tests in 16.21 seconds, log
sha256:62f5fdda6d124aea7360d6a654ad00f1566bd98b14e798c58c6000932ce7d82a@22.
This is a local owner candidate, not yet a
published or adopted Motronics dependency; stored-report consumption follows canonical publication.
