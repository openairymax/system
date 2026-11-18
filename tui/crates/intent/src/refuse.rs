// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 失败记录
//
// 失败三列表只有一份（FailKind）；本类型补上「附加」这一列，让接入面与
// 契约适配面共用一个错误载体：类别决定状态串、返回码与建议，附加字段只
// 写首个出错位置、动作名、组件名或读到的版本号。
// SSoT: 0.0.5 方案 §7 失败三列表。
// ===========================================================================

//! 失败类别与具体证据的组合。

use core::fmt::Display;

use crate::status::FailKind;

/// 一次失败：类别走三列表，附加字段只放具体证据。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Refusal {
    kind: FailKind,
    detail: String,
}

impl Refusal {
    /// 语法或必填字段不通过；附加字段写首个不通过项。
    pub fn invalid(detail: impl Display) -> Self {
        Self::new(FailKind::Invalid, detail)
    }

    /// 动作或版本不受支持；附加字段写命中的名字或版本。
    pub fn unsupported(detail: impl Display) -> Self {
        Self::new(FailKind::Unsupported, detail)
    }

    /// 信任级别不足；附加字段写所需的最低级别。
    pub fn denied(detail: impl Display) -> Self {
        Self::new(FailKind::Denied, detail)
    }

    /// 白名单未命中或黑名单命中；附加字段写命中的动作类别。
    pub fn forbidden(detail: impl Display) -> Self {
        Self::new(FailKind::Forbidden, detail)
    }

    /// 审批缺失或过期；附加字段写动作名。
    pub fn approval(detail: impl Display) -> Self {
        Self::new(FailKind::Approval, detail)
    }

    /// 端到端预算耗尽；附加字段写预算毫秒。
    pub fn timeout(budget_ms: u32) -> Self {
        Self::new(FailKind::Timeout, budget_ms)
    }

    /// 未完成请求达到上限；附加字段写当前积压数。
    pub fn busy(pending: usize) -> Self {
        Self::new(FailKind::Busy, pending)
    }

    /// 依赖组件不可用；附加字段写组件名。
    pub fn unavailable(component: impl Display) -> Self {
        Self::new(FailKind::Unavailable, component)
    }

    /// 自身缺陷；附加字段写能定位到的最小证据。
    pub fn internal(detail: impl Display) -> Self {
        Self::new(FailKind::Internal, detail)
    }

    /// 所属类别，供调用方按语义分支。
    pub fn kind(&self) -> FailKind {
        self.kind
    }

    /// 失败表的返回码，统一取负值。
    pub fn errno(&self) -> i32 {
        self.kind.errno()
    }

    /// 稳定的状态串，可直接写进日志与状态栏。
    pub fn status(&self) -> &'static str {
        self.kind.status()
    }

    /// 失败表的附加字段：解析位置、字段名、动作名或版本号。
    pub fn detail(&self) -> &str {
        &self.detail
    }

    /// 可执行建议；不含推测，只给本机能做的一步。
    pub fn advice(&self) -> &'static str {
        self.kind.advice()
    }

    fn new(kind: FailKind, detail: impl Display) -> Self {
        Self {
            kind,
            detail: detail.to_string(),
        }
    }
}
