// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 网络三级状态面
//
// 引导期最常被问的一句是「网为什么没起来」。这里不重新探测，只把
// `net_doctor.sh` 写下的诊断行摊成设备、链路、地址三层：走到哪一层、
// 哪一层失败、失败时的线索，一眼能读完。三层之外不推断任何东西——
// 状态文件缺失或不成形就写「不可读」并带上原因，不显示成「就绪」。
//
// 末行是会话状态：读 `session_store` 留下的上一次会话记录。网络面与会话
// 面同屏，是因为维修现场往往同时问「网通不通」和「上次界面怎么退的」。
//
// SSoT: 0.0.5 方案 §6.1 S-04、§14.13 降级矩阵。
// ===========================================================================

//! 网络三级状态面与会话状态行。

use std::fs;
use std::path::Path;

use airy_adapters::net::{Firmware, Report, State, Tier};

use crate::session_store::{Record, State as SessState, Store};

/// 生产者写下的状态文件路径。
pub const FILE: &str = "/run/airymaxos/net.state";

/// 一帧网络面的内容。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Face {
    summary: String,
    rows: Vec<Row>,
    session: String,
    note: Option<String>,
}

/// 一层的一行。
#[derive(Clone, Debug, Eq, PartialEq)]
struct Row {
    tier: Tier,
    mark: Mark,
    detail: String,
}

/// 该层的判定记号。
#[repr(usize)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Mark {
    /// 已通过。
    Pass,
    /// 在本层失败。
    Fail,
    /// 前一层失败，未走到本层。
    Skip,
}

/// 记号的写法；顺序与 [`Mark`] 的判别值一致。
const MARKS: [&str; 3] = ["通过", "失败", "未到"];

/// 层名的写法；顺序与 [`Tier`] 的判别值一致。
const LABELS: [&str; 3] = ["设备", "链路", "地址"];

impl Face {
    /// 读系统路径与上一次会话记录。
    pub fn read() -> Self {
        let session = Store::system().load();
        Self::load(Path::new(FILE), session.as_ref())
    }

    /// 从指定路径读诊断行；文件缺失或损坏时整面退成一句说明。
    pub fn load(path: &Path, session: Option<&Record>) -> Self {
        fs::read_to_string(path)
            .map_err(|why| why.to_string())
            .and_then(|text| Report::parse(text.trim_end()).map_err(|why| why.detail().to_owned()))
            .map_or_else(
                |why| Self::failed(&why),
                |report| Self::of(&report, session),
            )
    }

    /// 按诊断读数排三层；会话状态单独一行。
    pub fn of(report: &Report, session: Option<&Record>) -> Self {
        Self {
            summary: format!("网络  {}", state_word(report.state)),
            rows: [Tier::Device, Tier::Link, Tier::Addr]
                .into_iter()
                .map(|tier| Row::of(report, tier))
                .collect(),
            session: session_line(session),
            note: None,
        }
    }

    /// 读不到状态文件时的整面说明。
    pub fn failed(why: &str) -> Self {
        Self {
            summary: String::new(),
            rows: Vec::new(),
            session: String::new(),
            note: Some(format!("网络不可读：{why}")),
        }
    }

    /// 渲染成若干行：结论、三层、会话。
    pub fn lines(&self) -> Vec<String> {
        self.note.clone().map_or_else(
            || {
                let mut lines = Vec::with_capacity(self.rows.len() + 2);
                lines.push(self.summary.clone());
                lines.extend(self.rows.iter().map(Row::line));
                lines.push(self.session.clone());
                lines
            },
            |note| vec![note],
        )
    }
}

impl Row {
    fn of(report: &Report, tier: Tier) -> Self {
        let mark = mark(report, tier);
        Self {
            tier,
            mark,
            detail: detail(report, tier, mark),
        }
    }

    /// 一行：层名、记号、线索。
    fn line(&self) -> String {
        format!(
            "{}  {}  {}",
            tier_label(self.tier),
            self.mark.word(),
            self.detail
        )
    }
}

/// 层名的中文写法；三字同宽，便于列对齐。
fn tier_label(tier: Tier) -> &'static str {
    LABELS[tier as usize]
}

impl Mark {
    fn word(self) -> &'static str {
        MARKS[self as usize]
    }
}

/// 结论的中文写法。
fn state_word(state: State) -> &'static str {
    match state {
        State::NoDevice => "无设备",
        State::NoFirmware => "缺固件",
        State::NoCarrier => "无载波",
        State::NoSsid => "未关联",
        State::DhcpTimeout => "地址超时",
        State::Ready => "就绪",
    }
}

/// 该层是否通过、失败或未走到。
fn mark(report: &Report, tier: Tier) -> Mark {
    if !report.reached(tier) {
        Mark::Skip
    } else if report.fail_tier() == Some(tier) {
        Mark::Fail
    } else {
        Mark::Pass
    }
}

/// 该层的线索；失败时优先用生产者给的 `hint`。
fn detail(report: &Report, tier: Tier, mark: Mark) -> String {
    match (tier, mark) {
        (Tier::Device, Mark::Fail) => device_fail(report),
        (Tier::Device, Mark::Pass) => device_ok(report),
        (Tier::Link, Mark::Fail) => hint(report, "链路未建立"),
        (Tier::Link, Mark::Pass) => named(report, "已建立"),
        (Tier::Addr, Mark::Fail) => hint(report, "未取得地址"),
        (Tier::Addr, Mark::Pass) => hint(report, "已取得地址"),
        (_, Mark::Skip) => "未到".to_owned(),
    }
}

