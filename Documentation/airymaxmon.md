# airymaxmon — 实时监控守护进程

> **子仓**：`system/`
> **组件**：`monitoring/`
> **可编译入口**：`monitoring/airymaxmon.c` → `airymaxmon`
> **[SC] 依赖**：`kernel/include/uapi/linux/airymax/error.h`、`lsm_types.h`
> **设计文档**：`docs/AirymaxOS/10-architecture/07-directory-structure.md` §4.7

Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.

License: GPL-2.0-only

---

## 1. 设计目标

airymaxmon 是 AirymaxOS 的实时监控守护进程，提供：

- 每秒读取 `/proc/airy/status` 并解析关键字段
- 健康度评估（HEALTHY / DEGRADED / CRITICAL）
- 故障码参考提示（基于 [SC] `error.h`）
- SIGINT / SIGTERM 优雅退出
- Agent 数量上限参考（基于 [SC] `AIRY_CAP_MAX_AGENTS`）

## 2. 命令行接口

```
用法: airymaxmon [选项]

AirymaxOS 实时监控守护进程。

每秒读取 /proc/airy/status 并打印结构化监控信息。
按 Ctrl+C 或发送 SIGTERM 优雅退出。

选项:
  -h, --help      显示帮助
  -v, --version   显示版本
```

## 3. 输出示例

```
airymaxmon v0.1.1 启动 — 间隔=1s 状态文件=/proc/airy/status
AIRY_CAP_MAX_AGENTS=1024

[1784562248.123456789] iter=1 health=HEALTHY agents=3 faults=0
  heartbeat=1784562248 budget_ns=10000000
[1784562249.123456789] iter=2 health=DEGRADED agents=3 faults=5
  heartbeat=1784562249 budget_ns=10000000
  ↳ 故障参考: CAP_FORGED=0x1001 CAP_LEAK=0x1002 RING_CORRUPT=0x1003
  TIMEOUT=0x1004
```

如果 AirymaxOS 内核模块未加载（`/proc/airy/status` 不存在），每秒会
输出错误信息但不退出：

```
[1784562248.123456789] airymaxmon: 读取 /proc/airy/status 失败:
  No such file or directory
```

## 4. 健康度评估策略

| 条件 | 健康度 |
|------|--------|
| `agent_count < 0`（无法读取） | UNKNOWN |
| `fault_count == 0` | HEALTHY |
| `0 < fault_count < AIRY_CAP_MAX_AGENTS/4`（< 256） | DEGRADED |
| `fault_count ≥ 256` | CRITICAL |

`AIRY_CAP_MAX_AGENTS` 由 [SC] `lsm_types.h` 定义为 1024，确保
airymaxmon 与内核 LSM 使用同一上限。

## 5. 信号处理

| 信号 | 行为 |
|------|------|
| `SIGINT` (Ctrl+C) | 设置 `g_stop_flag`，主循环检测后退出 |
| `SIGTERM` | 同上 |
| `SIGPIPE` | 忽略（写入已关闭管道不中断） |

信号处理函数仅设置 `volatile sig_atomic_t` 标志，符合异步信号安全要求。

## 6. 构建与安装

### 6.1 构建

```bash
cd system
mkdir build && cd build
cmake ..
make
```

产物：`monitoring/airymaxmon`

### 6.2 安装

```bash
sudo make install
# 默认安装到 /usr/local/bin/airymaxmon
```

### 6.3 运行

```bash
# 前台运行
airymaxmon

# 后台运行（systemd unit 由 1.0.1 提供）
airymaxmon > /var/log/airy/airymaxmon.log 2>&1 &
```

## 7. IRON-9 同源关系

| 层级 | 实体 | 说明 |
|------|------|------|
| `[SC]` | `AIRY_FAULT_*` 故障码 | 与 agentrt 共享 |
| `[SC]` | `AIRY_CAP_MAX_AGENTS` | 与 LSM 共享 |
| `[SC]` | `struct airy_task_sec` | Agent 安全上下文（参考） |
| `[IND]` | airymaxmon 实现本身 | agentrt-linux 专属 |

agentrt 用户态有对应的 `airyobservability` 工具（ecosystem/airyobservability），
两者通过 [SC] 共享故障码与 Agent 上限，但实现完全独立。

## 8. 后续路线

| 版本 | 计划 |
|------|------|
| 0.1.1 | 当前实现：每秒读取 + 健康度评估 + 信号退出 |
| 1.0.1 | 增加 Prometheus 指标导出（HTTP :9100/metrics） |
| 1.0.1 | 增加 OpenTelemetry 追踪上报 |
| 1.0.1 | 增加 systemd unit 与配置文件 |
| 1.0.1 | 增加 JSON / structured logging 输出模式 |

## 9. 变更历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 0.1.1 | 2026-07-20 | 初始最小可编译实现：每秒读取 + 信号处理 + 健康度评估 |
