// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 引导期输入抽象层（L1 事件层）
//
// 键盘、语音、外接结构化 API、批处理四条来源先归一到 RawEvent，再由组合
// 层转成 airy.intent/1。抽象单位是事件而不是文本：文本只是 Key 源经过
// IME 之后的产物，语音与结构化载荷不得被塞回「文本 → 输入法」管线。
//
// 本片（N-01）声明 L1 的类型、trait 与错误码；N-02 起补键序列解码与
// 键盘事件源。
// SSoT: 30-interfaces/11-input-ingress.md §10.1-§10.3。
// ===========================================================================

//! L1 输入事件层：四类来源归一到 [`RawEvent`]。

mod api;
mod escape;
mod event;
mod key;
mod source;
mod term;

pub use api::{Api, Reject, MAX_FRAME, PENDING, SOCKET};
pub use escape::{decode, lone, Decoder, Step, MAX_PENDING};
pub use event::{
    AudioRef, KeyAction, KeyEvent, Mods, MonoTime, RawBody, RawEvent, SourceKind, TraceId, Trust,
};
pub use intent::{FailKind, Refusal};
pub use key::{
    key_byte, KEY_C, KEY_DOWN, KEY_END, KEY_ENTER, KEY_ESC, KEY_F1, KEY_F5, KEY_HOME, KEY_PAGEDOWN,
    KEY_PAGEUP, KEY_Q, KEY_UP,
};
pub use source::{trust_ok, InputSource};
pub use term::{Keys, Term};
