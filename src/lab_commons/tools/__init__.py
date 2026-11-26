"""THE TOOL DOOR: every external tool reaches a machine through ONE mechanism, by its declared mode.

User ruling 2026-11-25: installing a vendor tool is a family MECHANISM, never a hand install. Usage:

* ``python -m lab_commons.tools install <tool> [--version V] [--repo DIR]`` -- a MANAGED tool
  (``[--installer FILE] [--mode silent|interactive]``) is downloaded, hash-verified and installed under the managed
  root; a USER-INSTALLED one (``[--home DIR]``) is probed at the install home this machine declares;
* ``python -m lab_commons.tools list [--json]`` -- the catalogued tools and what this machine has;
* ``python -m lab_commons.tools check [<tool>...] [--repo DIR]`` -- the lab-commons probe's verdict.

Where each fact lives:

* each tool's mode, and what each managed release IS (source, sha256, installer kind, binary) -- ``tools.toml``,
  read by :mod:`lab_commons.tools.catalogue`;
* which version a repo NEEDS -- its ``[tool.lab_commons.tools]`` (:mod:`lab_commons.compute.presence`);
* the managed LAYOUT under ``<home>/tools`` and the install RECORD every probe reads --
  :mod:`lab_commons.tools.managed`;
* a managed install (staging, sha256 before anything runs, versioned directories, verify, record) --
  :mod:`lab_commons.tools.install`;
* a user-installed tool's home -- the config source's host file ``[dev.vendors.<vendor>] home``, read and probed by
  :mod:`lab_commons.tools.vendor`.
"""
