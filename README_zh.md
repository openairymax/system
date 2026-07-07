**语言:** [English](README.md) | 简体中文

# agentrt-liunx 系统（AirymaxOS System）

[![Version](https://img.shields.io/badge/version-0.1.1-5a6b7e)](https://atomgit.com/openairymax/system)
[![License](https://img.shields.io/badge/license-AGPL--3.0+Apache--2.0-4a90d9)](LICENSE)

> [agentrt-liunx（AirymaxOS）](https://atomgit.com/openairymax/agentrt-linux)（智能体操作系统）的系统层子系统。
> 由 [agentrt-linux](https://atomgit.com/openairymax/agentrt-linux) 管理仓聚合的叶子仓之一。
> 复用并扩展 Airymax `commons` 模块以提供 OS 级打包与配置。

---

## 概述

**agentrt-liunx 系统（AirymaxOS System）**（`airymaxos-system`）是 agentrt-liunx（AirymaxOS）（智能体操作系统）的系统层子系统。它提供 RPM 打包、dnf 包管理器、系统配置、shell 环境与 DevStation 开发者体验——这是打包并配置其他所有 agentrt-liunx 子系统的用户面。

在 agentrt-liunx 0.1.1 中，本仓库为**文档体系完成**，包含设计文档、参考发行版规范及架构草案。实际的内核与 OS 开发在 1.0.1 版本进行。

### 核心技术

- **RPM 打包** 实现可复现、签名、依赖感知的 OS 组件
- **dnf 包管理器** 对齐 Euler 标准仓库与更新模型
- **系统配置** 涵盖网络、服务、用户与区域设置
- **Shell 环境** 提供一致的开发者与运维者体验
- **DevStation** — 面向 agentrt-liunx 贡献者的集成开发工作站镜像

### 与 Airymax commons 的关系

agentrt-liunx 系统（AirymaxOS System）复用并扩展了 Airymax 运行时平台的 `commons` 模块。共享配置、打包辅助与 shell 工具在用户态运行时（agentrt）与 OS 级系统层（agentrt-liunx）之间共享，确保架构同源、无适配层。

## 仓库结构（0.1.1（文档体系完成））

```
system/
├── README.md           # 本文件（英文）
├── README_zh.md        # 中文翻译
├── LICENSE             # AGPL-3.0 + Apache-2.0 双许可证
├── NOTICE              # 版权、商标与第三方声明
└── .gitignore
```

设计文档与 参考发行版规范维护在伞仓的 `docs/AirymaxAgentOS/` 目录。

## 上下游依赖

### 上游

- **agentrt-liunx 内核（AirymaxOS Kernel）** — 提供系统层打包的内核镜像与模块
- **agentrt-liunx 服务态（AirymaxOS Services）** — 提供系统层配置与交付的服务集合
- **Airymax commons** — 提供被复用并扩展的共享配置与打包辅助

### 下游

- **End users** — 通过本子系统产出的 RPM/dnf 面安装 agentrt-liunx 的终端用户
- **DevStation users** — 启动 DevStation 镜像参与 agentrt-liunx 贡献的开发者

## 分支策略

本叶子仓在 **`feature/official-hubs-01`** 分支上开发。聚合管理仓 `agentrt-linux` 保持在 `main` 分支。

## 许可证

采用 **AGPL v3 + Apache 2.0** 双许可证（SPDX：`AGPL-3.0-or-later OR Apache-2.0`）。完整文本见 [LICENSE](LICENSE)。

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
