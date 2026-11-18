// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// L1 输入事件的载荷与元数据
//
// 事件带来源、信任级别、单调时刻与追踪号；载荷保持各来源的原貌，
// 归一化只发生在组合层。字段语义以接入契约为准。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.1、§4.1、§10.2。
// ===========================================================================

pub use intent::{AudioRef, SourceKind, TraceId, Trust};

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
