// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 失败路径的公开接口用例
//
// 这组用例只走 crate 的公开面，验证三条契约口径：预算耗尽后不交付任何
// 部分结果；取消的归类只落在 §7 的封闭九行里；终止之后必须能归还会话
// 台账，否则下一次切换会被误判为忙碌。
// ===========================================================================

//! 超时、取消与失败路径的端到端用例。

use std::thread;
use std::time::{Duration, Instant};

use intent::{Budget, Cancel, Cause, Constraints, FailKind, Ledger, TraceId};

/// 示例追踪号；同一台账内复用会被判为重复，故每个用例各自新建台账。
const TRACE: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";

fn trace() -> TraceId {
    TraceId::parse(TRACE).expect("合法追踪号")
}

#[test]
fn budget_comes_from_the_constraints() {
    let mut rules = Constraints::default();
    assert_eq!(Budget::derive(&rules), None, "缺省表示不设预算");

    rules.deadline_ms = Some(250);
    let budget = Budget::derive(&rules).expect("约束里有预算");
    assert_eq!(budget.millis(), 250);
    assert_eq!(budget.limit(), Duration::from_millis(250));
}

#[test]
fn timeout_delivers_no_partial_result() {
    let start = Instant::now() - Duration::from_millis(6);
    let deadline = Budget::from_millis(5).start_at(start);
    assert!(deadline.expired());

    let answer = deadline.settle(String::from("半截输出"));
    let refusal = answer.refusal().expect("到期必须带超时记录");
    assert_eq!(refusal.status(), "timeout");
    assert_eq!(refusal.errno(), -110);
    assert!(!refusal.advice().is_empty());
    assert_eq!(refusal.detail(), "5");
    assert_eq!(answer.value(), None, "超时后不得交付任何部分结果");
}

#[test]
fn live_budget_delivers_the_whole_result() {
    let answer = Budget::from_millis(5_000)
        .start()
        .settle(String::from("完整结果"));
    assert_eq!(answer.value().as_deref(), Some("完整结果"));
}

#[test]
fn cancel_uses_only_the_closed_table() {
    let user = Cancel::new();
    user.request(Cause::User);
    assert_eq!(user.kind(), None, "用户取消不得自造状态串");

    let budget = Cancel::new();
    budget.request(Cause::Budget);
    assert_eq!(budget.kind(), Some(FailKind::Timeout));
}

#[test]
fn cancel_reaches_the_worker() {
    let cancel = Cancel::new();
    let worker = cancel.clone();
    let handle = thread::spawn(move || {
        while !worker.is_set() {
            thread::yield_now();
        }
        worker.cause()
    });

    cancel.request(Cause::Budget);
    assert_eq!(
        handle.join().expect("工作线程不 panic"),
        Some(Cause::Budget)
    );
}

#[test]
fn termination_frees_the_ledger() {
    let mut ledger = Ledger::new();
    ledger.begin(trace()).expect("空闲会话应受理");
    assert_eq!(ledger.held(), Some(trace()));

    let cancel = Cancel::new();
    cancel.request(Cause::Budget);
    assert_eq!(cancel.kind(), Some(FailKind::Timeout));

    ledger.finish(trace()).expect("取消后必须能归还台账");
    assert!(ledger.held().is_none(), "终止后会话应回到空闲");
}
