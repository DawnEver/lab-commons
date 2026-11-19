---
name: Retrospectively audit the HPC cancel composition
description: Record the official two-parent source audit without rewriting an earlier unprotected merge or claiming runtime acceptance.
metadata:
  type: project
---

The official dev_lab.dev.mergeaudit._audit was run against the exact committed b7c53a6400af303a718f79d7c000fd02c5a64421 merge, its actual parents 92e3a9c3af00198592c219492f58095bc5b4c5fa and e76dc0175a02e9e78f891da927c274c140523e38, and the complete tests root. It returned zero deviations. The ignored JSON receipt preserves those inputs. This retrospective result does not pretend the missing hooks ran during that earlier merge; history is unchanged. A subsequent supported hook inspection confirms all six declared stages now have live shims.

The repair source d1e96abe944223b522c14b61125315d84f221364 remains unexecuted locally. Publication must use the currently checked-out same-name branch, followed by ci.yml workflow_dispatch with the exact published full SHA and both complete owners tests/test_hpc_span.py and tests/test_hpc_cancel.py. The workflow's measurement job, not a full-part result, runs jobmeasure and uploads its receipt and underlying verify evidence. No PR or smoke input may be combined with this measurement. Canonical outcome and complete-owner counts must be read back before claiming qualification. No real HPC cancel was invoked.
