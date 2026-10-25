---
name: a-tool-seat-is-measured-like-a-core
description: a seat's free count is measured -- declared less max(ledger-held, observed in use) less [reserve] seat_margin; the observation is declared per capability as process images
created: 2026-10-25
---
# A tool seat is measured like a core (2026-10-25)

Problem (user): WS1/WS2 keep doing a person's heavy JMAG work first and have several seats; a solve started
outside the ledger (Designer, Scheduler, jam.exe) was invisible, so a forge jmag part could take that seat.

- `[capabilities.<name>] in_use = { process = ["jam.exe", ...] }` names the images whose running count is the
  seats in use now (`box.machine.watched`, `box.supply.observe`, read in `Reading.now` from one Toolhelp snapshot).
- `free = size - max(held by claims, observed) - [reserve] seat_margin.<name>` in `box.supply.room`, the one
  formula the ledger and the forge's `compute.probe.box` both read (probe.box now takes seat counts from it).
  MAX, not sum: a claim's own solver is in the table too.
- A watched seat with no observation (table unreadable, or a Reading that never observed it) lends 0 and the
  refusal names why (`Reading.unseen`, appended by `ledger._decide`).
- `seat_margin` is the same kind as `cpu_margin`/`mem_margin`: kept, not reserved for waiters (ruling 10-14 stands).
  The at-once "larger than this box" check subtracts it too.
- JMAG images (motronics `hamilton/adapters/jmag/processes.py`, checked against the install 2026-09-19): the
  solver family is `jam.exe`, `jam_c.exe`, `jam_intel_mpi_b.exe`, `jam_intel_mpi_c.exe`; `execsolver.exe`
  launches `jam.exe` (counting both would count one solve twice); `scheduler.exe` and `designer.exe` are not seats.
- A `supply` probe that fails, times out or prints no count NO LONGER falls back to the declared `seats`
  (coordinator, aligned with `DESIGN-one-resource-path`): `seat_supply()` returns `(sizes, blind)`; the tool
  keeps its declared size (so it queues rather than "larger than this box") but `Capacity.blind` makes `room`
  lend 0 and `ledger._decide` names the reason. `in_use` is the second declared key on the SAME capability row,
  read beside `seat_supply` -- one seats path, no parallel reader.
