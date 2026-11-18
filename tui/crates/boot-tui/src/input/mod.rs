// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 引导期输入抽象层（L1 事件层）
//
// 键盘、语音、外接结构化 API、批处理四条来源先归一到 RawEvent，再由组合
// 层转成 airy.intent/1。抽象单位是事件而不是文本：文本只是 Key 源经过
// IME 之后的产物，语音与结构化载荷不得被塞回「文本 → 输入法」管线。
//
// 本片（N-01）只声明类型、trait 与错误码，不含函数体。
// SSoT: 30-interfaces/11-input-ingress.md §10.1-§10.3。
// ===========================================================================

//! L1 输入事件层：四类来源归一到 [`RawEvent`]。

mod event;
mod source;

pub use event::{
    AudioRef, KeyAction, KeyEvent, Mods, MonoTime, RawBody, RawEvent, SourceKind, TraceId, Trust,
};
pub use source::{InputSource, SourceError};
