"""The distributed compute system: one admission rule for every host, every resource equal.

:mod:`lab_commons.compute.model` is the pure core (resources, jobs, ``rank``) and
:mod:`lab_commons.compute.admission` its pure rule (``admit``: needs present, demand fits);
probes, learned demand and the serve loop arrive as sibling modules, each feeding it.

One computation is a :class:`~lab_commons.compute.unit.Unit` (``unit``: engine, version, inputs, outputs, keyed
by content), a calculation a :class:`~lab_commons.compute.graph.Graph` of them (``graph``); results live in the
content-addressed :class:`~lab_commons.compute.store.ResultStore` (``store``) and a graph runs through an
:class:`~lab_commons.compute.execute.Executor` (``execute``: in-process here, else offered as a Job).
"""

__all__: list[str] = []
