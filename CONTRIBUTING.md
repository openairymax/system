# 贡献指南 - system 子仓（系统监控）

> **治理依据**：[`docs/docs-linux/50-engineering-standards/07-maintainers-and-governance.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/07-maintainers-and-governance.md)
> **开发流程**：[`docs/docs-linux/50-engineering-standards/05-development-process.md`](https://github.com/openairymax/docs/blob/main/AirymaxOS/50-engineering-standards/05-development-process.md)

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

License: GPL-2.0-only

---

## 1. 子仓职责

system 子仓负责 AirymaxOS 的系统可观测性，包括：

- **airymaxmon**（`monitoring/`）：实时监控守护进程（[IND] 独立实现）
- **metrics**（`metrics/`）：Prometheus 指标导出
- **tracing**（`tracing/`）：分布式追踪（OpenTelemetry）
- **logging**（`logging/`）：结构化日志
- **health**（`health/`）：健康检查与心跳
- **perf**（`perf/`）：性能分析（ftrace / perf）

### IRON-9 主层

- `[IND]`：全部组件均为 agent-linux 专属实现
- `[SC]`：与 agentrt 共享 `error.h`、`lsm_types.h` 等契约

## 2. 开发环境要求

- **C 标准**：GNU C11（用户态 daemon）
- **编译器**：gcc ≥ 11 或 clang ≥ 14
- **构建系统**：CMake ≥ 3.20
- **内核头**：通过 `-I../kernel/include` 引用 [SC] 头文件

### [SC] 头文件引用约束（OS-IRON-014）

- [SC] 头文件**唯一物理宿主**：`../kernel/include/uapi/linux/airymax/`
- 本子仓通过 `-I` 引用，**禁止物理副本**
- CMakeLists.txt 配置：
  ```cmake
  include_directories(${CMAKE_SOURCE_DIR}/../kernel/include)
  include_directories(${CMAKE_SOURCE_DIR}/../kernel/include/uapi/linux)
  ```

## 3. 构建命令

```bash
mkdir build && cd build
cmake ..
make
```

构建产物：
- `monitoring/airymaxmon` — 实时监控守护进程

## 4. 代码规范

### 4.1 C 代码风格

- **OS-STD-FMT-001**：Tab-8 缩进（由 `.clang-format` 强制）
- **OS-STD-FMT-002**：80 列硬限制
- **GPL-2.0-only**：所有 `.c` / `.h` 文件必须包含 `SPDX-License-Identifier: GPL-2.0-only`
- **版权头**：`Copyright (c) 2025-2026 SPHARX Ltd.`

### 4.2 [SC] 故障码常量（error.h）

| 常量 | 值 | 含义 |
|------|------|------|
| `AIRY_EOK` | 0 | 成功 |
| `AIRY_EINVAL` | 负值 | 无效参数 |
| `AIRY_FAULT_CAP_FORGED` | 0x1001 | Capability 伪造 |
| `AIRY_FAULT_TIMEOUT` | 0x1004 | 超时 |

### 4.3 [SC] Agent 安全上下文（lsm_types.h）

| 字段 | 含义 |
|------|------|
| `struct airy_task_sec.agent_id` | Agent 唯一 ID |
| `struct airy_task_sec.agent_state` | Agent 状态 |
| `struct airy_task_sec.fault_count` | 累计故障数 |
| `struct airy_task_sec.sched_budget_ns` | 调度预算（纳秒） |
| `struct airy_task_sec.last_heartbeat` | 最后心跳时间戳 |
| `AIRY_CAP_MAX_AGENTS` | 最大 Agent 数（1024） |

## 5. 提交规范

### 5.1 DCO 签名

```bash
git commit -s
```

### 5.2 提交信息格式

```
system: 简短描述（≤72 字符）

详细说明 what 和 why，72 字符换行。

Signed-off-by: Your Name <your.email@example.com>
```

### 5.3 子系统前缀

| 前缀 | 范围 |
|------|------|
| `system:` | system/ 子仓整体 |
| `system: mon:` | airymaxmon 监控 |
| `system: metrics:` | Prometheus 指标 |
| `system: tracing:` | 分布式追踪 |

## 6. 分支策略

- **开发分支**：`feature/official-hubs-01`
- PR 目标分支：`feature/official-hubs-01`

## 7. 测试要求

- **OS-STD-TEST-***：所有变更必须包含或更新测试
- 单元测试：随代码放在各组件目录
- 集成测试：在 `tests-linux/` 子仓

## 8. 审查流程

1. 向 `feature/official-hubs-01` 提交 PR
2. CI 运行：SSoT 校验 + CMake 构建 + 测试
3. 至少一名维护者审批
4. Squash-merge

## 9. 报告问题

- **Bug**：在 [system issues](https://github.com/openairymax/system/issues) 提交
- **设计讨论**：使用管理仓的 GitHub Discussions
