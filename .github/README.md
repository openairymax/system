# `.github/` — system Leaf Repository Automation

> GitHub automation directory for the **system** leaf repository of
> [agentrt-linux (AirymaxOS)](https://atomgit.com/openairymax/agentrt-linux).

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## Positioning

This directory hosts system-specific GitHub automation. Management-level
orchestration (governance integrity, release RPM build + GPG signing + dnf repo
publish, nightly soak, SSoT validation) runs in the management repository; this
leaf repository hosts language-level CI for RPM spec lint, shell, and
configuration validation.

## Directory Contents

```
system/.github/
└── README.md    # This file
```

## Applicable Management-Repository Workflows

| Workflow | Jobs | Relevance to system |
|----------|------|----------------------|
| `mgmt-orchestrator.yml` | `file-integrity` + `orchestrate-leaf-ci` | Verifies the `system/` submodule dir exists; aggregates this repo's CI status |
| `release.yml` | `build-and-sign` + `publish-release` | `make binrpm-pkg` builds the kernel RPM; `syft system/` SBOM; GPG-signed artifacts; dnf repo published via `createrepo` + rsync |
| `nightly.yml` | `nightly-test-suite` + `nightly-revert-or-budget` | Soak and chaos validate boot/config stability |
| `ssot-validate.yml` | `ssot-syntax-and-rules` + `ssot-cross-ref` | Validates rule IDs referenced in system/packaging documentation |

## Development Guide

- Add leaf-local workflows for `rpmlint`, `shellcheck`, and config-schema
  validation; keep each workflow ≤ 2 jobs.
- Release RPMs are signed with GPG (CI secrets `GPG_PRIVATE_KEY` /
  `GPG_PASSPHRASE`); the dnf repo is published to the host in secret
  `DNF_REPO_HOST`. Never log these secrets.
- Kernel-adjacent helpers use the `airy_*` prefix.

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`).
See the repository root [LICENSE](../LICENSE) and [NOTICE](../NOTICE).

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
