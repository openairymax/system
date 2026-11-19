// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// airy-probe status --json 的消费侧类型
//
// 探针的载荷回答的是「这台机器的取证面现在是什么状态」，因此每个字段都
// 要能原样上屏：事件逐条给出可读性与原因，通道逐条给出权限位与本次写入
// 结果，静态计数区分「读不到」与「为零」——用 0 冒充缺失会让操作者把
// 「没有控制面」误判成「尚未触发」。
//
// 三个原因集合不能合并：事件多出 malformed（节点在但 id 不成形），
// policy 表只有 missing / unreadable，采样窗口回带任意 errno 名。按生产
// 侧的取值分别声明，读不懂的 token 一律拒绝，避免把新原因显示成旧原因。
//
// JSON 没有 schema 字段，版本口径由 probe/version 两个字段自陈。
// 实测样张见 tests/golden/probe-status.json。
// ===========================================================================

//! `airy-probe status --json` 的消费侧类型。

use std::collections::BTreeMap;

use intent::Refusal;
use serde::Deserialize;

use crate::decode;

/// 一次探测的完整结论。
#[derive(Debug, Deserialize)]
pub struct Report {
    /// 探针名；与二进制同名。
    pub probe: String,
    /// 产出该载荷的版本。
    pub version: String,
    /// 本次运行的模式。
    pub mode: Mode,
    /// 取证面结论。
    pub state: State,
    /// 结论对应的处置建议。
    pub hint: String,
    /// 可读事件数。
    pub readable: usize,
    /// 事件总数。
    pub total: usize,
    /// 十个追踪事件的可读性。
    pub events: Vec<Event>,
    /// 不可读事件名，顺序与 `events` 一致。
    pub missing: Vec<String>,
    /// 两条注入通道的权限面。
    pub channels: Vec<Channel>,
    /// 写入试探结果；status 模式下为空。
    pub attempts: Vec<Attempt>,
    /// 三处静态计数。
    pub stats: Stats,
    /// 采样窗口；status 模式下为空。
    pub samples: Vec<Sample>,
    /// 多窗口间是否观测到计数严格递增。
    pub witnessed: bool,
}

/// 探针的运行模式。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// 只读探测。
    Status,
    /// 开窗采样。
    Sample,
    /// 重复采样并核对递增。
    Verify,
}

/// 取证面结论。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum State {
    /// 十点追踪与三处计数均可用。
    Ok,
    /// 部分可用；缺项见 `missing` 与 `stats`。
    Partial,
    /// 本机没有 airy 取证面。
    Blocked,
}

/// 控制面的可读性；事件与 policy 表共用这两个 token。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Avail {
    /// 可读。
    Readable,
    /// 不可读。
    Unavailable,
}

/// 事件不可读的原因。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Why {
    /// 节点不存在。
    Missing,
    /// 权限不足。
    Denied,
    /// 目录存在但 id 不成形。
    Malformed,
}

/// policy 表不可读的原因；比事件少 denied 与 malformed。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Gap {
    /// 节点不存在。
    Missing,
    /// 节点存在但读不出来。
    Unreadable,
}

/// 一个追踪事件的可读性与启用位。
#[derive(Debug, Deserialize)]
pub struct Event {
    /// tracepoint 名，与内核 TRACE_EVENT 逐字对应。
    pub name: String,
    /// 事件号；不可读时为 null。
    pub id: Option<u32>,
    /// 当前启用位；不可读时为 null。
    pub enabled: Option<bool>,
    /// 可读性。
    pub state: Avail,
    /// 不可读的原因；可读时为 null。
    pub reason: Option<Why>,
}

/// 一条注入通道的权限面。
#[derive(Debug, Deserialize)]
pub struct Channel {
    /// 通道名。
    pub name: String,
    /// 节点路径。
    pub path: String,
    /// 八进制权限位；节点缺失时为 null。
    pub mode: Option<String>,
    /// 节点是否存在。
    pub present: bool,
    /// 权限位是否含写位。
    pub writable: bool,
    /// 缺失原因；存在时为 null。
    pub reason: Option<Why>,
}

/// 一次写入试探的结果。
#[derive(Debug, Deserialize)]
pub struct Attempt {
    /// 通道名。
    pub channel: String,
    /// 实际写入的节点路径。
    pub path: String,
    /// 内核给出的 errno 名；成功时为 null。
    pub error: Option<String>,
    /// 写入是否被受理。
    pub delivered: bool,
}

/// 三处静态计数的快照。
#[derive(Debug, Deserialize)]
pub struct Stats {
    /// 能力检查命中次数；节点缺失时为 null。
    pub cap_used: Option<u64>,
    /// 策略分发总数；节点缺失时为 null。
    pub stc_total: Option<u64>,
    /// 各策略的分发计数。
    pub stc_policies: BTreeMap<String, u32>,
    /// policy 表的可读性。
    pub policy: Avail,
    /// policy 表不可读的原因；可读时为 null。
    pub policy_reason: Option<Gap>,
    /// 每张决策表的求值分布。
    pub tables: Vec<Table>,
}

/// policy 表的一行，列序与内核打印一致。
#[derive(Debug, Deserialize)]
pub struct Table {
    /// 表名。
    pub name: String,
    /// 挂载点。
    pub hook: String,
    /// 行数。
    pub rows: u64,
    /// 求值次数。
    pub evals: u64,
    /// 放行次数。
    pub allow: u64,
    /// 拒绝次数。
    pub deny: u64,
    /// 审计次数。
    pub audit: u64,
    /// 告警次数。
    pub complain: u64,
    /// 故障次数。
    pub fault: u64,
}

/// 一个采样窗口。
#[derive(Debug, Deserialize)]
pub struct Sample {
    /// 窗口时长，秒。
    pub window: f64,
    /// 窗口结论。
    pub state: SampleState,
    /// 开窗失败的原因；可为 errno 名。
    pub reason: Option<String>,
    /// 窗口内事件总数。
    pub hit: u32,
    /// 各事件计数。
    pub counts: BTreeMap<String, u32>,
}

/// 采样窗口结论。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum SampleState {
    /// 窗口内观测到事件。
    Ok,
    /// 通道可读但窗口内无事件。
    Idle,
    /// 开窗或读缓冲失败。
    Unavailable,
}

impl Report {
    /// 解析 status 载荷；字段与枚举不受支持即拒绝。
    pub fn parse(bytes: &[u8]) -> Result<Self, Refusal> {
        decode(bytes)
    }
}
