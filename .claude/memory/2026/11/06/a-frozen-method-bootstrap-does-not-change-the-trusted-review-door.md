---
title: A frozen method bootstrap does not change the trusted review door
date: 2026-11-06
status: active
---

## Decision and evidence

The migration coordinator explicitly approved this temporary bootstrap on PR92, not adoption of
the unrelated red PR91. PR92's review job 127687582520 failed before reviewing: the old installer
combined locked ai-lab with a floating lab-commons, producing ImportError for API_KEY_TASKS. Its
three test jobs passed. PR91 independently failed because its new workflow passed --keys-env to
the old trusted-base door, which only accepts --secret-env; its two base test failures also expose
unrendered guard assets. No evidence establishes a missing secret or successful review execution.

Use the existing public ciworkflows.BASE_PATH data and consumer _steps layering. Mechanically
derive the entire old method list and replace ONLY method[0].run with the canonical frozen method
installer. Every old step name, env and later auth step remains unchanged. The trusted DOOR_SHA,
review CLI, credentials, protections, review bar, dependency lock and all unrelated workflow fields
must remain unchanged. Never select the PR head as the trusted review door. No hand-written workflow,
production framework, compatibility alias, credential operation or source push is authorized here.

## Exact owner provenance

The source is dev-lab src/dev_lab/dev/ci_base.toml, owned canonically by dev-lab:

- old commit: 2ef3d35b84d142324b4cddc2ecf27fe4f9f848db
- old Git blob: 3b6227bc94b9bd160861ad1bd050c531d10b700b
- frozen-installer commit: 2c8711e9cc304ba45a50b9b106719c4eec964436
- frozen-installer Git blob: 560d333332d1da2de7deb433d417fc91448935ea
- original method canonical-JSON SHA256: cd0d935b6de0617cd6fa06e91d89d7cd5c843f8b56dba2e04239861fbce85ba4
- derived method canonical-JSON SHA256: 09a95c74cb1596f4e9c16ca189111f6b1e39c8206610841f5a1f2832053398cb
- replacement run UTF-8 SHA256: 71e30b863c80a7e274f31ad18b8873a46076a794f45276ebf34dded066fe6f9f

Canonical JSON here means sorted keys, separators comma/colon, UTF-8, no whitespace. The temporary
consumer TOML is a generated materialization, not a second script authority. The locked old public
renderer generates and checks the workflow. Portable pure-source controls pin the owner hashes,
refuse planted changes to run/name/env/auth and compare every rendered field to the original except
the single method-installer run. No historical Git object is required by those runtime tests.

## Removal condition

This is NOT permanent compatibility. Once a separately validated official kit adoption has become
the trusted BASE and supports the new public review CLI/key protocol, delete this entire temporary
_steps override and its bootstrap-only tests, then perform the full canonical workflow render/check.
Deleting it immediately after PR92 alone, while its lock/base still uses old 2ef, would restore the
broken floating installer. The coordinator owns that explicit later removal; no green forge review,
kit adoption, recovered credential or secret availability is claimed by local source tests.

## Local verification

Six new pure controls first failed against the absent override, log
sha256:bf2ea1af87a74bb0bbae88fe9461ab18922ca5aaf99a08c42960638a4674e5b5@128.
The initial implementation run exposed an extra TOML multiline trailing newline and a test calling
the newer placed API instead of old public workflows; both were fixed without repinning any hash.
The old locked renderer's render and check now pass. Own-prefix official verify selected the two
CI/config files and six viz files: all 100 tests passed in 12.98 seconds, log
sha256:9a61e49cb56bc75f9905c38db1b0ce7f864f5990b6399f311d66de915bcd2e03@25,
tree e705257de8ddbb30. The rendered workflow diff changes exactly one existing run field. uv.lock
remains SHA256 8381a5b62c7b44804a6d72eade02245af2bd1f99ba4d6abf7841fa7cbe77c9ee.
No forge result is inferred from these local source/vector checks. The coordinator owns publication.
