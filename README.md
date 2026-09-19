**Language:** English | [简体中文](README_zh.md)

# system — agent-linux (AirymaxOS) System

[![Version](https://img.shields.io/badge/version-0.1.1-5a6b7e)](https://atomgit.com/openairymax/system)
[![License](https://img.shields.io/badge/license-AGPL--3.0+Apache--2.0-4a90d9)](LICENSE)

> System subsystem of [agent-linux (AirymaxOS)](https://atomgit.com/openairymax/agent-linux) — the AI Agent Operating System.
> One of the 8 leaf repositories aggregated by the [agent-linux](https://atomgit.com/openairymax/agent-linux) management repo.
> Reuses and extends the Airymax `commons` module for OS-level packaging and configuration.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

---

## Positioning

The **system** leaf repository is the system layer subsystem of agent-linux
(AirymaxOS). It provides system initialization, RPM packaging, the dnf package
manager, system configuration, the shell environment, and the DevStation developer
experience — the user-facing surface that packages, configures and brings up every
other agent-linux subsystem.

## Core Responsibilities

- **System initialization** — bootstrapping and first-boot configuration of an agent-linux image.
- **RPM packaging** for reproducible, signed, dependency-aware OS components.
- **dnf package manager** aligned with Euler-standard repository and update models.
- **System configuration** covering networking, services, users and locale.
- **Shell environment** providing a consistent developer and operator experience.
- **DevStation** — integrated developer workstation image for agent-linux contributors.

## Relationship with Airymax `commons`

The system leaf repo reuses and extends the `commons` module from the Airymax
runtime platform. The shared configuration, packaging helpers and shell utilities
are shared between the user-space runtime (`agentrt`) and the OS-level system
layer, ensuring architectural homology with no adaptation layer.

## Document & File List

```
system/
├── README.md           # This file (English)
├── README_zh.md        # Chinese translation
├── LICENSE             # AGPL-3.0 + Apache-2.0 dual license
├── NOTICE              # Copyright, trademark and third-party notices
├── .gitignore
└── .github/
    └── README.md       # GitHub automation for this leaf repo
```

Design documents and reference distribution specifications are maintained in the
`docs/docs-linux/` directory of the umbrella documentation repository.

## CI Status

System changes are governed by management-repository workflows (each ≤ 2 jobs):

| Workflow | Jobs | Applies to system via |
|----------|------|------------------------|
| `mgmt-orchestrator.yml` | `file-integrity` + `orchestrate-leaf-ci` | Verifies the `system/` submodule dir; aggregates this repo's CI status |
| `release.yml` | `build-and-sign` (kernel RPM via `make binrpm-pkg`; SBOM scan of `system/`; GPG signing) + `publish-release` (publish dnf repo via `createrepo` + rsync) | Release tag |
| `nightly.yml` | `nightly-test-suite` + `nightly-revert-or-budget` | Nightly cron |
| `ssot-validate.yml` | `ssot-syntax-and-rules` + `ssot-cross-ref` | When docs reference system/packaging rules |

Language-level CI (RPM spec lint, shell, config validation) is delegated to this
leaf repository's own `.github/workflows/`.

## Development Guide

- **Branch**: `feature/official-hubs-01` (the management repo stays on `main`).
- **DCO**: every commit must be `Signed-off-by` (`git commit -s`).
- **Commit prefix**: `system:`.
- **Code style**: shell — `shellcheck`; RPM spec — `rpmlint`; Python — PEP 8 (`ruff`).
- **Release signing**: RPM artifacts are signed with GPG (CI secrets
  `GPG_PRIVATE_KEY` / `GPG_PASSPHRASE`); never log these.
- **Function prefix**: `airy_*` for any kernel-adjacent helpers.

## Upstream & Downstream

- **Upstream** — `kernel` (kernel image and modules packaged here); `services` (service set configured and shipped); Airymax `commons`.
- **Downstream** — end users (install agent-linux via the RPM/dnf surface); DevStation users (developers booting the DevStation image); `cloudnative` (consumes the packaging surface for the hyper-node OS image).

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`).
See [LICENSE](LICENSE) for the full text.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