/// 设备层失败：缺固件写路径，缺设备写线索。
fn device_fail(report: &Report) -> String {
    match &report.fw {
        Firmware::Missing(path) => format!("固件缺失 {path}"),
        _ => hint(report, "无网络设备"),
    }
}

/// 设备层通过：网卡名带驱动。
fn device_ok(report: &Report) -> String {
    report.dev.as_ref().map_or_else(
        || "设备就绪".to_owned(),
        |dev| match &report.driver {
            Some(driver) => format!("{dev} ({driver})"),
            None => dev.clone(),
        },
    )
}

/// 生产者线索优先，缺失时用兜底句。
fn hint(report: &Report, fallback: &str) -> String {
    report.hint.clone().unwrap_or_else(|| fallback.to_owned())
}

/// 带网卡名的通过句。
fn named(report: &Report, fallback: &str) -> String {
    report
        .dev
        .as_ref()
        .map_or_else(|| fallback.to_owned(), |dev| format!("{dev} {fallback}"))
}

/// 会话行的中文写法。
fn session_line(session: Option<&Record>) -> String {
    session.map_or_else(|| "会话  无记录".to_owned(), session_of)
}

/// 有记录时按结果与备注拼一行。
fn session_of(record: &Record) -> String {
    let word = match record.state {
        SessState::Ready => "进行中",
        SessState::Done => "已退出",
        SessState::Fallback => "异常回落",
    };
    if record.note.is_empty() {
        format!("会话  {word}")
    } else {
        format!("会话  {word} · {}", record.note)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session_store::{Record, State as SessState};

    fn report(state: State, dev: Option<&str>, hint: Option<&str>) -> Report {
        Report {
            state,
            dev: dev.map(str::to_owned),
            driver: dev.map(|_| "iwlwifi".to_owned()),
            fw: Firmware::Ok,
            rfkill: 0,
            hint: hint.map(str::to_owned),
        }
    }

    #[test]
    fn a_ready_report_passes_all_three_tiers() {
        let face = Face::of(
            &report(State::Ready, Some("wlan0"), Some("addr=10.0.0.2")),
            None,
        );
        let lines = face.lines();
        assert_eq!(lines.len(), 5);
        assert!(lines[0].contains("就绪"));
        assert!(lines[1].contains("设备") && lines[1].contains("通过"));
        assert!(lines[3].contains("addr=10.0.0.2"));
        assert!(lines[4].contains("无记录"));
    }

    #[test]
    fn a_link_failure_marks_the_device_up_and_the_address_unreached() {
        let face = Face::of(
            &report(State::NoSsid, Some("wlan0"), Some("scan=empty")),
            None,
        );
        let lines = face.lines();
        assert!(lines[1].contains("通过"), "{:?}", lines);
        assert!(lines[2].contains("失败") && lines[2].contains("scan=empty"));
        assert!(lines[3].contains("未到"), "{:?}", lines);
    }

    #[test]
    fn a_missing_firmware_shows_the_path_on_the_device_row() {
        let mut base = report(State::NoFirmware, Some("wlan0"), None);
        base.fw = Firmware::Missing("iwlwifi-9000.ucode".to_owned());
        let lines = Face::of(&base, None).lines();
        assert!(
            lines[1].contains("固件缺失 iwlwifi-9000.ucode"),
            "{:?}",
            lines
        );
    }

    #[test]
    fn an_unreadable_file_becomes_one_sentence() {
        assert_eq!(
            Face::failed("没有那个文件").lines(),
            vec!["网络不可读：没有那个文件"]
        );
    }

    #[test]
    fn the_session_record_shows_its_outcome_and_note() {
        let record = Record {
            state: SessState::Fallback,
            note: "frame panic".to_owned(),
        };
        let lines = Face::of(&report(State::Ready, Some("wlan0"), None), Some(&record)).lines();
        assert!(lines[4].contains("异常回落"), "{:?}", lines);
        assert!(lines[4].contains("frame panic"));
    }

    #[test]
    fn a_device_without_an_address_says_so_on_the_address_row() {
        let lines = Face::of(&report(State::DhcpTimeout, Some("eth0"), None), None).lines();
        assert!(lines[0].contains("地址超时"));
        assert!(lines[1].contains("通过"));
        assert!(lines[2].contains("通过"));
        assert!(lines[3].contains("失败") && lines[3].contains("未取得地址"));
    }

    #[test]
    fn the_summary_never_calls_an_empty_device_ready() {
        let lines = Face::of(&report(State::NoDevice, None, Some("sysfs=absent")), None).lines();
        assert!(lines[0].contains("无设备"));
        assert!(lines[1].contains("失败") && lines[1].contains("sysfs=absent"));
    }

    #[test]
    fn a_tier_is_only_unreached_after_the_failed_one() {
        let report = report(State::NoCarrier, Some("eth0"), None);
        assert!(report.reached(Tier::Device));
        assert!(report.reached(Tier::Link));
        assert!(!report.reached(Tier::Addr));
    }
}
