// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// L1 适配器接口与错误码
//
// 适配器只负责产事件与关闭；组合、审批与执行都不在适配器内。错误码与
// 接入契约 §7 的稳定状态串一一对应，每个变体只表达一种失败语义。
//
// SSoT: 30-interfaces/11-input-ingress.md §7、§10.2。
// ===========================================================================

use super::{RawEvent, SourceKind, Trust};

/// 输入适配器的统一接口。
pub trait InputSource: Send {
    /// 本适配器的来源种类，恒定不变。
    fn kind(&self) -> SourceKind;

    /// 最多取出 `max` 条事件；空结果表示当前无可取事件。
    fn drain(&mut self, max: usize) -> Result<Vec<RawEvent>, SourceError>;

    /// 关闭来源并释放其独占资源；重复关闭不得报错。
    fn close(&mut self) -> Result<(), SourceError>;
}

/// 契约 §4.1：来源与信任级别的合法组合。
///
/// 适配器在把事件交给组合层之前调用一次；组合层不再复核，也不得改写级别。
pub fn trust_ok(kind: SourceKind, trust: Trust) -> Result<(), SourceError> {
    let ok = match kind {
        SourceKind::Key | SourceKind::Voice => trust == Trust::T0,
        SourceKind::Api => matches!(trust, Trust::T1 | Trust::T2),
        SourceKind::Batch => trust == Trust::T3,
    };
    if ok {
        Ok(())
    } else {
        Err(SourceError::Denied {
            need: need_of(kind),
        })
    }
}

fn need_of(kind: SourceKind) -> Trust {
    match kind {
        SourceKind::Key | SourceKind::Voice => Trust::T0,
        SourceKind::Api => Trust::T1,
        SourceKind::Batch => Trust::T3,
    }
}

/// 适配器错误；一个变体只对应契约里的一条失败语义。
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SourceError {
    /// 语法或必填字段不通过，`status=invalid`。
    Invalid {
        /// 首个不通过的字段名。
        field: &'static str,
    },
    /// 动作未注册，`status=unsupported`。
    Unsupported {
        /// 未注册的动作名。
        action: String,
    },
    /// 信任级别不足，`status=denied`。
    Denied {
        /// 所需的最低级别。
        need: Trust,
    },
    /// 白名单未命中或黑名单命中，`status=denied`。
    Forbidden {
        /// 命中的动作类别。
        item: String,
    },
    /// 审批缺失或已过期，`status=approval-required`。
    Approval {
        /// 需要审批的动作类别。
        action: String,
    },
    /// 端到端预算耗尽，`status=timeout`。
    Timeout {
        /// 预算毫秒数。
        budget_ms: u32,
    },
    /// 未完成请求达到上限，`status=busy`。
    Busy {
        /// 当前未完成数。
        pending: usize,
    },
    /// 依赖组件不可用，`status=unavailable`。
    Unavailable {
        /// 组件名，如 `ime`、`asr`。
        component: &'static str,
    },
    /// 结果不可序列化，`status=internal`。
    Internal {
        /// 出错的事件序号。
        seq: u64,
    },
}

/// 契约 §7 用到的 errno 值；Linux 通用编号，x86、ARM、RISC-V 一致。
const EPERM: i32 = 1;
const EIO: i32 = 5;
const EAGAIN: i32 = 11;
const EACCES: i32 = 13;
const EINVAL: i32 = 22;
/// `EOPNOTSUPP` 的同义词，本系统统一使用 `ENOTSUP` 这一名字。
const ENOTSUP: i32 = 95;
const ETIMEDOUT: i32 = 110;

impl SourceError {
    /// 契约 §7 的返回码，统一取负值。
    pub fn errno(&self) -> i32 {
        match self {
            Self::Invalid { .. } | Self::Unsupported { .. } => -EINVAL,
            Self::Denied { .. } | Self::Approval { .. } => -EACCES,
            Self::Forbidden { .. } => -EPERM,
            Self::Timeout { .. } => -ETIMEDOUT,
            Self::Busy { .. } => -EAGAIN,
            Self::Unavailable { .. } => -ENOTSUP,
            Self::Internal { .. } => -EIO,
        }
    }

