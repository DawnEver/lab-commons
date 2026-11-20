---
created: 2026-11-09
---
# The box lends GPUs and NPUs by device

- User 2026-11-08: GPU acceleration framework in lab-commons, widened the same day to one heterogeneous-device
  vocabulary (WS5: RTX PRO 4000 dGPU, Intel iGPU, Intel AI Boost NPU). `lab_commons.box.devices` reads devices
  (nvidia-smi; Intel via Windows CIM/PnP or Linux sysfs), `assign` decides by device, `binding` tells the child.
- The names are `lab_commons.dimensions` (moved out of `resources/`: box importing it through the engine package's
  `__init__` was a cycle; the table is a leaf). `gpu`, `npu`, `vram_gb`; requirements `PRECISIONS`, `EXCLUSIVE`.
- A GPU is shared by memory (learn-lab's k-fold fits): share free = total - max(resident now, claims' declared).
  `exclusive` needs an idle device. The engine decides everything but device names; `assign` decides those, so
  a count never pretends to know which device a share fits on.
- WS5's 100 % reading on 2026-11-08 was learn-lab's own U-Net fits admitted on cpu/mem only: undeclared GPU use.
  Under this design it reads as foreign: refuses `exclusive`, leaves 14 GiB to share.
- Open: Intel GPU/NPU use is unobserved, so they are inventoried and never lent until a reader exists (Windows
  `GPU Engine` counters by LUID is the candidate). P/E core class and affinity are not done: the enforcers
  (`_win` Job Object, `_linux` systemd `AllowedCPUs=`) would hold them; nothing reads core class yet.
  `--gres` is unit-tested only (no cluster reachable).
