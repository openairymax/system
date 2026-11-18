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

use intent::Refusal;

use super::{RawEvent, SourceKind, Trust};

/// 输入适配器的统一接口。
pub trait InputSource: Send {
    /// 本适配器的来源种类，恒定不变。
    fn kind(&self) -> SourceKind;

    /// 最多取出 `max` 条事件；空结果表示当前无可取事件。
    fn drain(&mut self, max: usize) -> Result<Vec<RawEvent>, Refusal>;

    /// 关闭来源并释放其独占资源；重复关闭不得报错。
    fn close(&mut self) -> Result<(), Refusal>;
}

/// 契约 §4.1：来源与信任级别的合法组合。
///
/// 适配器在把事件交给组合层之前调用一次；组合层不再复核，也不得改写级别。
pub fn trust_ok(kind: SourceKind, trust: Trust) -> Result<(), Refusal> {
    if kind.allows(trust) {
        Ok(())
    } else {
        Err(Refusal::denied(kind.need().label()))
    }
}

#[cfg(test)]
mod tests {
    use intent::FailKind;

    use super::*;

    fn cases() -> Vec<(Refusal, i32, &'static str)> {
        vec![
            (Refusal::invalid("schema"), -22, "invalid"),
            (Refusal::unsupported("disk.wipe"), -22, "unsupported"),
            (Refusal::denied(Trust::T2.label()), -13, "denied"),
            (Refusal::forbidden("disk.format"), -1, "denied"),
            (Refusal::approval("disk.format"), -13, "approval-required"),
            (Refusal::timeout(5000), -110, "timeout"),
            (Refusal::busy(8), -11, "busy"),
            (Refusal::unavailable("ime"), -95, "unavailable"),
            (Refusal::internal(7), -5, "internal"),
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
            Refusal::denied(Trust::T2.label()).detail(),
            "T2",
            "所需级别要写进附加字段"
        );
        assert_eq!(
            Refusal::timeout(5000).detail(),
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
