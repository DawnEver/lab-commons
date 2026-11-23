# Devices on the box -- GPU and NPU

What a consumer needs first; the mechanism and every rule's reason live in `lab_commons.box.devices` (module
docstring), the names in `lab_commons.dimensions`.

## Ask

A box demand (`lab_commons.box.run` / `hold`, or `python -m lab_commons.box run --seat <name>=<n>`) names:

| name | meaning |
| --- | --- |
| `gpu`, `npu` | whole count of devices of that kind |
| `vram_gb` | GiB of GPU memory on EACH GPU taken; a GPU claim without `exclusive` must state it |
| `exclusive` | `1`: take each GPU whole (no claim beside it; only an idle device) |
| `f64`, `f32`, `f16`, `int8` | `1`: every device taken must compute this natively |

Examples: a k-fold fit `{'cpu': 4, 'mem_gb': 8, 'gpu': 1, 'vram_gb': 4, 'f32': 1}` (several share one GPU);
a double-precision solve `{'cpu': 8, 'mem_gb': 32, 'gpu': 1, 'exclusive': 1, 'f64': 1}`; an int8 inference
`{'cpu': 1, 'mem_gb': 2, 'npu': 1, 'int8': 1}`.

## Admitted or not

- A GPU is shared by memory: its share free is its memory less the larger of what is resident now (any process,
  admitted or not) and what its claims declared. `exclusive` needs an idle device: no process, utilisation known
  and below `BUSY_UTIL_PCT`.
- A device lacking a demanded precision is never taken (an f64 demand never lands on an NPU or an Intel iGPU).
- A device whose use cannot be observed (today: Intel GPU/NPU, inventoried by the OS) is listed, never lent.
- A kind whose probe could not read it is blind: refused at once, naming why. The refusal text always names the
  devices and why each was not usable.

## Told

The admitted child's environment carries the binding, set once by the box (`devices.binding`):
`LAB_COMMONS_BOX_DEVICES` (every device id held, e.g. `gpu.nvidia.0`), `CUDA_VISIBLE_DEVICES` (NVIDIA indices,
`CUDA_DEVICE_ORDER=PCI_BUS_ID`), `LAB_COMMONS_BOX_OPENVINO` (`GPU.<n>`, `NPU`). A run that claimed none sees
them empty. Equal claims on an equal box bind equally (devices are taken in id order), so a seeded run that holds
one NVIDIA GPU always sees it as CUDA device 0.

## See what is held

`python -m lab_commons.box claims` shows each claim's `devices`.

## Cluster

The same `gpu` name sizes a Slurm plan and becomes `--gres=gpu:N` (`lab_commons.hpc.cluster.render_script`).
