"""The distributed compute system: one admission rule for every host, every resource equal.

:mod:`lab_commons.compute.model` is the pure core (resources, jobs, reservations, ``admit``, ``rank``);
probes, learned demand, the ledger and the serve loop arrive as sibling modules, each feeding it.
"""

__all__: list[str] = []
