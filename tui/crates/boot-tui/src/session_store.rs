// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 引导期会话的状态文件
//
// 一次会话的结局必须留在盘上，否则「上次为什么退出」只能靠猜。文件放在
// `/run` 下的 tmpfs：它服务于同一次开机内的取证，不跨重启，与 §14.15
// 「状态机不跨休眠保存」一致。
//
// 写入走「同目录临时文件 + rename」：读取方要么看到旧内容，要么看到完整
// 新内容，不会读到半行。解析严格——未知键、未知状态一律视为没有记录，
// 避免把别人写的文件误读成本会话的结论。
//
// SSoT: 0.0.5 方案 §14.3、§14.15。
// ===========================================================================

//! 会话状态文件的读写。

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// 状态文件路径。
pub const FILE: &str = "/run/airymaxos/tui/session";

/// 一次会话的结局。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum State {
    /// 首帧已上屏，会话进行中。
    Ready,
    /// 用户主动退出。
    Done,
    /// 未经用户退出：panic 或终止信号。
    Fallback,
}

impl State {
    /// 文件里的写法。
    pub fn word(self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::Done => "done",
            Self::Fallback => "fallback",
        }
    }

    /// 只认三种写法；其余一律拒绝。
    fn parse(word: &str) -> Option<Self> {
        match word {
            "ready" => Some(Self::Ready),
            "done" => Some(Self::Done),
            "fallback" => Some(Self::Fallback),
            _ => None,
        }
    }
}

/// 一条会话记录。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Record {
    /// 结局。
    pub state: State,
    /// 一行说明；换行会被压平。
    pub note: String,
}

/// 状态文件的读写句柄。
pub struct Store {
    path: PathBuf,
}

impl Store {
    /// 系统路径 `/run/airymaxos/tui/session`。
    pub fn system() -> Self {
        Self::at(FILE)
    }

    /// 指定路径；测试与诊断用。
    pub fn at(path: impl AsRef<Path>) -> Self {
        Self {
            path: path.as_ref().to_path_buf(),
        }
    }

    /// 读取上一次记录；不存在或不成形时为 `None`。
    pub fn load(&self) -> Option<Record> {
        let text = fs::read_to_string(&self.path).ok()?;
        parse(&text)
    }

    /// 原子写入一条记录。
    pub fn save(&self, record: &Record) -> io::Result<()> {
        if let Some(dir) = self.path.parent() {
            fs::create_dir_all(dir)?;
        }
        let temp = self.path.with_extension("new");
        fs::write(&temp, render(record))?;
        fs::rename(&temp, &self.path)
    }
}

/// 记录的文件内容；说明压成一行，避免把文件撑成多段。
fn render(record: &Record) -> String {
    format!(
        "state={}\nnote={}\n",
        record.state.word(),
        record.note.replace('\n', " ")
    )
}

/// 严格解析：两行、按序、无未知键。
fn parse(text: &str) -> Option<Record> {
    let mut lines = text.lines();
    let state = lines.next()?.strip_prefix("state=")?;
    let note = lines.next()?.strip_prefix("note=")?;
    if lines.next().is_some() {
        return None;
    }
    Some(Record {
        state: State::parse(state)?,
        note: note.to_owned(),
    })
}

#[cfg(test)]
mod tests {
    use super::{parse, render, Record, State, Store};
    use std::path::PathBuf;

    fn scratch(tag: &str) -> PathBuf {
        std::env::temp_dir().join(format!("boot-tui-{tag}-{}", std::process::id()))
    }

    #[test]
    fn a_record_survives_a_round_trip() {
        let path = scratch("round-trip").join("session");
        let store = Store::at(&path);
        let record = Record {
            state: State::Done,
            note: "quit".to_owned(),
        };
        store.save(&record).expect("写入");
        assert_eq!(store.load(), Some(record));
        assert!(!path.with_extension("new").exists(), "临时文件必须被重命名");
    }

    #[test]
    fn a_missing_file_has_no_record() {
        let store = Store::at(scratch("missing").join("session"));
        assert_eq!(store.load(), None);
    }

    #[test]
    fn a_broken_record_is_refused() {
        for text in [
            "",
            "state=done\n",
            "state=nope\nnote=x\n",
            "note=x\nstate=done\n",
            "state=done\nnote=x\npaid=1\n",
        ] {
            assert_eq!(parse(text), None, "{text:?} 不应被接受");
        }
    }

    #[test]
    fn a_multiline_note_stays_on_one_line() {
        let record = Record {
            state: State::Fallback,
            note: "panic: a\nb".to_owned(),
        };
        assert_eq!(render(&record), "state=fallback\nnote=panic: a b\n");
    }

    #[test]
    fn every_state_has_a_word() {
        assert_eq!(State::Ready.word(), "ready");
        assert_eq!(State::Done.word(), "done");
        assert_eq!(State::Fallback.word(), "fallback");
    }
}
