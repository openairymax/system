// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// airy-rt-cap 契约的样张测试
//
// 样张是 0.0.4 在目标机上的实测输出。三态标志里 unknown 必须原样保留：
// 把它读成 no 会让界面断言「内核不支持实时」，而真实原因只是这次没读到。
// ===========================================================================

//! 实时能力矩阵契约的样张测试。

use airy_adapters::rtcap::{Flag, Grade, Matrix};

const SAMPLE: &str = include_str!("golden/rtcap-matrix.json");

#[test]
fn matrix_fixture() {
    let matrix = Matrix::parse(SAMPLE.as_bytes()).expect("样张可解析");
    assert_eq!(matrix.grade, Grade::Soft);
    assert_eq!(matrix.caps.len(), 8);
    assert_eq!(matrix.pending.len(), 4);
}

#[test]
fn flags_keep_three_states() {
    let matrix = Matrix::parse(SAMPLE.as_bytes()).expect("样张可解析");
    let flags: Vec<Flag> = matrix.caps.iter().map(|cap| cap.flag).collect();
    assert_eq!(flags[0], Flag::No);
    assert_eq!(flags[1], Flag::Yes);
    assert_eq!(flags[6], Flag::Unknown);
}

#[test]
fn missing_condition_matches_pending() {
    let matrix = Matrix::parse(SAMPLE.as_bytes()).expect("样张可解析");
    let pending: Vec<&str> = matrix
        .caps
        .iter()
        .filter(|cap| cap.flag == Flag::No)
        .map(|cap| cap.name.as_str())
        .collect();
    assert_eq!(pending, matrix.pending);
}

#[test]
fn unknown_flag_is_refused() {
    let text = SAMPLE.replace("\"unknown\"", "\"maybe\"");
    let err = Matrix::parse(text.as_bytes()).expect_err("未知标志必须被拒绝");
    assert_eq!(err.status(), "invalid");
    assert!(err.detail().contains("maybe"));
}

#[test]
fn empty_payload_is_refused() {
    let err = Matrix::parse(b"{}").expect_err("缺字段必须被拒绝");
    assert_eq!(err.status(), "invalid");
    assert!(err.detail().contains("grade"));
}
