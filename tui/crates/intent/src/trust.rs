// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 来源与信任级别
//
// 四个来源各自只允许落在固定的信任级别上（接入契约 §4.1）。级别由适配器
// 判定，组合层与界面都不得改写或提升；这里只提供唯一的判定口径，避免
// 每个入口各写一份比较式。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.1、§4.1、§4.2。
// ===========================================================================

//! 来源种类、信任级别与二者之间的合法配对。

use serde::Deserialize;

/// 来源种类，与 `airy.intent/1` 的 `source.kind` 一一对应。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub enum SourceKind {
    /// 本地键盘：适配器只产键码，不产文本。
    #[serde(rename = "keyboard")]
    Key,
    /// 语音：识别结果先进可编辑缓冲区，确认后才成为意图。
    #[serde(rename = "voice")]
    Voice,
    /// 外接结构化 API：身份由 `SO_PEERCRED` 决定。
    #[serde(rename = "api")]
    Api,
    /// 批处理：逐行一个意图，信任级别固定 T3。
    #[serde(rename = "batch")]
    Batch,
}

/// 信任级别；只由适配器判定，组合层不得改写。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub enum Trust {
    /// 本地控制台用户。
    #[serde(rename = "T0")]
    T0,
    /// 已认证会话用户。
    #[serde(rename = "T1")]
    T1,
    /// 服务主体。
    #[serde(rename = "T2")]
    T2,
    /// 自动化。
    #[serde(rename = "T3")]
    T3,
}

impl Trust {
    /// 契约 §4.2 的写法，用于日志与错误附加字段。
    pub fn label(self) -> &'static str {
        match self {
            Self::T0 => "T0",
            Self::T1 => "T1",
            Self::T2 => "T2",
            Self::T3 => "T3",
        }
    }
}

impl SourceKind {
    /// 该来源被允许的信任级别。
    ///
    /// 键盘与语音同权（T0）；API 由 `SO_PEERCRED` 决定，取 T1–T2；
    /// 批处理固定 T3，不可提升。
    pub fn allows(self, trust: Trust) -> bool {
        match self {
            Self::Key | Self::Voice => trust == Trust::T0,
            Self::Api => matches!(trust, Trust::T1 | Trust::T2),
            Self::Batch => trust == Trust::T3,
        }
    }

    /// 契约 §7 `denied` 附加字段里的「所需级别」。
    pub fn need(self) -> Trust {
        match self {
            Self::Key | Self::Voice => Trust::T0,
            Self::Api => Trust::T1,
            Self::Batch => Trust::T3,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LEVELS: [Trust; 4] = [Trust::T0, Trust::T1, Trust::T2, Trust::T3];

    #[test]
    fn key_and_voice_are_t0_only() {
        for kind in [SourceKind::Key, SourceKind::Voice] {
            for level in LEVELS {
                assert_eq!(kind.allows(level), level == Trust::T0, "{kind:?}/{level:?}");
            }
        }
    }

    #[test]
    fn api_takes_t1_and_t2() {
        for level in LEVELS {
            let want = matches!(level, Trust::T1 | Trust::T2);
            assert_eq!(SourceKind::Api.allows(level), want, "{level:?}");
        }
        assert_eq!(SourceKind::Api.need(), Trust::T1);
    }

    #[test]
    fn batch_is_t3_and_cannot_be_lifted() {
        for level in LEVELS {
            assert_eq!(
                SourceKind::Batch.allows(level),
                level == Trust::T3,
                "{level:?}"
            );
        }
        assert_eq!(SourceKind::Batch.need(), Trust::T3);
    }

    #[test]
    fn labels_follow_the_contract() {
        let labels: Vec<_> = LEVELS.iter().map(|level| level.label()).collect();
        assert_eq!(labels, ["T0", "T1", "T2", "T3"]);
    }
}
