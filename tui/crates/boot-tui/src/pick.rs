// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 引导期会话选择
//
// 一次开机只能有一个会话结论：TUI 还是 GUI。结论落在 `/run` 下的 tmpfs，
// 供 lightdm／桌面就绪探针在启动前读（ExecCondition），也供运行期切换读取
// 当时的起点。选完即走，不驻留、不跨重启。
//
// 判定与呈现分开：本模块只回答「哪个键算哪个选择」「命令行开关是否要求
// 跳过」「结论怎么写盘」。终端读写与倒计时在二进制入口里，因此判定逻辑
// 可以在没有 pty 的单元测试里逐条断言。
//
// 只写 `desired`，不写 `mode`：`mode` 由会话真正就绪后的 root 助手
// `airy-session-state` 写入（§14.15 F-50）。选择器写 `mode` 会让「用户选了
// TUI 但 TUI 没起来」与「TUI 正在跑」在盘上不可区分。
//
// SSoT: 0.0.5 方案 §14.1、§14.15。
// ===========================================================================

//! 引导期会话选择的判定核心。

use std::fs;
use std::io;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

/// 会话状态目录；选择器只写其中的 `desired`。
pub const DIR: &str = "/run/airymaxos/session";

/// 内核命令行里跳过选择、直接进桌面的开关。
pub const OFF_TOKEN: &str = "airymax.session=off";

/// 选择结果。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Choice {
    /// 图形桌面。
    Gui,
    /// TUI 会话。
    Tui,
}

impl Choice {
    /// 状态文件里的写法。
    pub fn word(self) -> &'static str {
        match self {
            Self::Gui => "gui",
            Self::Tui => "tui",
        }
    }

    /// 只认两种写法；其余一律拒绝。
    pub fn parse(word: &str) -> Option<Self> {
        match word {
            "gui" => Some(Self::Gui),
            "tui" => Some(Self::Tui),
            _ => None,
        }
    }
}

/// 一次选择的参数。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Opts {
    /// 等待按键的秒数；0 表示立即取默认值。
    pub timeout: u64,
    /// 超时、没有终端或命令行要求跳过时的结论。
    pub default: Choice,
    /// 状态目录。
    pub dir: PathBuf,
}

impl Default for Opts {
    fn default() -> Self {
        Self {
            timeout: 120,
            default: Choice::Gui,
            dir: crate::root::path(DIR),
        }
    }
}

/// 解析命令行；未知选项与非法取值按失败处理，不静默取默认。
pub fn parse_args<I: Iterator<Item = String>>(mut args: I) -> Result<Opts, String> {
    let mut opts = Opts::default();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--timeout" => set_timeout(&mut args, &mut opts)?,
            "--default" => set_default(&mut args, &mut opts)?,
            "--dir" => set_dir(&mut args, &mut opts)?,
            other => return Err(format!("未知参数: {other}")),
        }
    }
    Ok(opts)
}

/// `--timeout <秒>`：上限 600 秒，超出即拒绝。
fn set_timeout<I: Iterator<Item = String>>(args: &mut I, opts: &mut Opts) -> Result<(), String> {
    let raw = args.next().ok_or("--timeout 缺少取值")?;
    let secs = raw
        .parse::<u64>()
        .map_err(|_| format!("--timeout 不是整数: {raw}"))?;
    if secs > 600 {
        return Err(format!("--timeout 超出 0..=600: {secs}"));
    }
    opts.timeout = secs;
    Ok(())
}

/// `--default <gui|tui>`：只认两个词。
fn set_default<I: Iterator<Item = String>>(args: &mut I, opts: &mut Opts) -> Result<(), String> {
    let raw = args.next().ok_or("--default 缺少取值")?;
    opts.default =
        Choice::parse(&raw).ok_or_else(|| format!("--default 只能是 gui 或 tui: {raw}"))?;
    Ok(())
}

/// `--dir <路径>`：状态目录，测试与诊断用。
fn set_dir<I: Iterator<Item = String>>(args: &mut I, opts: &mut Opts) -> Result<(), String> {
    let raw = args.next().ok_or("--dir 缺少取值")?;
    opts.dir = PathBuf::from(raw);
    Ok(())
}

/// 命令行里出现跳过开关即为真；按空白切词，避免前缀误命中。
pub fn cmdline_off(line: &str) -> bool {
    line.split_whitespace().any(|word| word == OFF_TOKEN)
}

