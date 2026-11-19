// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// airy-model status --json 的消费侧类型
//
// 对外 JSON 只有四个字段：状态、后端、原因与清单摘要；schema 不上屏，
// 版本口径由运行根里的清单自己持有。原因与后端是逐字节的稳定 token，
// 这里按 0.0.4 的枚举全量列出，不做前缀改写——degraded:<原因> 的拼接
// 属于行式输出，不在 JSON 字段内。
// 实测样张见 tests/golden/model-status.json。
// ===========================================================================

//! `airy-model status --json` 的消费侧类型。

use intent::Refusal;
use serde::Deserialize;

use crate::decode;

/// 一次运行期判定。
#[derive(Debug, Deserialize)]
pub struct Status {
    /// 运行状态。
    pub state: State,
    /// 实际使用的推理后端。
    pub backend: Backend,
    /// 降级或失败的原因；就绪时为 null。
    pub reason: Option<Reason>,
    /// 已校验清单的 sha256；无摘要时为空串。
    pub digest: String,
}

/// 运行状态。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum State {
    /// 就绪。
    Ready,
    /// 降级可用；此时必然带原因。
    Degraded,
    /// 失败；此时必然带原因。
    Failed,
}

/// 推理后端。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Backend {
    /// CPU。
    Cpu,
    /// CUDA。
    Cuda,
    /// ROCm。
    Rocm,
    /// Vulkan。
    Vulkan,
}

/// 状态原因；token 与状态文件、日志、systemd 条件共用一套。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum Reason {
    /// 没有可用的加速后端。
    NoAccel,
    /// 清单要求的引擎不可用。
    NoRuntime,
    /// 运行根里没有模型。
    NoModel,
    /// 清单文件不存在。
    NoManifest,
    /// 清单不可读或不可解析。
    ManifestUnreadable,
    /// 资产摘要与清单不符。
    DigestMismatch,
    /// 资产内容损坏。
    AssetCorrupt,
    /// 状态文件不可写。
    StateUnwritable,
}

impl Status {
    /// 解析 status 载荷；字段与枚举不受支持即拒绝。
    pub fn parse(bytes: &[u8]) -> Result<Self, Refusal> {
        decode(bytes)
    }
}
