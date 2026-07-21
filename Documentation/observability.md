# 可观测性总体设计

> **子仓**：`system/`
> **范围**：monitoring + metrics + tracing + logging + health + perf
> **设计文档**：`docs/AirymaxOS/10-architecture/07-directory-structure.md` §4.7

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

License: GPL-2.0-only

---

## 1. 设计目标

system 子仓提供 AirymaxOS 的端到端可观测性，覆盖三大支柱：

| 支柱 | 组件 | 数据源 | 输出 |
|------|------|--------|------|
| Metrics | `metrics/` | `/proc/airy/status`、内核计数器 | Prometheus HTTP :9100 |
| Tracing | `tracing/` | tracepoint、kfifo 事件 | OpenTelemetry OTLP |
| Logging | `logging/` | `printk`、`dmesg`、daemon stderr | 结构化 JSON 日志 |

外加：

| 辅助 | 组件 | 用途 |
|------|------|------|
| Monitoring | `monitoring/` (airymaxmon) | 实时控制台监控 |
| Health | `health/` | 心跳与就绪检查 |
| Perf | `perf/` | ftrace / perf 性能分析 |
| Sysdump | `sysdump/` | 故障现场快照 |

## 2. 数据流

```
┌─────────────────────────────────────────────────────────────────┐
│ AirymaxOS 内核（kernel/）                                        │
│   - security/airy/airy_lsm.c     → /proc/airy/status            │
│   - cognition/coreloopthree/      → kfifo 事件                  │
│   - kernel/superv/                → die_notifier                │
└──────────────────────┬──────────────────────────────────────────┘
                       │
            ┌──────────┴──────────┐
            │                     │
            ▼                     ▼
┌────────────────────┐   ┌────────────────────┐
│ /proc/airy/status  │   │ tracepoint / kfifo │
└─────────┬──────────┘   └──────────┬─────────┘
          │                         │
          ▼                         ▼
┌────────────────────┐   ┌────────────────────┐
│ airymaxmon         │   │ tracing/           │
│ (monitoring/)      │   │ (OpenTelemetry)    │
└─────────┬──────────┘   └──────────┬─────────┘
          │                         │
          ▼                         ▼
┌────────────────────┐   ┌────────────────────┐
│ stdout / stderr    │   │ OTLP collector     │
└────────────────────┘   └────────────────────┘
          │
          ▼
┌────────────────────┐
│ metrics/           │
│ (Prometheus)       │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ HTTP :9100/metrics │
└────────────────────┘
```

## 3. 关键指标定义

| 指标 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `airy_agents_total` | gauge | `/proc/airy/status` | 当前 Agent 总数 |
| `airy_agents_active` | gauge | 同上 | 活跃 Agent 数 |
| `airy_faults_total` | counter | 同上 | 累计故障数 |
| `airy_ipc_messages_total` | counter | 同上 | 累计 IPC 消息数 |
| `airy_cog_loops_total` | counter | cognition kthread | CoreLoopThree 循环数 |
| `airy_mr_l1_alloc_total` | counter | memory kmem_cache | L1 hot 记录分配数 |

## 4. [SC] 依赖

可观测性组件依赖以下 [SC] 头文件：

| [SC] 头文件 | 用途 |
|-------------|------|
| `error.h` | 故障码常量（AIRY_FAULT_*） |
| `lsm_types.h` | Agent 上限（AIRY_CAP_MAX_AGENTS） |
| `ipc.h` | IPC 消息统计（opcode / magic） |
| `memory_types.h` | 内存层级（AIRY_MEM_*） |
| `cognition_types.h` | 认知阶段（AIRY_COG_*） |

## 5. IRON-9 同源关系

| 层级 | 实体 | 说明 |
|------|------|------|
| `[SC]` | 故障码 / Agent 上限 / IPC magic | 与 agentrt 共享 |
| `[IND]` | airymaxmon + metrics + tracing 实现 | agentrt-linux 专属 |

## 6. 当前实现状态

0.1.1 版本中可观测性仅 `airymaxmon` 可编译：

- ✅ airymaxmon：每秒读取 `/proc/airy/status` + 健康度评估
- ⏳ metrics：Prometheus 导出（1.0.1）
- ⏳ tracing：OpenTelemetry 上报（1.0.1）
- ⏳ logging：结构化 JSON 日志（1.0.1）
- ⏳ health：心跳检查（1.0.1）
- ⏳ perf：ftrace 集成（1.0.1）

## 7. 变更历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.1 | 2026-07-20 | 初始设计文档；airymaxmon 最小可编译实现 |
