---
name: six-doors-from-a-motronics-long-run
description: Mechanisms A-F on pr/retry-and-live-run-doors (PR #8), each earned by a 2026-10-19/20 motronics long-run incident.
created: 2026-10-20
accessed: 2026-10-20
tags: [netverb, liverun, box, dep, presence, durable]
metadata: {pr: 8}
---

# Six doors from a motronics long run (PR #8)

- A `netverb`: one definition of a network verb; every lab-commons network call retried. `transport 'file'
  not allowed` is permanent, and `dep` passes `-c protocol.file.allow=always` for local submodules.
  Incident: a gitea auth blip was reported as blocked after one try.
- B `census` + `liverun`: a venv lock (any process executing from it) and a tree lock (a process that reads the
  tree: cwd or argv). `forge serve` chdirs to its work root. Incident: edits and merges into a tree whose run
  was live.
- C `supply.room(asker=pid)`: a holder's nested `outside=True` ask uses its own unused claim; `stuck_at_head`
  says so. Incident: five deadlocks in a day (issue #5).
- D `dep`: a component whose source moved is reinstalled. Incident: numerics-lab `TrialNotEvaluable` missing
  after a submodule move.
- E presence: probes get `stdin=DEVNULL`; a probe that cannot start caches nothing. Incident: `EngineAbsent`
  from a hidden detached process (WinError 6).
- F `durable`: atomic writes for every state file; a torn table is quarantined. Incident: a learned table left
  as 5746 NUL bytes when Python vanished mid-write.
- MODULE_FLOOR 300 -> 308: a minimum, so raising it tightens the scan.
