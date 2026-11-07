---
name: a-new-project-kit-does-not-replace-the-trusted-base-review-protocol
description: Preserve external integration ancestry, fully materialize the frozen new kit with registry-wheel guards, regenerate owned assets, and explicitly bridge only the old trusted review protocol until phase two.
type: decision
date: 2026-11-06
supersedes: a-frozen-method-bootstrap-does-not-change-the-trusted-review-door
---

# A new project kit does not replace the trusted-base review protocol

## Ancestry and exact environment

The coordinator authorized reconciliation of the external auto-sync after its claim ended.
Normal merge a56126ebcb57d8ba7087ae9a39383884192d276f has parents
9d02453cbbd19d91ec2969cb8417c109dc0b482c and
64519efab3b225fa5068b99e6b1402cb2fa4d311; both remain ancestors. The latter already retains
f127 and canonical ed24. The first merge subject's unsupported type was refused by Commitizen;
a normal chore(ci) commit completed it, without bypass, reset, history rewrite or push.

The external lock is authoritative, SHA256
3392260b2e4996357c54d33f3554c00387fa7bbe9e79fb42aa3d08ad0024cc9b.
The public protected dev_lab.dev.dep --bootstrap <worktree> door generated a complete uv sync
with every declared extra and --frozen. Plain --sync was not used: its implementation upgrades
floating requirements. Independent tooling invoked the door, which identifies and seats the
TARGET worktree's .venv, not the invoking environment. No partial install was treated as adoption.

The initial global UV_NO_BUILD=true offline attempt refused dev-lab's VCS binary availability,
even though a canonical cached wheel could be identified. The cache identity/metadata was not
edited. The official three-attempt refusal measured env d6fe81117561008a unchanged.

Local uv 0.13.5 argument-parser probes, against a deliberately nonexistent project, proved that
UV_NO_BUILD_PACKAGE is SPACE-separated: two names separated by space reached only the project
path refusal, while comma/semicolon forms failed environment parsing. Those probes built or
installed nothing. From the new lock's 54 registry names plus the active Hatch dependency closure,
a 61-name source-build prohibition was derived, sorted and joined by spaces; its SHA256 is
ac10492555fe4f2ffb02d2466c326844db29dabde1c436ca016bf5c6976c285f.
The additional active build names are derived from cached wheel METADATA for hatch-vcs, hatchling,
packaging, pathspec, pluggy, setuptools, setuptools-scm, tomlkit, trove-classifiers and vcs-versioning.
Both permitted source projects declare only hatchling.build plus the VCS version hook.

The coordinator then approved UV_NO_BUILD=false only with that 61-name registry/build-dependency
guard, UV_OFFLINE=true and UV_PYTHON_DOWNLOADS=never. The only VCS in this lock is canonical
dev-lab 2c8711e9; lab-commons is this editable workspace. A second complete offline attempt
refused missing locked wheels (including cryptography, bokeh and ruff), again leaving the measured
environment unchanged. Conditional authorization permitted only UV_OFFLINE=false to download
locked registry wheels, retaining the 61-name guard, exact lock, GIT_TERMINAL_PROMPT=0 and
GCM_INTERACTIVE=Never. No raw credential verb, login or configuration change was performed.

The third full frozen attempt succeeded. Only the pure-Hatch editable lab-commons build was
logged; registry/native distributions were precompiled wheels. Fresh target-Python inspection
proved all 56 installed names exactly equal the lock's names and EVERY pinned version matches.
Dev-lab direct_url commit is 2c8711e9cc304ba45a50b9b106719c4eec964436 with requested_revision
integrate/main; lab-commons direct_url is editable at this worktree. No AI package exists in either
the lock or installed metadata. uv pip check reports all 56 compatible, and the lock hash is unchanged.
The door's measured transition was d6fe81117561008a to 2c9749cea179f592; the subsequent new-kit
verify logs below report their own environment key 66b87bd9363a8824.

The approved cache is checkout-local output/logs/26/11/06/canonical-dependency-adoption/uv-cache.
All temporary paths and MPLCONFIGDIR are inside this worktree. No test or hook ran concurrently
with its environment mutation; metadata checks completed before generation or runtime tests.

## Normal owned hook and guard generation

The coordinator explicitly approved the normal hook installation after an ownership audit.
Before installation, Git used D:/MingyangBao/lab-commons/.git/hooks: four declared stages were
owned and already installed, two were absent, none was foreign. The future worktree-local
hooks directory contained no files. The NEW public installer scopes linked worktrees to their
own git-dir/hooks rather than rewriting common hook files. Thus actual writes are under
D:/MingyangBao/lab-commons/.git/worktrees/lab-commons-report-holes/hooks, with a worktree-scoped
core.hooksPath. All six are re-measured installed/protected. The four common hashes remain
exactly unchanged; the two common absent files remain absent. No other worktree is upgraded,
cleaned or claimed protected by this operation.

