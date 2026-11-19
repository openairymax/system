// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 会话流模型
//
// 屏幕上的每一行都要回答三个问题：谁说的、什么时候说的、说了什么。这里
// 把这三件事做成一条条目，而不是让调用方各自拼字符串：角色与时间只有
// 一份来源，换行与缩进只在渲染时做一次。条目正文允许多行，续行缩进两列，
// 一眼能看出它属于上一条而不是新的一条。
//
// 时间是会话内相对时间，不是墙上时钟：引导期没有可靠时区，取绝对时间
// 只会在同一台机器上显示出两种口径。超过一小时才显示小时位，短会话不
// 浪费两列。
//
// 条目总数有上限，超出后丢最旧一条；会话视图另有自己的行数上限，两者
// 互不覆盖——前者管模型，后者管屏幕。
//
// SSoT: 0.0.5 方案 §14.2（帧预算）、§14.5（输入抽象）。
// ===========================================================================

//! 会话流：角色、会话内时间与文本行的唯一成形处。

use std::collections::VecDeque;
use std::time::Duration;

/// 保留的条目上限；超出后从最旧一条丢弃。
pub const LIMIT: usize = 2000;

/// 会话内时间，精度到秒。
#[derive(Clone, Copy, Debug, Default, Eq, Ord, PartialEq, PartialOrd)]
pub struct Stamp {
    secs: u64,
}

impl Stamp {
    /// 按秒数取时间。
    pub fn from_secs(secs: u64) -> Self {
        Self { secs }
    }

    /// 按已过去的时长取时间；不足一秒向下取整。
    pub fn of(elapsed: Duration) -> Self {
        Self::from_secs(elapsed.as_secs())
    }

    /// 显示文本；一小时以内为 `MM:SS`，之后带小时位。
    pub fn text(&self) -> String {
        let hour = self.secs / 3600;
        let minute = self.secs % 3600 / 60;
        let second = self.secs % 60;
        if hour == 0 {
            format!("{minute:02}:{second:02}")
        } else {
            format!("{hour}:{minute:02}:{second:02}")
        }
    }
}

/// 一条消息的发出方。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Role {
    /// 系统自身的提示与状态。
    System,
    /// 用户输入。
    User,
    /// 会话助手。
    Assist,
    /// 执行面的输出。
    Exec,
}

impl Role {
    /// 屏幕上的角色标签。
    pub fn label(self) -> &'static str {
        match self {
            Self::System => "系统",
            Self::User => "你",
            Self::Assist => "极境",
            Self::Exec => "执行",
        }
    }
}

/// 一条会话消息。
struct Entry {
    at: Stamp,
    role: Role,
    text: String,
}

impl Entry {
    /// 首行带角色与时间，续行缩进。
    fn lines(&self) -> Vec<String> {
        let mut body = self.text.lines();
        let Some(first) = body.next() else {
            return Vec::new();
        };
        let mut out = vec![format!(
            "[{}] {} · {first}",
            self.at.text(),
            self.role.label()
        )];
        for line in body {
            out.push(format!("  {line}"));
        }
        out
    }
}

/// 一段会话的消息序列。
pub struct Session {
    entries: VecDeque<Entry>,
}

impl Session {
    /// 空会话。
    pub fn new() -> Self {
        Self {
            entries: VecDeque::new(),
        }
    }

    /// 追加一条消息，返回它成形后的文本行，供视图直接上屏。
    pub fn push(&mut self, at: Stamp, role: Role, text: impl Into<String>) -> Vec<String> {
        let entry = Entry {
            at,
            role,
            text: text.into(),
        };
        let lines = entry.lines();
        self.entries.push_back(entry);
        if self.entries.len() > LIMIT {
            self.entries.pop_front();
        }
        lines
    }

    /// 全部消息的文本行，按到达顺序。
    pub fn lines(&self) -> Vec<String> {
        self.entries.iter().flat_map(Entry::lines).collect()
    }

    /// 当前保存的消息条数。
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// 是否还没有任何消息。
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

impl Default for Session {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::{Role, Session, Stamp, LIMIT};

    fn one(role: Role, text: &str) -> Vec<String> {
        Session::new().push(Stamp::from_secs(7), role, text)
    }

    #[test]
    fn a_line_names_the_role_and_the_time() {
        assert_eq!(
            one(Role::User, "查看磁盘"),
            ["[00:07] 你 · 查看磁盘".to_owned()]
        );
    }

    #[test]
    fn every_role_has_its_own_label() {
        let labels = [
            Role::System.label(),
            Role::User.label(),
            Role::Assist.label(),
            Role::Exec.label(),
        ];
        assert_eq!(labels, ["系统", "你", "极境", "执行"]);
    }

    #[test]
    fn a_second_line_is_indented_under_the_first() {
        assert_eq!(
            one(Role::Exec, "第一行\n第二行"),
            ["[00:07] 执行 · 第一行".to_owned(), "  第二行".to_owned()]
        );
    }

    #[test]
    fn empty_text_produces_no_line() {
        assert!(one(Role::System, "").is_empty());
    }

    #[test]
    fn a_stamp_grows_an_hour_field_only_when_needed() {
        assert_eq!(Stamp::from_secs(9).text(), "00:09");
        assert_eq!(Stamp::from_secs(3_661).text(), "1:01:01");
    }

    #[test]
    fn lines_keep_the_arrival_order() {
        let mut session = Session::new();
        session.push(Stamp::from_secs(0), Role::System, "甲");
        session.push(Stamp::from_secs(1), Role::User, "乙");
        assert_eq!(
            session.lines(),
            ["[00:00] 系统 · 甲".to_owned(), "[00:01] 你 · 乙".to_owned()]
        );
    }

    #[test]
    fn the_oldest_entry_is_dropped_beyond_the_limit() {
        let mut session = Session::new();
        for index in 0..=LIMIT {
            session.push(Stamp::from_secs(0), Role::System, index.to_string());
        }
        assert_eq!(session.len(), LIMIT);
        assert_eq!(
            session.lines().first().map(String::as_str),
            Some("[00:00] 系统 · 1")
        );
    }
}
