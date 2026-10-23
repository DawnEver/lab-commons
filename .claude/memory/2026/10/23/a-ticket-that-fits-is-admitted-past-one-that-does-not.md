---
name: a-ticket-that-fits-is-admitted-past-one-that-does-not
description: The box queue moved from strict FIFO to backfill by reservation (box.order); why it cannot starve, and the holder rule.
metadata:
  type: decision
created: 2026-10-23
accessed: 2026-10-23
---
# A ticket that fits is admitted past one that does not (2026-10-23)

- DEFECT (motronics long run, 02:36-11:06): ticket 4305 (`case run ... lucid`, 6.45 GB) could not fit for
  hours and, strict FIFO, held three `jmag read s.job` tickets (1.74 GB each) that DID fit; each read gates a
  JMAG station, so 3 of 6 Job System slots idled. Same at 00:35 behind three ~6.9 GB `case run` tickets.
- RULE (`lab_commons.box.order`, the one ordering rule; strict FIFO deleted, no flag): an earlier contending
  ticket's EARLIEST START is the first moment (now or a held claim's deadline) the room, with every claim
  ending by then released, admits it; a later ticket that fits now is admitted iff, held to its own deadline,
  it moves no earlier contending ticket's earliest start later.
- WHY THIS AND NOT AGE-BOUNDED BYPASS: a bypass bound (N overtakes / T seconds) returns to head-of-line
  blocking once spent -- exactly the idle the defect measured -- and its N/T is a number nobody can derive.
  The reservation uses only what the ledger has (claim deadlines) and proves the bound: no admission after a
  ticket was written moves its start later, and that start is a held claim's deadline.
- Named limits: room used OUTSIDE the ledger is measured, not scheduled (a ticket that fits not even with
  every claim released has no start to protect); a ticket written before tickets declared demand still
  orders everything behind it.
- HOLDER RULE (the 2026-10-19 deadlock, recorded in motronics memory): a process holding a claim is ordered
  behind no waiter. Identity is the pid, so threads of one process that holds a claim pass the queue together
  (LocalExecutor units); still bounded, since a holder stops being one when its claims end.
- Tests: `tests/test_box_order.py` (planted ledger, other-pid claims, a subprocess holder for the deadlock).