| Stage | Old common SHA256 | New worktree-local SHA256 |
| --- | --- | --- |
| pre-commit | 991ca055ffb15dbea070a9856cc929e19c5e3bc7f8dac4927b2d2e5c7936c324 | fa086a96e54424c15d1c1e22818550ce48fbb516a401ab731f3452ee9e751a70 |
| commit-msg | e24495dea8f9511636a90d63b3adea51193a46e2e4f735b2861c36b2873c3467 | b0894c2c5ca4da6c3b65d3abed7b2dd816e7d55f260431ded36ccbc4d4a9e104 |
| post-commit | f4f23ac318fef678d982696cb80eb3bb2c70afba819c8f50fd2ce180d4b5df80 | fd644b89219bc4fbba703a9d69327829903cd04ef6b7e9d6326b72f886d50a04 |
| pre-push | bafd7354ba0877588ce193f5ec281f0c5c5efbfd1759663d0003505b0ed7a913 | 088d0f49ec3127554782584a8bce2b9a85c71294f990149d925c68adea64d297 |
| post-merge | absent | 51b4fe97a7b2e7867f025fa33223fdc236bb7d56aafc21a3a893542806428cad |
| post-checkout | absent | 9e08450c69dfe3f7b89b60daff0319ddb395743c4503f4bffda953b4d4b605a9 |

Repository extensions.worktreeConfig and the normal three publication defaults already had their
target values. Public hook_install also generated the local source rem configuration backlog=issues.
Public agent_guard --install updated the owned engine/wiring; the existing scripts/deny_rules.py
entry point rendered the declared rules. No rule, exemption or remedy was loosened.
Public ciworkflows render/check now covers ci.yml, merge-tick.yml and review-claims.yml.

## Finite phase-one protocol, not a permanent compatibility path

The PROJECT kit is now 2c8711e9, but the PR's actual trusted BASE is still ed24ab516a14, whose
lock names old dev-lab 2ef3d35b. Using PR HEAD for the secret-bearing door remains forbidden.
The old installer-only bridge and its stale old-kit tests are retired, not kept as aliases.
The current sole declarative phase-one layer is mechanically derived from owner data:

- method[0]: canonical new 2c8711e9 frozen installer;
- method[1]: old trusted 2ef3d35b auth step, unchanged;
- review: only old trusted 2ef3d35b tests/review step with its --secret-env protocol, unchanged.

The new canonical forge-tokens step is EXACTLY equal to old review[0], so it stays canonical
and is not duplicated. All other new canonical workflows, guards, permissions and values remain
unchanged. No secret value, credential, review bar or success state is edited.

Owner source: dev-lab src/dev_lab/dev/ci_base.toml, old commit
2ef3d35b84d142324b4cddc2ecf27fe4f9f848db / Git blob
3b6227bc94b9bd160861ad1bd050c531d10b700b, and new commit
2c8711e9cc304ba45a50b9b106719c4eec964436 / Git blob
560d333332d1da2de7deb433d417fc91448935ea. Canonical-JSON SHA256 receipts:

- new base method: 744f3bbda5875a3ddd54b9e221246759aacf1fd8742b55a67806b3c2c94fe2dc
- new base review: d1f394c5fbc58fc8a377d3aa88d532c3db21a66e712bc69498e91efaf810f4a9
- phase-one method: 09a95c74cb1596f4e9c16ca189111f6b1e39c8206610841f5a1f2832053398cb
- phase-one review: 41bf69b27cae24d124e5b7c20be677e8f1263b2b6c6ae9e81cfa7386206181f4

The replacement portable tests pin those data owners and use the same assertion against planted
installer/auth/CLI/env drift and a PR-head door mutation. They compare complete generated workflows
against the new canonical family, with only the two explicit protocol-step substitutions allowed.
They require all three GitHub workflow files; no historical Git object is needed at test time.
There is no runtime version detector, fallback protocol, shim or dual key path.

PHASE TWO IS REQUIRED: once the official new kit is the actual trusted base and supports the new
review protocol, remove BOTH temporary lists AND the phase-one-only tests, then perform the full
canonical render/check immediately. Do not preserve the old auth protocol as a permanent option.
The coordinator owns that next integration checkpoint. Current remote auth remains external/pending;
local source tests do not prove a remote review, authenticate a key, or claim publication/adoption.

For merge-audit clarity, the deliberately retired file is
tests/test_ci_method_bootstrap_changes_only_the_frozen_installer.py, including
test_only_the_installer_run_is_derived_from_the_new_canonical_owner,
test_the_bootstrap_refuses_changes_beyond_its_one_installer (four planted cases), and
test_the_rendered_door_cli_keys_and_every_other_workflow_field_remain_unchanged.
Its six old-kit cases are replaced by seven stronger phase-one cases, not silently dropped.

## Actual local RED/GREEN and bounded regression

With the real new environment but before generation/protocol declaration, the focused selection
ran eleven runtime cases: eight failed and three passed in 5.22 seconds. Stale hooks and the
initial new-test line-format check were additional honest preflight failures. Log
sha256:cb3f49f9981e6c2c7d4e9adcda034a91afd2c48d4f95369ead29d22e9b111f1f@223.
After normal public generation and the explicit layer, the same eleven passed in 5.07 seconds,
log sha256:203d66f467ae8ed0d681ed5a91bc65f6b793e567e1a750dc3f678782862c33c6@17.

The broader protected non-export selection replaces the old six bootstrap cases with the new
seven, includes the two named guard checks, and retains the nine EXACT export exclusions from
non-export-viz-regression-is-not-full-export-coverage. Four workers executed 122 cases:
all 122 passed in 16.29 seconds, tree 5864ed1613d91ea7, env 66b87bd9363a8824, log
sha256:a7f57b6e9534b2de20fc923a808d7de61e4a1f52595c2e7961a92c32a1597b07@18.
This is NOT full export coverage. No test body, skip, waiver or vague -k filter was changed.
No PNG/HTML export, GUI, CAD, mesher, native build or solver runs were authorized or performed.
