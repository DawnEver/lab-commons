"""Opt-in tier -- split a batch of independent work items across a Slurm cluster, sized to what is free NOW.

WHAT THIS DOES NOT DO, AND WHY. Slurm is already the scheduler; a second one written here would be a
worse copy racing the real one. The two questions Slurm cannot answer for a caller are HOW BIG each
piece should be and WHICH partition to send it to, and those are the only two this layer decides. It
reads the cluster once (:mod:`lab_commons.hpc.slurm`), cuts the work into array tasks that fit the
holes and the caller's own quota (:mod:`lab_commons.hpc.plan`), and hands one job array to ``sbatch``
with a concurrency throttle. Queueing, backfill and retries of a node failure stay Slurm's.

FOUR LAYERS, EACH IGNORANT OF THE ONE ABOVE IT::

    config  (one TOML file -- the single source of every number a run uses)
      -> slurm   (parse ``scontrol``/``sacctmgr`` text into a :class:`Snapshot`; pure, plus one probe)
      -> plan    (Snapshot + Work + Policy -> Plan; pure, no I/O)
      -> run     (render the array script, submit, poll ``sacct``, gather results; I/O through a Runner)

and :mod:`lab_commons.hpc.worker` is what each array task executes on the compute node. The cluster is
reached through a :data:`~lab_commons.hpc.run.Runner` -- a local shell on a login node, or ``ssh`` from a
workstation -- so nothing above ``run`` knows which.

WHERE a run may go is the MACHINE's, not the job's: :mod:`lab_commons.hpc.grants` reads this box's
shares of shared accounts, and :func:`lab_commons.hpc.plan.allocate` picks one from what Slurm reports
each box holding. :mod:`lab_commons.hpc.verdict` runs one commit's test suite through the same layers.

STDLIB ONLY, so no extra gates it: ``tomllib`` reads the config and ``json`` carries the manifest. Like
``lab_commons.dev`` and ``lab_commons.viz`` it is an opt-in IMPORT -- ``import lab_commons`` does not
reach it, and ``tests/test_hpc_gate.py`` pins that against a fresh interpreter.
"""

from __future__ import annotations

__all__: list[str] = []
