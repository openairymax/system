// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 端到端预算与超时交付
//
// 契约 §4.4 把 `constraints.deadline_ms` 钉成端到端预算：从接入到结果
// 回到用户的总时长，而不是某一段的等待。超时的后果只有一条——取消，
// 不给部分结果。因此这里不提供「先返回半截、再补状态」的口径：结果
// 只能在预算内整体交付，过期后到达的值整条作废。
//
// 交付口只有一个：`Deadline::settle`。调用方拿到值就说明交付那一刻仍在
// 预算内；拿到超时记录就说明值已被丢弃。分两处判断（先取结果、再查时间）
// 会让「最后一毫秒到达的部分输出」漏出去，故判定与交付合成一个函数。
//
// 预算缺省表示不设上限；`deadline_ms=0` 不是「无限制」，而是立即到期——
// 字段写 0 就是要求零预算，不能悄悄换成不设限。
//
// SSoT: 30-interfaces/11-input-ingress.md §4.4、§7；0.0.5 §14.9。
// ===========================================================================

//! 端到端预算、截止时刻与唯一交付口。

use std::time::{Duration, Instant};

use crate::refuse::Refusal;
use crate::schema::Constraints;

/// 一次请求的端到端预算，单位毫秒。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Budget(u32);

impl Budget {
    /// 以毫秒构造；`0` 表示立即到期。
    pub const fn from_millis(millis: u32) -> Self {
        Self(millis)
    }

    /// 从执行约束取预算；缺省返回 `None`，表示不设上限。
    pub fn derive(rules: &Constraints) -> Option<Self> {
        rules.deadline_ms.map(Self::from_millis)
    }

    /// 预算毫秒数；写进超时记录的附加字段。
    pub const fn millis(self) -> u32 {
        self.0
    }

    /// 预算对应的时长。
    pub fn limit(self) -> Duration {
        Duration::from_millis(u64::from(self.0))
    }

    /// 以当前单调时刻起算。
    pub fn start(self) -> Deadline {
        self.start_at(Instant::now())
    }

    /// 以调用方给的单调时刻起算；同一条意图的多个步骤共用起点。
    pub fn start_at(self, origin: Instant) -> Deadline {
        Deadline {
            budget: self,
            origin,
        }
    }
}

/// 预算的剩余时间；所有判定都以单调时钟为准。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Deadline {
    budget: Budget,
    origin: Instant,
}

impl Deadline {
    /// 起算时用的预算。
    pub const fn budget(&self) -> Budget {
        self.budget
    }

    /// 自起算点起经过的时间。
    pub fn elapsed(&self) -> Duration {
        self.origin.elapsed()
    }

    /// 剩余时间；到期后收敛到零，不回绕。
    pub fn remaining(&self) -> Duration {
        self.budget.limit().saturating_sub(self.elapsed())
    }

    /// 是否已到期；`deadline_ms=0` 在起算瞬间即到期。
    pub fn expired(&self) -> bool {
        self.elapsed() >= self.budget.limit()
    }

    /// 到期即返回超时记录，未到期返回 `Ok`。
    pub fn check(&self) -> Result<(), Refusal> {
        if self.expired() {
            Err(Refusal::timeout(self.budget.millis()))
        } else {
            Ok(())
        }
    }

    /// 唯一交付口：预算内整体交付，过期则连同值一起作废。
    pub fn settle<T>(&self, value: T) -> Answer<T> {
        match self.check() {
            Ok(()) => Answer::Done(value),
            Err(refusal) => Answer::Late(refusal),
        }
    }
}

/// 交付结果；只有 [`Answer::Done`] 带值，超时时值已被丢弃。
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Answer<T> {
    /// 预算内交付的完整结果。
    Done(T),
    /// 超时记录；值未交付，也不存在部分值。
    Late(Refusal),
}

impl<T> Answer<T> {
    /// 取交付的值；超时时返回 `None`。
    pub fn value(self) -> Option<T> {
        match self {
            Self::Done(value) => Some(value),
            Self::Late(_) => None,
        }
    }

    /// 取超时记录；成功交付时为 `None`。
    pub fn refusal(&self) -> Option<&Refusal> {
        match self {
            Self::Done(_) => None,
            Self::Late(refusal) => Some(refusal),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::status::FailKind;

    fn expired_deadline(millis: u32) -> Deadline {
        let past = Duration::from_millis(u64::from(millis) + 1);
        Budget::from_millis(millis).start_at(Instant::now() - past)
    }

    #[test]
    fn zero_budget_expires_at_once() {
        let deadline = Budget::from_millis(0).start();
        assert!(deadline.expired());
        assert_eq!(deadline.remaining(), Duration::ZERO);
    }

    #[test]
    fn absent_budget_means_no_limit() {
        let rules = Constraints::default();
        assert_eq!(Budget::derive(&rules), None);
    }

    #[test]
    fn expiry_carries_contract_timeout() {
        let deadline = expired_deadline(40);
        let refusal = deadline.check().expect_err("必须超时");
        assert_eq!(refusal.kind(), FailKind::Timeout);
        assert_eq!(refusal.status(), "timeout");
        assert_eq!(refusal.errno(), -110);
        assert_eq!(refusal.detail(), "40");
    }

    #[test]
    fn late_value_is_dropped() {
        let answer = expired_deadline(10).settle(String::from("半截"));
        assert_eq!(answer.value(), None);
    }

    #[test]
    fn live_budget_delivers_whole_value() {
        let deadline = Budget::from_millis(5_000).start();
        let answer = deadline.settle(String::from("完整"));
        assert_eq!(answer.refusal(), None);
        assert_eq!(answer.value().as_deref(), Some("完整"));
    }

    #[test]
    fn remaining_never_wraps() {
        let deadline = expired_deadline(3);
        assert_eq!(deadline.remaining(), Duration::ZERO);
    }
}
