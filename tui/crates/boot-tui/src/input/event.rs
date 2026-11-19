// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// L1 输入事件的载荷与元数据
//
// 事件带来源、信任级别、单调时刻与追踪号；载荷保持各来源的原貌，
// 归一化只发生在组合层。字段语义以接入契约为准。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.1、§4.1、§10.2。
// ===========================================================================

use std::time::{Instant, SystemTime, UNIX_EPOCH};

pub use intent::{AudioRef, SourceKind, TraceId, Trust};
use intent::{CheckError, Refusal};
use serde_json::Value;

/// 事件时钟：单调时刻与追踪号都从这里取。
///
/// 追踪号由启动时刻与自增序号拼成 16 字节：同一引导内唯一，重启后也不
/// 复用；它只用于关联，不承担不可预测性。多个来源共用一个实现，避免每
/// 个适配器各抄一份种子与序号逻辑。
pub(crate) struct Clock {
    base: Instant,
    seed: u64,
    seq: u64,
}

impl Clock {
    pub(crate) fn new() -> Self {
        let seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|since| since.as_nanos() as u64)
            .unwrap_or_default();
        Self {
            base: Instant::now(),
            seed,
            seq: 0,
        }
    }

    pub(crate) fn now(&self) -> MonoTime {
        MonoTime(self.base.elapsed().as_nanos() as u64)
    }

    pub(crate) fn next(&mut self) -> TraceId {
        self.seq = self.seq.wrapping_add(1);
        let mut raw = [0u8; 16];
        raw[..8].copy_from_slice(&self.seed.to_be_bytes());
        raw[8..].copy_from_slice(&self.seq.to_be_bytes());
        TraceId(raw)
    }
}

/// 一条被拒的输入：关联键与失败三列之一。
///
/// 逐行处理的来源（socket、批处理）共用这一载体：状态串、附加字段与建议
/// 都取自统一的失败表，适配器只登记证据，不另造一套错误文案。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Reject {
    trace: TraceId,
    refusal: Refusal,
}

impl Reject {
    pub(crate) fn new(trace: TraceId, refusal: Refusal) -> Self {
        Self { trace, refusal }
    }

    /// 关联键；语法坏到取不到原值时由适配器生成。
    pub fn trace(&self) -> TraceId {
        self.trace
    }

    /// 失败记录：状态串、附加字段与可执行建议。
    pub fn refusal(&self) -> &Refusal {
        &self.refusal
    }
}

/// 单调时钟读数，单位纳秒，用于乱序与超时判定。
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct MonoTime(pub u64);

/// 按键动作。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KeyAction {
    /// 按下。
    Press,
    /// 长按重复。
    Repeat,
    /// 抬起。
    Release,
}

/// 修饰键；只表示物理状态，不决定字符大小写。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Mods {
    /// Shift。
    pub shift: bool,
    /// Control。
    pub ctrl: bool,
    /// Alt。
    pub alt: bool,
}

/// 键码与修饰键；`code` 用 Linux `KEY_*` 码，文本由组合层决定。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct KeyEvent {
    /// Linux `KEY_*` 键码。
    pub code: u16,
    /// 同时处于按下状态的修饰键。
    pub mods: Mods,
    /// 本次动作。
    pub action: KeyAction,
}

/// L1 载荷：保持来源原貌，不在此处做归一化。
#[derive(Clone, Debug)]
pub enum RawBody {
    /// 键码事件，不含文本。
    Key(KeyEvent),
    /// 语音识别结果与置信度。
    Transcript {
        /// 识别文本。
        text: String,
        /// 置信度，取值 0.0–1.0。
        confidence: f32,
        /// 或可回放的音频引用。
        audio_ref: AudioRef,
    },
    /// 已结构化的载荷，待 schema 校验。
    Structured(serde_json::Value),
    /// 批处理的一行。
    Line(String),
}

/// L1 事件：四个适配器的统一产出。
#[derive(Clone, Debug)]
pub struct RawEvent {
    /// 追踪号，随执行链路透传。
    pub trace_id: TraceId,
    /// 来源种类。
    pub source: SourceKind,
    /// 该来源允许的信任级别。
    pub trust: Trust,
    /// 产生时刻。
    pub at: MonoTime,
    /// 来源载荷。
    pub body: RawBody,
}

/// 语法错误写解析位置，与 §7 的 `invalid` 附加字段一致。
pub(crate) fn syntax(err: &serde_json::Error) -> Refusal {
    Refusal::invalid(format!("第{}行第{}列", err.line(), err.column()))
}

/// 字段错误优先写字段名；其余退回 serde 的原文。
pub(crate) fn shape(err: &serde_json::Error) -> Refusal {
    let text = err.to_string();
    if let Some(field) = text
        .strip_prefix("missing field `")
        .and_then(|rest| rest.split('`').next())
    {
        return Refusal::invalid(field);
    }
    Refusal::invalid(text)
}

/// 从原文抢救 `trace_id`；取不到时由调用方生成。
pub(crate) fn trace_in(value: &Value) -> Option<TraceId> {
    let raw = value.get("trace_id")?.clone();
    serde_json::from_value(raw).ok()
}

/// 接入侧预检的失败语义按 §7 换成统一的失败记录。
pub(crate) fn refusal_of(err: CheckError, kind: SourceKind) -> Refusal {
    match err {
        CheckError::Invalid(field) => Refusal::invalid(field),
        CheckError::Denied(_) => Refusal::denied(kind.need().label()),
    }
}

/// 坏行（语法或字段）：能从原文取到 trace_id 就复用，否则新配一个。
pub(crate) fn bad_line(clock: &mut Clock, value: Option<&Value>, refusal: Refusal) -> Reject {
    let trace = value.and_then(trace_in).unwrap_or_else(|| clock.next());
    Reject::new(trace, refusal)
}

/// 从积压里取走至多 `max` 条事件；两个逐行适配器共用这段出队逻辑。
pub(crate) fn take_events(
    queue: &mut std::collections::VecDeque<RawEvent>,
    max: usize,
) -> Vec<RawEvent> {
    let mut batch = Vec::new();
    while batch.len() < max {
        let Some(event) = queue.pop_front() else {
            break;
        };
        batch.push(event);
    }
    batch
}
