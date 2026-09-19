# system 子仓文档索引

> **子仓定位**：AirymaxOS 系统可观测性 —— airymaxmon + metrics + tracing + logging + health + perf。

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

License: GPL-2.0-only

---

## 文档清单

### 已有文档

| 文档 | 说明 |
|------|------|
| [airymaxmon.md](airymaxmon.md) | airymaxmon 实时监控守护进程设计 |
| [observability.md](observability.md) | 可观测性总体设计 |

### 待编写文档（0.1.1 → 1.0.1）

| 文档 | 说明 | 计划版本 |
|------|------|---------|
| `metrics.md` | Prometheus 指标导出设计 | 1.0.1 |
| `tracing.md` | OpenTelemetry 分布式追踪设计 | 1.0.1 |
| `logging.md` | 结构化日志设计 | 1.0.1 |
| `health.md` | 健康检查与心跳设计 | 1.0.1 |
| `perf.md` | ftrace / perf 性能分析设计 | 1.0.1 |

## 相关文档

- [07-directory-structure.md](../../../docs/docs-linux/10-architecture/07-directory-structure.md) §4.7 — system 子仓完整目录结构
- [error.h](../../kernel/include/uapi/linux/airymax/error.h) — [SC] 故障码契约
- [lsm_types.h](../../kernel/include/uapi/linux/airymax/lsm_types.h) — [SC] Agent 安全上下文
- 顶层 [CONTRIBUTING.md](../CONTRIBUTING.md) — system 子仓贡献指南
