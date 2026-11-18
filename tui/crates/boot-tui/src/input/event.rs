// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
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