    /// 稳定的状态串，可直接写进日志与状态栏。
    pub fn status(&self) -> &'static str {
        match self {
            Self::Invalid { .. } => "invalid",
            Self::Unsupported { .. } => "unsupported",
            Self::Denied { .. } | Self::Forbidden { .. } => "denied",
            Self::Approval { .. } => "approval-required",
            Self::Timeout { .. } => "timeout",
            Self::Busy { .. } => "busy",
            Self::Unavailable { .. } => "unavailable",
            Self::Internal { .. } => "internal",
        }
    }

    /// 契约 §7 的「附加」字段：解析位置、字段名、动作名等具体证据。
    pub fn detail(&self) -> String {
        match self {
            Self::Invalid { field } => (*field).to_string(),
            Self::Unsupported { action } | Self::Approval { action } => action.clone(),
            Self::Denied { need } => need.label().to_string(),
            Self::Forbidden { item } => item.clone(),
            Self::Timeout { budget_ms } => budget_ms.to_string(),
            Self::Busy { pending } => pending.to_string(),
            Self::Unavailable { component } => (*component).to_string(),
            Self::Internal { seq } => seq.to_string(),
        }
    }

    /// 可执行建议；不含推测，只给本机能做的一步。
    pub fn advice(&self) -> &'static str {
        match self {
            Self::Invalid { .. } => "检查 JSON 语法与必填字段后重试",
            Self::Unsupported { .. } => "改用执行侧已注册的动作",
            Self::Denied { .. } => "提升信任级别或改用允许的来源通道",
            Self::Forbidden { .. } => "从 deny 列表移除该动作或改用允许的动作",
            Self::Approval { .. } => "在本地确认对话框完成审批后重试",
            Self::Timeout { .. } => "提高 deadline_ms 或缩小请求范围",
            Self::Busy { .. } => "等待未完成请求返回后重试",
            Self::Unavailable { .. } => "确认依赖组件已启动后重试",
            Self::Internal { .. } => "记录 trace_id 并上报缺陷",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cases() -> Vec<(SourceError, i32, &'static str)> {
        vec![
            (SourceError::Invalid { field: "schema" }, -22, "invalid"),
            (
                SourceError::Unsupported {
                    action: "disk.wipe".to_string(),
                },
                -22,
                "unsupported",
            ),
            (SourceError::Denied { need: Trust::T2 }, -13, "denied"),
            (
                SourceError::Forbidden {
                    item: "disk.format".to_string(),
                },
                -1,
                "denied",
            ),
            (
                SourceError::Approval {
                    action: "disk.format".to_string(),
                },
                -13,
                "approval-required",
            ),
            (SourceError::Timeout { budget_ms: 5000 }, -110, "timeout"),
            (SourceError::Busy { pending: 8 }, -11, "busy"),
            (
                SourceError::Unavailable { component: "ime" },
                -95,
                "unavailable",
            ),
            (SourceError::Internal { seq: 7 }, -5, "internal"),
        ]
    }

    #[test]
    fn errno_and_status_follow_contract() {
        for (err, code, status) in cases() {
            assert_eq!(err.errno(), code, "{err:?}");
            assert_eq!(err.status(), status, "{err:?}");
        }
    }

    #[test]
    fn detail_names_the_offending_input() {
        for (err, _, _) in cases() {
            assert!(!err.detail().is_empty(), "{err:?}");
        }
        assert_eq!(
            SourceError::Denied { need: Trust::T2 }.detail(),
            "T2",
            "所需级别要写进附加字段"
        );
        assert_eq!(
            SourceError::Timeout { budget_ms: 5000 }.detail(),
            "5000",
            "预算毫秒要写进附加字段"
        );
    }

    #[test]
    fn trust_pairs_match_the_contract() {
        let levels = [Trust::T0, Trust::T1, Trust::T2, Trust::T3];
        for kind in [SourceKind::Key, SourceKind::Voice] {
            for level in levels {
                assert_eq!(trust_ok(kind, level).is_ok(), level == Trust::T0);
            }
        }
        for level in levels {
            let api = trust_ok(SourceKind::Api, level);
            assert_eq!(api.is_ok(), matches!(level, Trust::T1 | Trust::T2));
            if let Err(err) = api {
                assert_eq!(err.detail(), "T1", "API 的最低级别是 T1");
            }
            assert_eq!(
                trust_ok(SourceKind::Batch, level).is_ok(),
                level == Trust::T3
            );
        }
    }

    #[test]
    fn every_variant_has_advice() {
        for (err, _, _) in cases() {
            assert!(!err.advice().is_empty(), "{err:?}");
        }
    }
}