/// 一个按键对应哪个选择；不构成选择的键返回 `None`。
///
/// `Ctrl-C`（0x03）必须落在这里而不是由终端转成 SIGINT：选择器在 TUI 的
/// 启动路径上，被中断就等于把用户锁在没有会话的 tty1 上。
pub fn judge(byte: u8) -> Option<Choice> {
    match byte {
        b'g' | b'G' | b'\r' | b'\n' => Some(Choice::Gui),
        b't' | b'T' => Some(Choice::Tui),
        _ => None,
    }
}

/// 倒计时那一行的内容。
pub fn countdown_line(secs: u64) -> String {
    format!("剩余 {secs} 秒，超时进入图形桌面")
}

/// 原子写 `desired`：同目录临时文件 + rename，读方不会看到半行。
pub fn write_choice(dir: &Path, choice: Choice) -> io::Result<()> {
    fs::create_dir_all(dir)?;
    fs::set_permissions(dir, fs::Permissions::from_mode(0o755))?;
    let temp = dir.join(".desired.new");
    fs::write(&temp, format!("{}\n", choice.word()))?;
    fs::rename(&temp, dir.join("desired"))
}

#[cfg(test)]
mod tests {
    use super::{cmdline_off, countdown_line, judge, parse_args, write_choice, Choice, Opts, DIR};
    use std::path::PathBuf;

    fn args(words: &[&str]) -> Vec<String> {
        words.iter().map(|w| (*w).to_owned()).collect()
    }

    fn scratch(tag: &str) -> PathBuf {
        std::env::temp_dir().join(format!("boot-tui-pick-{tag}-{}", std::process::id()))
    }

    #[test]
    fn defaults_match_the_unit() {
        assert_eq!(parse_args(args(&[]).into_iter()), Ok(Opts::default()));
        assert_eq!(Opts::default().timeout, 120);
        assert_eq!(Opts::default().dir, PathBuf::from(DIR));
    }

    #[test]
    fn every_option_is_honoured() {
        let got = parse_args(
            args(&["--timeout", "30", "--default", "tui", "--dir", "/tmp/x"]).into_iter(),
        )
        .expect("合法参数");
        assert_eq!(got.timeout, 30);
        assert_eq!(got.default, Choice::Tui);
        assert_eq!(got.dir, PathBuf::from("/tmp/x"));
    }

    #[test]
    fn bad_arguments_are_refused() {
        for words in [
            vec!["--timeout"],
            vec!["--timeout", "x"],
            vec!["--timeout", "601"],
            vec!["--default"],
            vec!["--default", "desktop"],
            vec!["--dir"],
            vec!["--nope"],
        ] {
            assert!(parse_args(args(&words).into_iter()).is_err(), "{words:?}");
        }
    }

    #[test]
    fn the_off_switch_is_matched_as_a_whole_word() {
        assert!(cmdline_off("ro quiet airymax.session=off splash"));
        assert!(cmdline_off("airymax.session=off"));
        assert!(!cmdline_off(""));
        assert!(!cmdline_off("airymax.session=offx"));
        assert!(!cmdline_off("xairymax.session=off"));
    }

    #[test]
    fn keys_map_to_the_documented_choices() {
        for key in [b'g', b'G', b'\r', b'\n'] {
            assert_eq!(judge(key), Some(Choice::Gui), "{key:?}");
        }
        for key in [b't', b'T'] {
            assert_eq!(judge(key), Some(Choice::Tui), "{key:?}");
        }
        for key in [0x03, 0x04, 0x1b, b'x', b' '] {
            assert_eq!(judge(key), None, "{key:?}");
        }
    }

    #[test]
    fn the_countdown_line_carries_the_seconds() {
        assert!(countdown_line(0).contains('0'));
        assert!(countdown_line(119).contains("119"));
    }

    #[test]
    fn a_choice_is_written_atomically() {
        let dir = scratch("write");
        write_choice(&dir, Choice::Tui).expect("写入");
        assert_eq!(
            std::fs::read_to_string(dir.join("desired")).expect("读取"),
            "tui\n"
        );
        assert!(!dir.join(".desired.new").exists(), "临时文件必须被重命名");
        write_choice(&dir, Choice::Gui).expect("覆盖");
        assert_eq!(
            std::fs::read_to_string(dir.join("desired")).expect("读取"),
            "gui\n"
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn only_two_words_are_choices() {
        assert_eq!(Choice::parse("gui"), Some(Choice::Gui));
        assert_eq!(Choice::parse("tui"), Some(Choice::Tui));
        assert_eq!(Choice::parse("GUI"), None);
        assert_eq!(Choice::parse("desktop"), None);
    }
}
