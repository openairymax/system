// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 白/黑名单判定
//
// 接入侧预检的第 5 步只回答一个问题：这条动作类别在约束下能否放行。
// 判定顺序是固定的——deny 先于 allow，缺省的白名单等于不设限；唯一的
// 例外是 T3，自动化来源默认拒绝，只有显式列入白名单的动作才放行。
//
// 这里不读策略，也不判审批：最终判定属于安全通道与执行侧，本函数只做
// 语法与约束层面的预检，通过不代表允许执行。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.4 第 5 步、§4.1、§7。
// ===========================================================================

//! 白/黑名单判定。

use crate::schema::Constraints;
use crate::trust::Trust;

/// 判定结果。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Verdict {
    /// 预检放行；执行侧仍须做动作注册与参数校验。
    Allow,
    /// 拒绝；调用方按 `-EPERM` 报 `denied`，附加字段填命中项。
    Deny(Reason),
}

/// 拒绝的原因，用于把命中项写进错误附加字段。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Reason {
    /// 命中黑名单。
    DenyList,
    /// 白名单未包含该动作。
    AllowList,
    /// 自动化来源未在显式白名单里。
    NoList,
}

/// 按约束与来源级别判定一个动作类别。
pub fn decide(constraints: &Constraints, trust: Trust, action: &str) -> Verdict {
    if constraints.deny.iter().any(|item| item == action) {
        return Verdict::Deny(Reason::DenyList);
    }
    match constraints.allow.as_ref() {
        Some(list) if !list.iter().any(|item| item == action) => Verdict::Deny(Reason::AllowList),
        None if trust == Trust::T3 => Verdict::Deny(Reason::NoList),
        _ => Verdict::Allow,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn constraints(allow: Option<&[&str]>, deny: &[&str]) -> Constraints {
        Constraints {
            deadline_ms: None,
            allow: allow.map(|list| list.iter().map(|item| (*item).to_string()).collect()),
            deny: deny.iter().map(|item| (*item).to_string()).collect(),
        }
    }

    #[test]
    fn deny_wins_over_allow() {
        let rules = constraints(Some(&["disk.format"]), &["disk.format"]);
        assert_eq!(
            decide(&rules, Trust::T0, "disk.format"),
            Verdict::Deny(Reason::DenyList)
        );
    }

    #[test]
    fn listed_allow_restricts() {
        let rules = constraints(Some(&["disk.status"]), &[]);
        assert_eq!(decide(&rules, Trust::T0, "disk.status"), Verdict::Allow);
        assert_eq!(
            decide(&rules, Trust::T0, "disk.format"),
            Verdict::Deny(Reason::AllowList)
        );
    }

    #[test]
    fn absent_allow_means_no_restriction() {
        let rules = constraints(None, &[]);
        assert_eq!(decide(&rules, Trust::T0, "disk.format"), Verdict::Allow);
        assert_eq!(decide(&rules, Trust::T2, "net.reset"), Verdict::Allow);
    }

    #[test]
    fn automation_needs_an_explicit_list() {
        let rules = constraints(None, &[]);
        assert_eq!(
            decide(&rules, Trust::T3, "disk.status"),
            Verdict::Deny(Reason::NoList)
        );
        let listed = constraints(Some(&["disk.status"]), &[]);
        assert_eq!(decide(&listed, Trust::T3, "disk.status"), Verdict::Allow);
    }
}
