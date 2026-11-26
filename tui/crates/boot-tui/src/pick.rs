// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 引导期会话选择
//
// 一次开机只能有一个会话结论：TUI 还是 GUI。结论落在 `/run` 下的 tmpfs，
// 供 lightdm／桌面就绪探针在启动前读（ExecCondition），也供运行期切换读取
// 当时的起点。选完即走，不驻留、不跨重启。
//
// 跨重启的只有「上次选了什么」这一个词：`PREFERRED` 让第二次开机不再白等
// 两分钟，只留一个够按 T 的短窗口。它不是第二个状态源——本次开机的唯一
// 事实仍是 `desired`，`preferred` 只影响窗口长度与超时落点。
//
// 判定与呈现分开：本模块只回答「哪个键算哪个选择」「命令行开关是否要求
// 跳过」「结论怎么写盘」。终端读写与倒计时在二进制入口里，因此判定逻辑
// 可以在没有 pty 的单元测试里逐条断言。
//
// 只写 `desired`，不写 `mode`：`mode` 由会话真正就绪后的 root 助手
// `airy-session-state` 写入（§14.15 F-50）。选择器写 `mode` 会让「用户选了
// TUI 但 TUI 没起来」与「TUI 正在跑」在盘上不可区分。`preferred` 是偏好
// 记录而不是本次状态，两个下游闸门都不读它。
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

/// 上次选择的持久落点。`/run` 随重启清空，记不住用户偏好；这个文件跨重启
/// 保留，只由选择器在交互有结论时改写。
pub const PREFERRED: &str = "/var/lib/airymaxos/session/preferred";

/// 内核命令行里跳过选择、直接进桌面的开关。
pub const OFF_TOKEN: &str = "airymax.session=off";

/// 输入设备类目录；键盘是否在位由它下面的键位图回答。
pub const INPUTS: &str = "/sys/class/input";

/// `capabilities/key` 位图里的 KEY_A 位号，与 `ci/lib/hwcap.py` 的 input
/// 判据同源：有字母键才算键盘，电源键、休眠键之类不算。
const KEY_A: u32 = 30;

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
    /// 记住上次选择后的等待秒数。第一次开机仍用 `timeout`，之后只留一个
    /// 够按 T 的短窗口——每次开机都白等两分钟是开机慢的直接原因。
    pub repeat: u64,
    /// 超时或没有终端时的结论；命令行跳过与无键盘一律进桌面。
    pub default: Choice,
    /// 状态目录。
    pub dir: PathBuf,
}

impl Default for Opts {
    fn default() -> Self {
        Self {
            timeout: 120,
            repeat: 5,
            default: Choice::Gui,
            dir: crate::root::path(DIR),
        }
    }
}

/// 解析命令行；未知选项与非法取值按失败处理，不静默取默认。
pub fn parse_args<I: Iterator<Item = String>>(mut args: I) -> Result<Opts, String> {
    let mut opts = Opts::default();
    while let Some(arg) = args.next() {
        apply_arg(&mut opts, &arg, &mut args)?;
    }
    Ok(opts)
}

/// 把一个选项落到 `opts` 上；取值从同一个迭代器续读。
///
/// 逐条分派单独成函数，是为了让「循环读词」与「选项表」各自保持平坦：
/// 合在一起时每加一个开关都要同时提高两处的分支密度。
fn apply_arg<I: Iterator<Item = String>>(
    opts: &mut Opts,
    arg: &str,
    args: &mut I,
) -> Result<(), String> {
    match arg {
        "--timeout" => opts.timeout = take_secs(args, "--timeout")?,
        "--repeat-timeout" => opts.repeat = take_secs(args, "--repeat-timeout")?,
        "--default" => set_default(args, opts)?,
        "--dir" => set_dir(args, opts)?,
        other => return Err(format!("未知参数: {other}")),
    }
    Ok(())
}

