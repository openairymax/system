**Language:** English | [简体中文](README_zh.md)

# agentrt-liunx System (AirymaxOS System)

[![Version](https://img.shields.io/badge/version-0.1.1-5a6b7e)](https://atomgit.com/openairymax/system)
[![License](https://img.shields.io/badge/license-AGPL--3.0+Apache--2.0-4a90d9)](LICENSE)

> System subsystem of [agentrt-liunx（AirymaxOS）](https://atomgit.com/openairymax/agentrt-linux) — the AI Agent Operating System.
> One of the leaf repositories aggregated by the [agentrt-linux](https://atomgit.com/openairymax/agentrt-linux) management repo.
> Reuses and extends the Airymax `commons` module for OS-level packaging and configuration.

---

## Overview

The **agentrt-liunx System (AirymaxOS System)** (`airymaxos-system`) is the system layer subsystem of agentrt-liunx（AirymaxOS）, the AI Agent Operating System. It provides RPM packaging, the dnf package manager, system configuration, the shell environment, and the DevStation developer experience — the user-facing surface that packages and configures every other agentrt-liunx subsystem.

In agentrt-liunx 0.1.1, this repository is **documentation complete** (文档体系完成) containing design documents, reference distribution specifications, and architectural drafts. Actual kernel and OS development takes place in version 1.0.1.

### Core Technologies

- **RPM packaging** for reproducible, signed, dependency-aware OS components
- **dnf package manager** aligned with Euler standard repository and update models
- **System configuration** covering networking, services, users and locale
- **Shell environment** providing a consistent developer and operator experience
- **DevStation** — integrated developer workstation image for agentrt-liunx contributors

### Relationship with Airymax commons

The agentrt-liunx System (AirymaxOS System) reuses and extends the `commons` module from the Airymax runtime platform. The shared configuration, packaging helpers and shell utilities are shared between the user-space runtime (agentrt) and the OS-level system layer (agentrt-liunx), ensuring architectural homology with no adaptation layer.

## Repository Structure (0.1.1 Documentation Complete)

```
system/
├── README.md           # This file (English)
├── README_zh.md        # Chinese translation
├── LICENSE             # AGPL-3.0 + Apache-2.0 dual license
├── NOTICE              # Copyright, trademark and third-party notices
└── .gitignore
```

Design documents and reference distribution specifications are maintained in the `docs/AirymaxAgentOS/` directory of the umbrella repository.

## Upstream & Downstream Dependencies

### Upstream

- **agentrt-liunx Kernel (AirymaxOS Kernel)** — provides the kernel image and modules that the system layer packages
- **agentrt-liunx Services (AirymaxOS Services)** — provides the service set that the system layer configures and ships
- **Airymax commons** — provides the shared configuration and packaging helpers that are reused and extended

### Downstream

- **End users** — install agentrt-liunx via the RPM/dnf surface produced by this subsystem
- **DevStation users** — developers booting the DevStation image for agentrt-liunx contribution

## Branch Strategy

This leaf repository is developed on **`feature/official-hubs-01`**. The aggregating `agentrt-linux` management repo stays on `main`.

## License

Dual-licensed under **AGPL v3 + Apache 2.0** (SPDX: `AGPL-3.0-or-later OR Apache-2.0`). See [LICENSE](LICENSE) for the full text.

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
