// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 追踪轨迹视图
//
// 十点追踪是取证面的最小可判定集合：每个事件是否可读、事件号是多少、当前
// 是否启用，都直接决定「开机卡住时能不能定位」。这里只把 `airy-probe
// status --json` 的 `events[]` 按原序排成行，不读 `/sys/kernel/tracing`，
// 也不猜测缺失事件的启用位——读不到就写成不可读并带上原因。
//
// 事件名来自内核 TRACE_EVENT，长度不一；名字列取本面最宽者并夹到上限，
// 保证后续列对齐又不把行撑出终端。列宽统一走 `wcwidth`，不另立算法。
//
// SSoT: 0.0.5 方案 §2.3 探针契约、§14.13 降级矩阵。
// ===========================================================================

//! 追踪轨迹视图：每个追踪事件的可读性、事件号与启用位。

use airy_adapters::probe::{Avail, Event, Why};

use crate::wcwidth::display_width;

/// 名字列的最小宽度。
const NAME_MIN: usize = 8;

/// 名字列的最大宽度；超出按终端截断，不挤掉后续列。
const NAME_MAX: usize = 24;

/// 一帧轨迹面的内容。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct View {
    rows: Vec<Row>,
    note: Option<String>,
}

/// 一个追踪事件的一行。
#[derive(Clone, Debug, Eq, PartialEq)]
struct Row {
    name: String,
    id: Option<u32>,
    enabled: Option<bool>,
    state: Avail,
    reason: Option<Why>,
}

impl View {
    /// 按契约原序收下事件表。
    pub fn of(events: &[Event]) -> Self {
        Self {
            rows: events.iter().map(Row::of).collect(),
            note: None,
        }
    }

    /// 读不到事件表时的整面说明。
    pub fn failed(why: &str) -> Self {
        Self {
            rows: Vec::new(),
            note: Some(format!("追踪不可读：{why}")),
        }
    }

    /// 渲染成若干行；顺序与契约一致。
    pub fn lines(&self) -> Vec<String> {
        if let Some(note) = &self.note {
            return vec![note.clone()];
        }
        if self.rows.is_empty() {
            return vec!["追踪事件列表为空".to_owned()];
        }
        let width = self.name_width();
        self.rows.iter().map(|row| row.line(width)).collect()
    }

    /// 名字列宽度：最宽事件名夹到上下限。
    fn name_width(&self) -> usize {
        let widest = self
            .rows
            .iter()
            .map(|row| display_width(&row.name))
            .max()
            .unwrap_or(NAME_MIN);
        widest.clamp(NAME_MIN, NAME_MAX)
    }
}

impl Row {
    fn of(event: &Event) -> Self {
        Self {
            name: event.name.clone(),
            id: event.id,
            enabled: event.enabled,
            state: event.state,
            reason: event.reason,
        }
    }

    /// 一行：名字、事件号、启用位、可读性。
    fn line(&self, width: usize) -> String {
        format!(
            "{} {:>4} {:>2} {}",
            pad(&self.name, width),
            id_word(self.id),
            on_off(self.enabled),
            state_word(self.state, self.reason),
        )
    }
}

/// 按列补空格；宽度口径与截断一致。
fn pad(name: &str, width: usize) -> String {
    let mut out = String::from(name);
    out.extend(std::iter::repeat_n(
        ' ',
        width.saturating_sub(display_width(name)),
    ));
    out
}

/// 事件号的文字；不可读时不冒充 0。
fn id_word(id: Option<u32>) -> String {
    id.map_or_else(|| "-".to_owned(), |value| value.to_string())
}

/// 启用位的文字。
fn on_off(enabled: Option<bool>) -> &'static str {
    match enabled {
        Some(true) => "开",
        Some(false) => "关",
        None => "-",
    }
}

/// 可读性的文字；不可读必须带原因。
fn state_word(state: Avail, reason: Option<Why>) -> String {
    match (state, reason) {
        (Avail::Readable, _) => "可读".to_owned(),
        (Avail::Unavailable, Some(why)) => format!("不可读·{}", why_word(why)),
        (Avail::Unavailable, None) => "不可读".to_owned(),
    }
}

/// 不可读原因的词。
fn why_word(why: Why) -> &'static str {
    match why {
        Why::Missing => "缺失",
        Why::Denied => "受限",
        Why::Malformed => "损坏",
    }
}

#[cfg(test)]
mod tests {
    use super::{why_word, View};
    use airy_adapters::probe::{Avail, Event, Why};

    fn event(name: &str, id: Option<u32>, enabled: Option<bool>, reason: Option<Why>) -> Event {
        Event {
            name: name.to_owned(),
            id,
            enabled,
            state: if reason.is_some() {
                Avail::Unavailable
            } else {
                Avail::Readable
            },
            reason,
        }
    }

    #[test]
    fn a_readable_event_shows_its_id_and_enable_bit() {
        let view = View::of(&[event("airy_ipc_send", Some(12), Some(true), None)]);
        let lines = view.lines();
        assert_eq!(lines.len(), 1);
        assert!(lines[0].contains("airy_ipc_send"));
        assert!(lines[0].contains("12"));
        assert!(lines[0].trim_end().ends_with("开 可读"));
    }

    #[test]
    fn an_unreadable_event_spells_out_the_reason() {
        let view = View::of(&[event("airy_sched", None, None, Some(Why::Denied))]);
        let line = &view.lines()[0];
        assert!(line.contains("不可读·受限"), "{line}");
        assert!(line.contains(" -  "), "未知事件号不得写成 0：{line}");
    }

    #[test]
    fn rows_keep_the_contract_order() {
        let view = View::of(&[
            event("a", Some(1), Some(false), None),
            event("b", Some(2), Some(true), None),
        ]);
        let lines = view.lines();
        assert!(lines[0].starts_with('a'));
        assert!(lines[1].starts_with('b'));
    }

    #[test]
    fn names_are_padded_to_one_column() {
        let view = View::of(&[
            event("short", Some(1), Some(true), None),
            event("a_much_longer_name", Some(2), Some(false), None),
        ]);
        let lines = view.lines();
        let first = lines[0].find("   1 ").expect("事件号列对齐");
        let second = lines[1].find("   2 ").expect("事件号列对齐");
        assert_eq!(first, second);
    }

    #[test]
    fn a_failed_read_becomes_a_sentence() {
        let view = View::failed("airy-probe 未安装");
        assert_eq!(view.lines(), vec!["追踪不可读：airy-probe 未安装"]);
    }

    #[test]
    fn an_empty_report_says_so_instead_of_rendering_nothing() {
        assert_eq!(View::of(&[]).lines(), vec!["追踪事件列表为空"]);
    }

    #[test]
    fn every_reason_has_its_own_word() {
        assert_eq!(why_word(Why::Missing), "缺失");
        assert_eq!(why_word(Why::Denied), "受限");
        assert_eq!(why_word(Why::Malformed), "损坏");
    }
}
