// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// airy 网络诊断行的消费侧类型
//
// 生产者是 system/scripts/net_doctor.sh：一次诊断压成一行写进
// `/run/airymaxos/net.state`，同时打进 journal——
//   AIRYMAX-NET state=<token> dev=<ifname|-> driver=<mod|->
//               fw=<ok|missing:path|-> rfkill=<n> hint=<text|->
// 字段恒定、顺序恒定，空白一律由生产者压成下划线，所以这里按空白切词。
//
// 本层只做两件事：把这一行拆成字段，再把结论归到三级——设备、链路、
// 地址。三级是诊断顺序，不是新发明的分层：前一级不通过，后一级不可能
// 成立，据此视图才能把「未到」与「失败」分开写。
//
// 解析从严：六个键缺一即拒绝，未知键、重复键、未知 token 同样拒绝；
// 缺字段时不补默认值，避免把损坏的状态文件读成「就绪」。
//
// SSoT: system/scripts/net_doctor.sh 头部的输出格式段、0.0.5 方案 §6.1 S-04。
// ===========================================================================

//! `net_doctor.sh` 诊断行的消费侧类型与三级归类。

use intent::Refusal;

/// 行首标记；生产者每一行都以它开头。
pub const PREFIX: &str = "AIRYMAX-NET";

/// 六个字段的键序；与 [`Fields`] 的槽位一一对应。
const KEYS: [&str; 6] = ["state", "dev", "driver", "fw", "rfkill", "hint"];

/// token 与结论写在同一条目里；解析只读这张表，不另设平行数组。
const TOKENS: [(&str, State); 6] = [
    ("no-device", State::NoDevice),
    ("no-firmware", State::NoFirmware),
    ("no-carrier", State::NoCarrier),
    ("no-ssid", State::NoSsid),
    ("dhcp-timeout", State::DhcpTimeout),
    ("ok", State::Ready),
];

/// 三级顺序；数组下标即判别值。
const RANKS: [u8; 3] = [0, 1, 2];

/// 诊断结论；token 与 `net_doctor.sh` 的 `state` 一一对应。
#[repr(usize)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum State {
    /// 没有网络设备。
    NoDevice,
    /// 设备在，但没有可用固件。
    NoFirmware,
    /// 设备在，载波未建立。
    NoCarrier,
    /// 无线设备未关联到任何 SSID。
    NoSsid,
    /// 载波已建立，但没有非 link-local 地址。
    DhcpTimeout,
    /// 有设备拿到地址。
    Ready,
}

/// 三级判据：设备、链路、地址。
#[repr(usize)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Tier {
    /// 设备与驱动是否就绪。
    Device,
    /// 载波与关联是否建立。
    Link,
    /// 是否取得可用地址。
    Addr,
}

/// 固件状态；缺失时保留生产者给出的路径。
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum Firmware {
    /// 固件齐备。
    Ok,
    /// 固件缺失，值为探测到的路径。
    Missing(String),
    /// 生产者未能判断。
    Unknown,
}

/// 一次诊断的完整读数。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Report {
    /// 诊断结论。
    pub state: State,
    /// 聚焦的网卡名；没有设备时为 `None`。
    pub dev: Option<String>,
    /// 驱动模块名；未知时为 `None`。
    pub driver: Option<String>,
    /// 固件状态。
    pub fw: Firmware,
    /// 软/硬阻塞计数之和。
    pub rfkill: u32,
    /// 生产者给出的定位线索；无附加信息时为 `None`。
    pub hint: Option<String>,
}

impl Report {
    /// 解析一整行；任何缺键、重复键或未知 token 都拒绝。
    pub fn parse(line: &str) -> Result<Self, Refusal> {
        let mut words = line.split_whitespace();
        if words.next() != Some(PREFIX) {
            return Err(Refusal::invalid(PREFIX));
        }
        let mut fields = Fields::default();
        for word in words {
            let (key, value) = word.split_once('=').ok_or_else(|| Refusal::invalid(word))?;
            fields.set(key, value)?;
        }
        fields.finish()
    }

    /// 失败所在层级；就绪时没有失败层。
    pub fn fail_tier(&self) -> Option<Tier> {
        match self.state {
            State::NoDevice | State::NoFirmware => Some(Tier::Device),
            State::NoCarrier | State::NoSsid => Some(Tier::Link),
            State::DhcpTimeout => Some(Tier::Addr),
            State::Ready => None,
        }
    }

    /// 该层是否已经走到；就绪时三层都算走到。
    pub fn reached(&self, tier: Tier) -> bool {
        self.fail_tier()
            .is_none_or(|failed| tier.rank() <= failed.rank())
    }
}

impl State {
    /// 只认生产者写出的六个 token。
    pub fn parse(token: &str) -> Result<Self, Refusal> {
        TOKENS
            .iter()
            .find(|(known, _)| *known == token)
            .map(|(_, state)| *state)
            .ok_or_else(|| Refusal::invalid(token))
    }
}

impl Tier {
    /// 诊断顺序：设备先于链路，链路先于地址。
    fn rank(self) -> u8 {
        RANKS[self as usize]
    }
}

/// 六个字段的暂存；重复键在写入时即拒绝。
#[derive(Default)]
struct Fields {
    slots: [Option<String>; 6],
}

impl Fields {
    fn set(&mut self, key: &str, value: &str) -> Result<(), Refusal> {
        let index = KEYS
            .iter()
            .position(|known| *known == key)
            .ok_or_else(|| Refusal::invalid(key))?;
        let slot = &mut self.slots[index];
        if slot.is_some() {
            return Err(Refusal::invalid(key));
        }
        *slot = Some(value.to_owned());
        Ok(())
    }

