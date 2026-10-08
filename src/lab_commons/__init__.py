"""Shared infrastructure namespaces, imported explicitly from their defining modules.

The package root has no runtime facade. Importing a stdlib logging primitive must
not initialize units, process resources, structured logging, or file/path policy.
Use ``lab_commons.log``, ``lab_commons.paths``, ``lab_commons.file_io``,
``lab_commons.units``/``em``, or a named renderer adapter when that capability is
actually required. Each module remains independently importable.
"""
