// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// golden 样张对读
//
// 样张是 0.0.4 契约的实际输出，类型若与样张脱节，本组用例先失败：
// 字段名、枚举 token 与层级都按样张断言，不看文档描述。样张来源见
// disk.rs 文件头。
// ===========================================================================

//! 用 golden 样张锁定 disk 契约的消费侧类型。

use airy_adapters::disk::{
    Bus, Fs, Health, HealthState, InstallState, Layout, LayoutKind, LayoutState, Role, Status,
    Table, Topo, Verdict, SCHEMA_VERSION,
};
use intent::FailKind;

fn read<T: serde::de::DeserializeOwned>(name: &str) -> T {
    let text = match name {
        "status" => include_str!("golden/disk-status.json"),
        "parts" => include_str!("golden/disk-status-parts.json"),
        "layout" => include_str!("golden/disk-layout.json"),
        "health" => include_str!("golden/disk-health.json"),
        other => panic!("未知样张 {other}"),
    };
    serde_json::from_str(text).unwrap_or_else(|err| panic!("{name} 与类型不符：{err}"))
}

#[test]
fn status_fixture() {
    let status: Status = read("status");
    assert_eq!(status.schema, 1);
    assert_eq!(status.kind, Topo::Multi);
    assert_eq!(status.disks.len(), 4);
    let first = &status.disks[0];
    assert_eq!(first.name, "sda");
    assert_eq!(first.size, 758_038_528);
    assert_eq!(first.bus, Bus::Other);
    assert_eq!(first.table, Table::None);
    assert!(first.ro);
    assert!(!first.removable);
    assert_eq!(first.model, "Virtual Disk");
    assert_eq!(first.serial, "");
    assert!(first.parts.is_empty());
    assert_eq!(status.install.state, InstallState::Absent);
    assert_eq!(status.install.verdict, Verdict::Absent);
    assert!(status.install.diff.is_empty());
    assert_eq!(status.install.bytes, 0);
}

#[test]
fn parts_fixture() {
    let status: Status = read("parts");
    assert_eq!(status.kind, Topo::Single);
    let disk = &status.disks[0];
    assert_eq!(disk.bus, Bus::Virtio);
    assert_eq!(disk.table, Table::Gpt);
    assert_eq!(disk.parts.len(), 2);
    let root = &disk.parts[0];
    assert_eq!(root.name, "sdd1");
    assert_eq!(root.fs, Fs::Ext4);
    assert_eq!(root.mount.as_deref(), Some("/"));
    assert_eq!(root.label.as_deref(), Some("AIRYROOT"));
    assert_eq!(root.role, Role::System);
    assert_eq!(disk.parts[1].role, Role::Data);
    assert_eq!(disk.parts[1].mount.as_deref(), Some("/data"));
    assert_eq!(status.install.verdict, Verdict::Match);
    assert_eq!(status.install.disk, "sdd");
}

#[test]
fn layout_fixture() {
    let layout: Layout = read("layout");
    assert_eq!(layout.kind, LayoutKind::None);
    assert_eq!(layout.verdict, LayoutState::NoRoot);
    assert_eq!(layout.root, "");
    assert_eq!(layout.data, "");
}

#[test]
fn health_fixture() {
    let health: Health = read("health");
    assert_eq!(health.verdict, HealthState::NoData);
    assert_eq!(health.part, "");
    assert_eq!((health.total, health.free, health.used), (0, 0, 0));
    assert!(health.advice.contains("/data"));
}

#[test]
fn parse_follows_the_samples() {
    let status = Status::parse(include_str!("golden/disk-status.json").as_bytes())
        .expect("status 样张可解析");
    assert_eq!(status.schema, SCHEMA_VERSION);
    let layout = Layout::parse(include_str!("golden/disk-layout.json").as_bytes())
        .expect("layout 样张可解析");
    assert_eq!(layout.verdict, LayoutState::NoRoot);
    let health = Health::parse(include_str!("golden/disk-health.json").as_bytes())
        .expect("health 样张可解析");
    assert_eq!(health.verdict, HealthState::NoData);
}

#[test]
fn parse_rejects_schema() {
    let text = include_str!("golden/disk-status.json").replace("\"schema\": 1", "\"schema\": 2");
    let err = Status::parse(text.as_bytes()).expect_err("2 不是本版契约");
    assert_eq!(err.kind(), FailKind::Unsupported);
    assert_eq!(err.status(), "unsupported");
    assert_eq!(err.errno(), -22);
    assert_eq!(err.detail(), "2");
}

#[test]
fn parse_rejects_garbage() {
    let err = Status::parse(b"{}").expect_err("缺字段必须拒绝");
    assert_eq!(err.kind(), FailKind::Invalid);
    assert_eq!(err.status(), "invalid");
    assert!(err.detail().contains("schema"), "附加字段要指出首个缺项");
    assert!(!err.advice().is_empty());
}
