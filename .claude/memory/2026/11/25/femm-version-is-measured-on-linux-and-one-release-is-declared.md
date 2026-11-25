---
name: femm-version-is-measured-on-linux-and-one-release-is-declared
description: "User ruling 2026-11-25 (option A): femm_headless on Linux reads its version from the install's version.env, never 'unread'; [tool.lab_commons.tools] gains the one-release shape {exact, reason} for 'only the newest FEMM 5' (5.0.20261112)"
metadata:
  type: project
---

# FEMM's version is measured on Linux, and one release can be declared (2026-11-25)

User ruling 2026-11-25: "(3) A -- but our FEMM is strictly only the latest FEMM 5 now; older versions are forbidden"
(user, translated) -- option A (the
femm_headless install carries a version file, read by the probe), and the family uses ONLY the newest FEMM 5 release.

- Before: `binary_version` read Linux `femm_headless` as `('install', 'unread')` (f158e87e), so a serving repo's
  `femm = {floor = "5.0", ...}` refused it as unordered -- FEMM read ABSENT on every Linux host.
- `toolchecks.FEMM_VERSION_FILE = 'version.env'` beside the binary: femm-qt's own build stamp
  (`cmake/BuildInfo.cmake` writes `<build>/generated/version.env`: `#` comments, `FEMM_VERSION=5.0.YYYYMMDD`,
  `FEMM_BUILD=<n>`). `femm_version` reads its `FEMM_VERSION`; a missing file, unreadable file or no dotted
  `FEMM_VERSION` raises `VersionUnreadable` naming the file and the door (reinstall femm_headless so its install
  carries it). `presence.probe` records that as the version evidence: absent, with or without a declaration.
- Windows keeps its measured source, the binary's VS_FIXEDFILEINFO, decoded by `femm_release` back to femm-qt's
  one number (`WinVersion.cmake` splits the 8-digit patch: `5.0.2026.1112` -> `5.0.20261112`).
- `[tool.lab_commons.tools]` accepts `<tool> = {exact = "<version>", reason = "<why>"}` (`Requirement(exact=True)`,
  cached as `==<version>`): older is refused with `upgrade to <version>`, newer with `move the declaration to it
  once it is measured`. A bare string (a pin without reason) and exact beside floor/below stay refused.
- Newest FEMM 5 on 2026-11-25: `5.0.20261112` (femm.info download5 page: `femm-5.0.20261112-1157.exe`/`.dmg`;
  femm-qt `CMakeLists.txt` `project(femm-qt VERSION 5.0.20261112 ...)`).
- NOT HERE: the declaration row itself belongs in the FEMM-serving repo's pyproject
  (`femm = {exact = "5.0.20261112", reason = "..."}`); no family installer writes version.env yet -- the Linux
  femm_headless was a hand source build, so it reads absent until its install carries the file.
