"""The distributed compute system: one admission rule for every host, every resource equal.

:mod:`lab_commons.compute.model` is the pure core (resources, jobs, ``rank``) and
:mod:`lab_commons.compute.admission` its pure rule (``admit``: needs present, demand fits);
probes, learned demand and the serve loop arrive as sibling modules, each feeding it.
"""

__all__: list[str] = []
