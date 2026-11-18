// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// AirymaxOS 结构化意图模型（intent）
//
// 把键盘、语音与外接 API 三条通道的输入统一成同一种意图类型，使执行面
// 只面对一种输入。本 crate 是库，不产出可执行文件。公开面在 A-01 起
// 逐片填入：先立契约标识符与信任配对，再补载荷模型、校验与路由。
// ===========================================================================

//! 结构化意图模型的公共接口。

mod schema;
mod trust;

pub use schema::{TraceId, TraceIdError};
pub use trust::{SourceKind, Trust};
