---
name: a-tool-seat-is-measured-like-a-core
description: a seat's free count is measured -- declared less max(ledger-held, observed in use); a person declares a claim (box claim), no seat_margin; the observation is declared per capability as process images
created: 2026-10-25
---
# A tool seat is measured like a core (2026-10-25)

Problem (user): WS1/WS2 keep doing a person's heavy JMAG work first and have several seats; a solve started
outside the ledger (Designer, Scheduler, jam.exe) was invisible, so a forge jmag part could take that seat.

- `[capabilities.<name>] in_use = { process = ["jam.exe", ...] }` names the images whose running count is the
  seats in use now (`box.machine.watched`, `box.supply.observe`, read in `Reading.now` from one Toolhelp snapshot).
- `free = size - max(held by claims, observed)` in `box.supply.room`, the one
  formula the ledger and the forge's `compute.probe.box` both read (probe.box now takes seat counts from it).
  MAX, not sum: a claim's own solver is in the table too.
- A watched seat with no observation (table unreadable, or a Reading that never observed it) lends 0 and the
  refusal names why (`Reading.unseen`, appended by `ledger._decide`).
- NO RESERVATION FOR PEOPLE (user ruling 2026-10-25, superseding the same-day `[reserve] seat_margin`, deleted as
  baggage: a machine file naming it is refused as an unknown key). A person's or session's work DECLARES its use
  as an ordinary ledger claim: `python -m lab_commons.box claim --cpu N --mem-gb G --seat jmag=K --wall 4h --why
  "..."` (`ledger.declare`: kept row, EMPTY probe = lives to its deadline, recorded not admitted), listed by
  `box claims [--json]`, dropped by `box release <id>`. Every admission (a forge hold) gets only the room outside
  it; forgetting to declare still counts via max(held, observed). `[reserve] cpu_margin`/`mem_margin` stay: OS
  headroom (1 core / 10%), not a reservation for people.
- JMAG images (motronics `hamilton/adapters/jmag/processes.py`, checked against the install 2026-09-19): the
  solver family is `jam.exe`, `jam_c.exe`, `jam_intel_mpi_b.exe`, `jam_intel_mpi_c.exe`; `execsolver.exe`
  launches `jam.exe` (counting both would count one solve twice); `scheduler.exe` and `designer.exe` are not seats.
- A `supply` probe that fails, times out or prints no count NO LONGER falls back to the declared `seats`
  (coordinator, aligned with `DESIGN-one-resource-path`): `seat_supply()` returns `(sizes, blind)`; the tool
  keeps its declared size (so it queues rather than "larger than this box") but `Capacity.blind` makes `room`
  lend 0 and `ledger._decide` names the reason. `in_use` is the second declared key on the SAME capability row,
  read beside `seat_supply` -- one seats path, no parallel reader.
