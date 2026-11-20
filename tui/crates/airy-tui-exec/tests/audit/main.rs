// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 审计行用例
//
// 用例只读公开面：由请求与结果生成记录，再按行内字段取值。覆盖契约 §3
// 的每个字段、审批拒绝的三个原因、输出折叠与两种截断，以及旧追踪号列。
// ===========================================================================

//! `airy_tui_exec::audit` 的行格式与字段归因。

use airy_tui_exec::approve::Refusal;
use airy_tui_exec::audit::{Entry, PREFIX, TEXT};
use airy_tui_exec::exec::{digest, Action, Request};
use airy_tui_exec::normalize::{Captured, Outcome};
use intent::TraceId;

const TRACE: &str = "0123456789abcdef0123456789abcdef";
const OLDER: &str = "fedcba9876543210fedcba9876543210";

fn trace(text: &str) -> TraceId {
    TraceId::parse(text).expect("合法追踪号")
}

fn request() -> Request {
    Action::find("disk.status")
        .expect("已登记动作")
        .request(trace(TRACE))
}

fn tokens(line: &str) -> Vec<&str> {
    line.split_whitespace().collect()
}

fn value<'a>(line: &'a str, key: &str) -> &'a str {
    let head = format!("{key}=");
    line.split_whitespace()
        .find_map(|word| word.strip_prefix(&head))
        .expect("字段必须存在")
}

#[test]
fn a_success_line_carries_every_contract_field() {
    let req = request();
    let line = Entry::of(&req, "console", Outcome::spawned(Some(0), trace(TRACE))).line();
    assert!(line.starts_with(PREFIX), "{line}");
    assert_eq!(tokens(&line).len(), 18, "{line}");
    assert_eq!(value(&line, "trace"), TRACE);
    assert_eq!(value(&line, "prev"), "-");
    assert_eq!(value(&line, "principal"), "console");
    assert_eq!(value(&line, "action"), "disk.status");
    assert_eq!(value(&line, "digest"), digest(&req));
    assert_eq!(value(&line, "status"), "ok");
    assert_eq!(value(&line, "errno"), "0");
    assert_eq!(value(&line, "code"), "0");
    assert_eq!(value(&line, "reason"), "-");
    assert_eq!(value(&line, "approval"), "-");
    assert_eq!(value(&line, "advice"), "-");
    assert_eq!(value(&line, "out"), "0");
    assert_eq!(value(&line, "out_cut"), "0");
    assert_eq!(value(&line, "err"), "0");
    assert_eq!(value(&line, "err_cut"), "0");
}

#[test]
fn the_hit_permit_file_lands_in_the_approval_column() {
    let mut outcome = Outcome::spawned(Some(0), trace(TRACE));
    outcome.approval = Some("disk.status.conf".to_owned());
    let line = Entry::of(&request(), "uid:1000", outcome).line();
    assert_eq!(value(&line, "approval"), "disk.status.conf");
    assert_eq!(value(&line, "principal"), "uid:1000");
}

#[test]
fn a_refused_request_names_its_reason_and_returns_eperm() {
    let outcome = Outcome::refused(Refusal::Expired, trace(TRACE));
    let line = Entry::of(&request(), "console", outcome).line();
    assert_eq!(value(&line, "status"), "approval-required");
    assert_eq!(value(&line, "errno"), "-13");
    assert_eq!(value(&line, "code"), "-");
    assert_eq!(value(&line, "reason"), "approval_expired");
    assert_eq!(value(&line, "approval"), "-");
    assert!(!value(&line, "advice").is_empty());
}

#[test]
fn output_with_whitespace_stays_on_one_line() {
    let mut outcome = Outcome::spawned(Some(0), trace(TRACE));
    outcome.stdout = Captured {
        text: "a b\nc\td%e".to_owned(),
        truncated: false,
    };
    let line = Entry::of(&request(), "console", outcome).line();
    assert_eq!(value(&line, "stdout"), "a%20b%0Ac%09d%25e");
    assert_eq!(value(&line, "stderr"), "-");
}

#[test]
fn control_bytes_become_percent_hex() {
    let mut outcome = Outcome::spawned(Some(0), trace(TRACE));
    outcome.stderr = Captured {
        text: "\u{7}boom".to_owned(),
        truncated: false,
    };
    let line = Entry::of(&request(), "console", outcome).line();
    assert_eq!(value(&line, "stderr"), "%07boom");
}

#[test]
fn long_output_is_clipped_and_flagged() {
    let text = "x".repeat(TEXT + 88);
    let mut outcome = Outcome::spawned(Some(0), trace(TRACE));
    outcome.stdout = Captured {
        text: text.clone(),
        truncated: false,
    };
    let line = Entry::of(&request(), "console", outcome).line();
    assert_eq!(value(&line, "out"), text.len().to_string());
    assert_eq!(value(&line, "out_cut"), "1");
    assert_eq!(value(&line, "stdout").len(), TEXT);
}

#[test]
fn a_pipe_truncation_is_flagged_without_clipping() {
    let mut outcome = Outcome::spawned(Some(0), trace(TRACE));
    outcome.stdout = Captured {
        text: "short".to_owned(),
        truncated: true,
    };
    let line = Entry::of(&request(), "console", outcome).line();
    assert_eq!(value(&line, "out"), "5");
    assert_eq!(value(&line, "out_cut"), "1");
    assert_eq!(value(&line, "stdout"), "short");
}

#[test]
fn a_reused_trace_can_carry_the_older_id() {
    let mut entry = Entry::of(
        &request(),
        "console",
        Outcome::spawned(Some(0), trace(TRACE)),
    );
    entry.prev = Some(trace(OLDER));
    let line = entry.line();
    assert_eq!(value(&line, "trace"), TRACE);
    assert_eq!(value(&line, "prev"), OLDER);
}

#[test]
fn an_empty_principal_falls_back_to_dash() {
    let line = Entry::of(&request(), "", Outcome::spawned(Some(0), trace(TRACE))).line();
    assert_eq!(value(&line, "principal"), "-");
}

#[test]
fn advice_with_spaces_is_folded() {
    let outcome = Outcome::failed(intent::FailKind::Unavailable, trace(TRACE));
    let line = Entry::of(&request(), "console", outcome).line();
    let advice = value(&line, "advice");
    assert!(!advice.contains(' '), "{advice}");
    assert_eq!(tokens(&line).len(), 18, "{line}");
}
