// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// AirymaxOS 冻结契约适配层（airy-adapters）
//
// 把四个已冻结的 --json 契约（磁盘、模型、探针、RT 能力）适配成本工作区
// 的类型，契约字段集不得改写，不够用处只加消费侧适配。本 crate 是库，
// 不产出可执行文件。公开面按契约逐片填入：S-01 落磁盘，S-02 落模型，
// 每片先录 0.0.4 的实测输出作 golden，再按样张声明类型。
// ===========================================================================

//! 冻结契约适配层的公共接口。

pub mod disk;
pub mod model;
pub mod probe;
pub mod rtcap;

use intent::Refusal;
use serde::de::DeserializeOwned;

/// 契约载荷的统一解码路径：解析失败折成失败表的 invalid。
fn decode<T: DeserializeOwned>(bytes: &[u8]) -> Result<T, Refusal> {
    serde_json::from_slice(bytes).map_err(Refusal::invalid)
}
