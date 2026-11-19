// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 模型契约样张对读
//
// 样张是 0.0.4 在无模型清单的运行根上实际打印的 JSON：状态为 failed、
// 原因为 no-manifest。就绪与降级的 token 由 0.0.4 的状态枚举给出。
// ===========================================================================

//! 用 golden 样张锁定 model 契约的消费侧类型。

use airy_adapters::model::{Backend, Reason, State, Status};
use intent::FailKind;

const SAMPLE: &str = include_str!("golden/model-status.json");

#[test]
fn status_fixture() {
    let status = Status::parse(SAMPLE.as_bytes()).expect("样张可解析");
    assert_eq!(status.state, State::Failed);
    assert_eq!(status.backend, Backend::Cpu);
    assert_eq!(status.reason, Some(Reason::NoManifest));
    assert_eq!(status.digest, "");
}

#[test]
fn reason_is_optional() {
    let text = SAMPLE.replace("\"no-manifest\"", "null");
    let status = Status::parse(text.as_bytes()).expect("就绪态不带原因");
    assert_eq!(status.reason, None);
}

#[test]
fn unknown_reason_is_refused() {
    let text = SAMPLE.replace("\"no-manifest\"", "\"no-such\"");
    let err = Status::parse(text.as_bytes()).expect_err("枚举外的原因必须拒绝");
    assert_eq!(err.kind(), FailKind::Invalid);
    assert_eq!(err.status(), "invalid");
    assert!(err.detail().contains("no-such"), "附加字段要写命中的取值");
}