/// 取一个秒数取值：上限 600 秒，超出即拒绝。
fn take_secs<I: Iterator<Item = String>>(args: &mut I, flag: &str) -> Result<u64, String> {
    let raw = args.next().ok_or_else(|| format!("{flag} 缺少取值"))?;
    let secs = raw
        .parse::<u64>()
        .map_err(|_| format!("{flag} 不是整数: {raw}"))?;
    if secs > 600 {
        return Err(format!("{flag} 超出 0..=600: {secs}"));
    }
    Ok(secs)
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

/// 键盘是否在位；`None` 表示输入类读不到，无从判断。
///
/// 拔掉键盘的机器上 tty1 仍然存在，若照常倒计时，用户什么也按不了，桌面
/// 却被白等两分钟（A.2.7）。判据只认内核的键位图：`/sys/class/input` 可读
/// 而没有任何设备带字母键，才算「无键盘」；读不到输入类时保留等待，因为
/// 那可能是裁剪过的宿主，贸然跳过会拿走用户本该有的选择。
pub fn keyboard_at(dir: &Path) -> Option<bool> {
    let entries = fs::read_dir(dir).ok()?;
    let mut keyboard = false;
    for entry in entries.flatten() {
        let caps = entry.path().join("capabilities/key");
        if let Ok(text) = fs::read_to_string(caps) {
            keyboard |= key_bit(&text, KEY_A);
        }
    }
    Some(keyboard)
}

/// 内核按 `%*pb` 打印键位图：空格分字，最后一个字是最低位。
fn key_bit(text: &str, bit: u32) -> bool {
    let words: Vec<&str> = text.split_whitespace().collect();
    let idx = words.len().checked_sub(1 + (bit / 64) as usize);
    idx.and_then(|i| u64::from_str_radix(words[i], 16).ok())
        .is_some_and(|word| (word >> (bit % 64)) & 1 == 1)
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

/// 倒计时那一行的内容；超时落点必须写清楚，记住的是 TUI 时不能再报桌面。
pub fn countdown_line(secs: u64, fallback: Choice) -> String {
    let mode = match fallback {
        Choice::Gui => "图形桌面",
        Choice::Tui => "文本模式",
    };
    format!("剩余 {secs} 秒，超时进入{mode}")
}

/// 原子写一页：同目录临时文件 + rename，读方不会看到半行。
fn write_page(dir: &Path, name: &str, choice: Choice) -> io::Result<()> {
    fs::create_dir_all(dir)?;
    fs::set_permissions(dir, fs::Permissions::from_mode(0o755))?;
    let temp = dir.join(format!(".{name}.new"));
    fs::write(&temp, format!("{}\n", choice.word()))?;
    fs::set_permissions(&temp, fs::Permissions::from_mode(0o644))?;
    fs::rename(&temp, dir.join(name))
}

/// 原子写 `desired`。
pub fn write_choice(dir: &Path, choice: Choice) -> io::Result<()> {
    write_page(dir, "desired", choice)
}

/// 读上次选择；文件缺失、读失败或内容非法一律按「没有记住」处理。
///
/// 坏值不当成证据：升级前留下的旧文件、掉电截断的文件都不该让这台机器
/// 永远按一个无意义的值开机，回到首次开机的长窗口更接近用户预期。
pub fn read_preferred(path: &Path) -> Option<Choice> {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| Choice::parse(text.trim()))
}

/// 原子写上次选择，权限 0644：状态可读，但只有 root 能改。
pub fn write_preferred(path: &Path, choice: Choice) -> io::Result<()> {
    let dir = path.parent();
    let name = path.file_name().and_then(|n| n.to_str());
    match (dir, name) {
        (Some(dir), Some(name)) => write_page(dir, name, choice),
        _ => Err(io::Error::from(io::ErrorKind::InvalidInput)),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        cmdline_off, countdown_line, judge, key_bit, keyboard_at, parse_args, read_preferred,
        write_choice, write_preferred, Choice, Opts, DIR, KEY_A, PREFERRED,
    };
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
        assert_eq!(Opts::default().repeat, 5);
        assert_eq!(Opts::default().dir, PathBuf::from(DIR));
        assert_eq!(PREFERRED, "/var/lib/airymaxos/session/preferred");
    }

    #[test]
    fn every_option_is_honoured() {
        let got = parse_args(
            args(&[
                "--timeout",
                "30",
                "--repeat-timeout",
                "7",
                "--default",
                "tui",
                "--dir",
                "/tmp/x",
            ])
            .into_iter(),
        )
        .expect("合法参数");
        assert_eq!(got.timeout, 30);
        assert_eq!(got.repeat, 7);
        assert_eq!(got.default, Choice::Tui);
        assert_eq!(got.dir, PathBuf::from("/tmp/x"));
    }

    #[test]
    fn bad_arguments_are_refused() {
        for words in [
            vec!["--timeout"],
            vec!["--timeout", "x"],
            vec!["--timeout", "601"],
            vec!["--repeat-timeout"],
            vec!["--repeat-timeout", "x"],
            vec!["--repeat-timeout", "601"],
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
    fn the_keyboard_bit_follows_the_kernel_word_order() {
        assert!(key_bit("40000000", KEY_A));
        assert!(key_bit("0 40000000\n", KEY_A));
        assert!(!key_bit("20000000", KEY_A));
        assert!(!key_bit("40000000 0", KEY_A));
        assert!(!key_bit("", KEY_A));
        assert!(!key_bit("zz", KEY_A));
    }

    #[test]
    fn a_missing_keyboard_is_told_apart_from_an_unreadable_class() {
        let dir = scratch("kbd");
        let caps = dir.join("input0/capabilities");
        std::fs::create_dir_all(&caps).expect("夹具");
        std::fs::write(caps.join("key"), "0\n").expect("写入");
        assert_eq!(keyboard_at(&dir), Some(false), "只有电源键不算键盘");
        std::fs::write(caps.join("key"), "40000000\n").expect("写入");
        assert_eq!(keyboard_at(&dir), Some(true), "字母键在位");
        std::fs::remove_dir_all(&dir).ok();
        assert_eq!(keyboard_at(&dir), None, "读不到输入类不下结论");
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
        assert!(countdown_line(0, Choice::Gui).contains('0'));
        assert!(countdown_line(119, Choice::Gui).contains("119"));
        assert!(countdown_line(5, Choice::Gui).contains("图形桌面"));
        assert!(countdown_line(5, Choice::Tui).contains("文本模式"));
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

    #[test]
    fn a_remembered_choice_survives_a_round_trip() {
        let dir = scratch("pref");
        let file = dir.join("preferred");
        assert_eq!(read_preferred(&file), None, "没有文件按没记住处理");
        write_preferred(&file, Choice::Tui).expect("写入");
        assert_eq!(read_preferred(&file), Some(Choice::Tui));
        assert!(!dir.join(".preferred.new").exists(), "临时文件必须被重命名");
        write_preferred(&file, Choice::Gui).expect("覆盖");
        assert_eq!(read_preferred(&file), Some(Choice::Gui));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn a_damaged_record_is_not_a_choice() {
        let dir = scratch("pref-bad");
        let file = dir.join("preferred");
        std::fs::create_dir_all(&dir).expect("夹具");
        for text in ["", "\n", "desktop\n", "GUI\n", "gui tui\n"] {
            std::fs::write(&file, text).expect("写入");
            assert_eq!(read_preferred(&file), None, "{text:?}");
        }
        std::fs::remove_dir_all(&dir).ok();
    }
}
