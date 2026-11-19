// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// airy-probe 契约的样张测试
//
// 样张是 0.0.4 在目标机上的实测输出，字段一个不改。测试先钉住每个字段的
// 读法，再验证读不懂的 token 与缺字段都被拒绝——探针是排障入口，把新原因
// 静默显示成旧原因比直接报错更危险。
// ===========================================================================

//! 探针契约的样张测试。

use airy_adapters::probe::{Avail, Gap, Mode, Report, State, Why};

const SAMPLE: &str = include_str!("golden/probe-status.json");

#[test]
fn report_fixture() {
    let report = Report::parse(SAMPLE.as_bytes()).expect("样张可解析");
    assert_eq!(report.probe, "airy-probe");
    assert_eq!(report.version, "0.0.4");
    assert_eq!(report.mode, Mode::Status);
    assert_eq!(report.state, State::Blocked);
    assert_eq!(report.readable, 0);
    assert_eq!(report.total, 10);
    assert_eq!(report.events.len(), 10);
    assert_eq!(report.missing.len(), 10);
    assert!(!report.witnessed);
}

#[test]
fn events_keep_the_reason() {
    let report = Report::parse(SAMPLE.as_bytes()).expect("样张可解析");
    let event = &report.events[3];
    assert_eq!(event.name, "airy_ipc_send");
    assert_eq!(event.state, Avail::Unavailable);
    assert_eq!(event.reason, Some(Why::Missing));
    assert_eq!(event.id, None);
}

#[test]
fn channels_keep_the_mode() {
    let report = Report::parse(SAMPLE.as_bytes()).expect("样张可解析");
    let channel = &report.channels[0];
    assert_eq!(channel.name, "policy");
    assert_eq!(channel.path, "/sys/kernel/security/airy/policy");
    assert_eq!(channel.mode, None);
    assert!(!channel.present);
    assert_eq!(channel.reason, Some(Why::Missing));
}

#[test]
fn stats_follow_the_policy() {
    let report = Report::parse(SAMPLE.as_bytes()).expect("样张可解析");
    assert_eq!(report.stats.policy, Avail::Unavailable);
    assert_eq!(report.stats.policy_reason, Some(Gap::Missing));
    assert_eq!(report.stats.cap_used, None);
    assert!(report.stats.tables.is_empty());
    assert!(report.attempts.is_empty());
    assert!(report.samples.is_empty());
}

#[test]
fn unknown_state_is_refused() {
    let text = SAMPLE.replace("\"blocked\"", "\"bogus\"");
    let err = Report::parse(text.as_bytes()).expect_err("未知结论必须被拒绝");
    assert_eq!(err.status(), "invalid");
    assert!(err.detail().contains("bogus"));
}

#[test]
fn empty_payload_is_refused() {
    let err = Report::parse(b"{}").expect_err("缺字段必须被拒绝");
    assert_eq!(err.status(), "invalid");
    assert!(err.detail().contains("probe"));
}
