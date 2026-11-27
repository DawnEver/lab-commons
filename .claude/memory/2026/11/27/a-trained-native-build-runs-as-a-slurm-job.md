---
name: a-trained-native-build-runs-as-a-slurm-job
description: "User ruling 2026-11-27: a research job's artifact must carry a profile-guided (PGO) native, not only the plain release build. Design: a repo declares native.trained (prefetch on the login node, build in a Slurm job of the grant, PROVENANCE required); [job] native selects plain or trained"
created: 2026-11-27
metadata:
  type: project
---

# A trained native build runs as a Slurm job (2026-11-27)

## Why

User ruling 2026-11-27 (wdg-optimization session): research jobs through `lab_commons.hpc submit -c` built the Rust
kernel with the plain release command `[tool.lab_commons.hpc] native.build`, while the consumer's production kernel
is profile-guided (wdg-lab `scripts/pgo_wheel.py`: instrument, train on the production CLI, merge, rebuild; 1.23x
useful nodes per allocated CPU-second, wdg-lab decision #252). A timing measured through the door was therefore not
comparable with the production one. "为啥 这是缺陷！ 必须要让 hpc 工具支持！" (user): that is a defect, and the HPC tool
must support it.

## Constraints that shape the design

- A trained build RUNS THE WORKLOAD (minutes of CPU over an allocation), so it never runs on a login node; the plain
  build does run there (`artifact_script` over `runner_for(grant, BUILD_TIMEOUT)`).
- Its network-dependent preparation (crates for an `--offline` cargo, the env's packages) is done on the login node:
  whether compute nodes reach the internet is a cluster fact this module does not assume.
- The compute node must see everything the build reads: the exported tree and the env are made under the account
  root (compute-visible by the layout's definition, `lab_commons.hpc.layout`), not in the login node's `$TMPDIR`.
- The profile is retrained per tree and never carried over (the consumer's provenance rule): the trained native is
  keyed on the native inputs AND the trained recipe, distinct from the plain key, and must leave `PROVENANCE`.

## Design

- Declaration (the consumer's pyproject), beside the plain build, which stays a selectable option (the A/B baseline):

      [tool.lab_commons.hpc.native]
      inputs = [...]
      build = "<plain>"
      [tool.lab_commons.hpc.native.trained]
      prefetch = "<login node, may use the network>"
      inputs = ["<what the training reads beyond native.inputs>"]
      build = "<compute node>"
      needs = ["<need-set the build's env needs>"]
      cpus = 32
      mem = "64 GiB"
      wall = "50 min"

- Selection: `[job] native = "plain" | "trained"` (default `plain`); `trained` on a commit that declares none is
  refused by name.
- Keys: the trained native key is over the native inputs plus the trained `inputs` (the workload's own source: a
  profile is retrained on the tree that drives it), the trained recipe (prefetch, build) and the env key; its env
  adds the declared `needs`. The plain key is unchanged by the trained declaration. The artifact file is `<sha>.trained.tar.gz`, so the plain and trained artifacts of
  one commit never stand in for each other.
- Build: the artifact script makes its scratch under `<root>/native/` (owned by the layout), runs `prefetch` there on
  the login node, then `sbatch --wait` of a one-task job with the declared cpus/mem/wall on the grant's account and
  its partitions that need no qos (the grant's setup lines first). The job runs the keyed build (lock + installed
  marker, as the plain build). A trained native with no `PROVENANCE` fails the build; the artifact facts print it.
- No fallback: a failed trained build fails the submission; it never serves the plain one.

## Proven end to end (2026-11-27)

- Consumer commit 3ab30c0ab (a PGO wheel build declared as `native.trained`, 32 CPUs, 64 GiB, 50 min), submitted from
  a workstation through `lab_commons.hpc submit -c` at fdb3a45d.
- The first try failed in the login-node prefetch and said why: the consumer does not track `Cargo.lock`, so
  `cargo fetch --locked` cannot create it. The consumer's prefetch is now `cargo fetch`. The refusal named the log and
  the cause, so the wall taught.
- The second built `<sha>.trained.tar.gz` in one Slurm job and printed its PROVENANCE: `pgo=true`,
  `profdata_sha256=a4aed483...`, 19 profraw files, `wheel_sha256=0c085446...`.
- Array job 8498901: 2/2 items ok. Item 0 asserts the loaded extension resolves under the artifact's `native/` and
  that its PROVENANCE says `pgo=true`; item 1 runs one production CLI cell.
- The local targeted verify was walled on this base by the pending `[tool.dev_lab.placement]` -> `layout.toml`
  migration (not this change's), so `tests/test_hpc_trained_native.py` waits for the forge.