    fn finish(self) -> Result<Report, Refusal> {
        let [state, dev, driver, fw, rfkill, hint] = take(self.slots)?;
        Ok(Report {
            state: State::parse(&state)?,
            dev: named(dev),
            driver: named(driver),
            fw: firmware(&fw)?,
            rfkill: rfkill.parse().map_err(|_| Refusal::invalid("rfkill"))?,
            hint: named(hint),
        })
    }
}

/// 六槽齐备才放行；缺哪一个就用键名报错。
fn take(slots: [Option<String>; 6]) -> Result<[String; 6], Refusal> {
    let mut out: [String; 6] = std::array::from_fn(|_| String::new());
    for (index, slot) in slots.into_iter().enumerate() {
        out[index] = slot.ok_or_else(|| Refusal::invalid(KEYS[index]))?;
    }
    Ok(out)
}

/// 生产者的空值记号 `-` 折成 `None`。
fn named(value: String) -> Option<String> {
    (value != "-").then_some(value)
}

/// 固件字段：`ok`、`-` 或 `missing:<path>`。
fn firmware(value: &str) -> Result<Firmware, Refusal> {
    match value {
        "ok" => Ok(Firmware::Ok),
        "-" => Ok(Firmware::Unknown),
        _ => missing(value),
    }
}

/// `missing:` 必须有非空路径。
fn missing(value: &str) -> Result<Firmware, Refusal> {
    value
        .strip_prefix("missing:")
        .filter(|path| !path.is_empty())
        .map(|path| Firmware::Missing(path.to_owned()))
        .ok_or_else(|| Refusal::invalid(value))
}

#[cfg(test)]
mod tests {
    use super::{Firmware, Report, State, Tier};

    const OK: &str =
        "AIRYMAX-NET state=ok dev=wlan0 driver=iwlwifi fw=ok rfkill=0 hint=addr=192.168.1.9";

    #[test]
    fn a_ready_line_keeps_every_field() {
        let report = Report::parse(OK).expect("样张可解析");
        assert_eq!(report.state, State::Ready);
        assert_eq!(report.dev.as_deref(), Some("wlan0"));
        assert_eq!(report.driver.as_deref(), Some("iwlwifi"));
        assert_eq!(report.fw, Firmware::Ok);
        assert_eq!(report.rfkill, 0);
        assert_eq!(report.hint.as_deref(), Some("addr=192.168.1.9"));
        assert_eq!(report.fail_tier(), None);
        assert!(report.reached(Tier::Addr));
    }

    #[test]
    fn a_failure_lands_on_its_tier_and_later_tiers_are_unreached() {
        let line = "AIRYMAX-NET state=no-ssid dev=wlan0 driver=- fw=ok rfkill=0 hint=scan=empty";
        let report = Report::parse(line).expect("样张可解析");
        assert_eq!(report.fail_tier(), Some(Tier::Link));
        assert!(report.reached(Tier::Device));
        assert!(report.reached(Tier::Link));
        assert!(!report.reached(Tier::Addr));
        assert_eq!(report.driver, None);
    }

    #[test]
    fn a_missing_firmware_keeps_the_path() {
        let line =
            "AIRYMAX-NET state=no-firmware dev=wlan0 driver=iwlwifi fw=missing:iwlwifi-9000.ucode rfkill=0 hint=-";
        let report = Report::parse(line).expect("样张可解析");
        assert_eq!(
            report.fw,
            Firmware::Missing("iwlwifi-9000.ucode".to_owned())
        );
        assert_eq!(report.fail_tier(), Some(Tier::Device));
        assert_eq!(report.hint, None);
    }

    #[test]
    fn a_missing_field_is_rejected_by_name() {
        let line = "AIRYMAX-NET state=ok dev=wlan0 driver=iwlwifi fw=ok rfkill=0";
        let error = Report::parse(line).expect_err("缺 hint 必须拒绝");
        assert_eq!(error.detail(), "hint");
    }

    #[test]
    fn a_duplicate_field_is_rejected() {
        let line = "AIRYMAX-NET state=ok dev=wlan0 dev=eth0 driver=- fw=ok rfkill=0 hint=-";
        let error = Report::parse(line).expect_err("重复 dev 必须拒绝");
        assert_eq!(error.detail(), "dev");
    }

    #[test]
    fn an_unknown_field_is_rejected() {
        let line = "AIRYMAX-NET state=ok dev=wlan0 driver=- fw=ok rfkill=0 hint=- extra=1";
        let error = Report::parse(line).expect_err("未知键必须拒绝");
        assert_eq!(error.detail(), "extra");
    }

    #[test]
    fn an_unknown_state_is_rejected() {
        assert!(State::parse("pending").is_err());
        assert!(State::parse("OK").is_err());
    }

    #[test]
    fn a_bad_rfkill_count_is_rejected() {
        let line = "AIRYMAX-NET state=ok dev=wlan0 driver=- fw=ok rfkill=soft hint=-";
        let error = Report::parse(line).expect_err("rfkill 非数字必须拒绝");
        assert_eq!(error.detail(), "rfkill");
    }

    #[test]
    fn a_broken_firmware_word_is_rejected() {
        let line = "AIRYMAX-NET state=ok dev=wlan0 driver=- fw=missing: rfkill=0 hint=-";
        let error = Report::parse(line).expect_err("空路径必须拒绝");
        assert_eq!(error.detail(), "missing:");
    }

    #[test]
    fn another_producer_line_is_rejected() {
        let error =
            Report::parse("AIRYMAX-NET-READY ipv4=192.168.1.9").expect_err("别的行不属于本契约");
        assert_eq!(error.detail(), "AIRYMAX-NET");
    }
}
