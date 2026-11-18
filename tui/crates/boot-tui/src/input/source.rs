// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// L1 适配器接口与错误码
//
// 适配器只负责产事件与关闭；组合、审批与执行都不在适配器内。错误类别与
// 状态串、返回码、建议三列由 `intent::FailKind` 统一给出：接入侧与执行面
// 共用同一份契约表，本模块只把「具体是哪个字段、哪个组件」写进附加字段，
// 不再保存第二份映射。
//
// SSoT: 30-interfaces/11-input-ingress.md §7、§10.2。
// ===========================================================================

use intent::FailKind;

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
    if kind.allows(trust) {
        Ok(())
    } else {
        Err(SourceError::denied(kind.need()))
    }
}

/// 适配器错误；类别决定状态串与返回码，附加字段只放具体证据。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceError {
    kind: FailKind,
    detail: String,
}

impl SourceError {
    /// 语法或必填字段不通过；`field` 是首个不通过的字段名。
    pub fn invalid(field: &str) -> Self {
        Self::new(FailKind::Invalid, field)
    }

    /// 动作未注册。
    pub fn unsupported(action: &str) -> Self {
        Self::new(FailKind::Unsupported, action)
    }

    /// 信任级别不足；附加字段写所需的最低级别。
    pub fn denied(need: Trust) -> Self {
        Self::new(FailKind::Denied, need.label())
    }

    /// 白名单未命中或黑名单命中；附加字段写命中的动作类别。
    pub fn forbidden(item: &str) -> Self {
        Self::new(FailKind::Forbidden, item)
    }

    /// 审批缺失或过期。
    pub fn approval(action: &str) -> Self {
        Self::new(FailKind::Approval, action)
    }

    /// 端到端预算耗尽。
    pub fn timeout(budget_ms: u32) -> Self {
        Self::new(FailKind::Timeout, budget_ms)
    }

    /// 未完成请求达到上限。
    pub fn busy(pending: usize) -> Self {
        Self::new(FailKind::Busy, pending)
    }

    /// 依赖组件不可用。
    pub fn unavailable(component: &str) -> Self {
        Self::new(FailKind::Unavailable, component)
    }

    /// 结果不可序列化；附加字段写事件序号。
    pub fn internal(seq: u64) -> Self {
        Self::new(FailKind::Internal, seq)
    }

    /// 所属类别，供调用方按语义分支。
    pub fn kind(&self) -> FailKind {
        self.kind
    }

    /// 契约 §7 的返回码，统一取负值。
    pub fn errno(&self) -> i32 {
        self.kind.errno()
    }

    /// 稳定的状态串，可直接写进日志与状态栏。
    pub fn status(&self) -> &'static str {
        self.kind.status()
    }

    /// 契约 §7 的「附加」字段：解析位置、字段名、动作名等具体证据。
    pub fn detail(&self) -> String {
        self.detail.clone()
    }

    /// 可执行建议；不含推测，只给本机能做的一步。
    pub fn advice(&self) -> &'static str {
        self.kind.advice()
    }

    fn new(kind: FailKind, detail: impl core::fmt::Display) -> Self {
        Self {
            kind,
            detail: detail.to_string(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cases() -> Vec<(SourceError, i32, &'static str)> {
        vec![
            (SourceError::invalid("schema"), -22, "invalid"),
            (SourceError::unsupported("disk.wipe"), -22, "unsupported"),
            (SourceError::denied(Trust::T2), -13, "denied"),
            (SourceError::forbidden("disk.format"), -1, "denied"),
            (
                SourceError::approval("disk.format"),
                -13,
                "approval-required",
            ),
            (SourceError::timeout(5000), -110, "timeout"),
            (SourceError::busy(8), -11, "busy"),
            (SourceError::unavailable("ime"), -95, "unavailable"),
            (SourceError::internal(7), -5, "internal"),
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
            SourceError::denied(Trust::T2).detail(),
            "T2",
            "所需级别要写进附加字段"
        );
        assert_eq!(
            SourceError::timeout(5000).detail(),
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

    #[test]
    fn kinds_are_distinct() {
        let seen: Vec<FailKind> = cases().into_iter().map(|(err, _, _)| err.kind()).collect();
        let expected = [
            FailKind::Invalid,
            FailKind::Unsupported,
            FailKind::Denied,
            FailKind::Forbidden,
            FailKind::Approval,
            FailKind::Timeout,
            FailKind::Busy,
            FailKind::Unavailable,
            FailKind::Internal,
        ];
        assert_eq!(seen, expected, "构造器与类别必须一一对应");
    }
}
