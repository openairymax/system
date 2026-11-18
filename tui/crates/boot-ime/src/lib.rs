// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// AirymaxOS 输入法引擎（boot-ime）
//
// 承载拼音切分、候选生成与排序，以及 AIRYIME1 词典的加载与校验。
// N-03 落地词典、检索、排序、JSON Lines 协议与会话状态机五块；词典字节
// 格式的权威说明在本 crate 的 SPEC.md。进程侧的重放与恢复由 boot-tui
// 驱动，本 crate 只提供可复用的解析、排序与状态推进。
// ===========================================================================

//! 引导期 TUI 输入法引擎的公共接口。

pub mod dict;
pub mod ipc;
pub mod lookup;
pub mod rank;
pub mod session;

pub use dict::{build, crc32, Dictionary, Entry, LoadError, MAGIC, VERSION};
pub use ipc::{Frame, IpcError, Msg, MAX_FRAME, RECOVER_MS, REPLAY, SLOW_MS};
pub use lookup::search;
pub use rank::{rank, Candidate};
pub use session::{Effect, SessError, Session, LIMIT};
