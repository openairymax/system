// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// airy-session-pick — 引导期会话选择入口
//
// 由 `airy-session-choice.service` 在 tty1 上以 root 启动，在 120 秒窗口内
// 等一个键：T 进 TUI，G 或回车进桌面，超时取 `--default`。结论只写
// `/run/airymaxos/session/desired`，随后退出；TUI 分支在这里显式拉起
// `boot-tui.service`，GUI 分支什么都不做（lightdm 由 graphical.target 拉起）。
//
// 120 秒只在第一次开机发生。交互一旦有结论，结果记进
// `/var/lib/airymaxos/session/preferred`；此后每次开机只留一个短窗口
// （`--repeat-timeout`），超时按记住的选择走，开机不再白等两分钟。
// 命令行旁路与无键盘是设备事实而不是用户选择，不写这份记录。
//
// 无键盘的机器不占这 120 秒：tty1 仍在，但没人能按键，白等只会拖慢开机
// （§14.1 A.2.7）。判据取内核键位图，读不到输入类时仍按原样等待。
//
// 判定、写盘与参数解析都在 `boot_tui::pick` 里，本文件只做四件事：读
// `/proc/cmdline`、驱动 tty1 的倒计时、把结论记进 journal、把交互结论留
// 给下次开机。终端一定被恢复成原样，输入队列一定被清空——否则残留的 `T`
// 会被 getty 或桌面收走。
//
// 退出码恒为 0：选择不是失败点，任何异常都回落默认值并留下 journal 行。
// 唯一例外是参数本身不合法（退出码 2），那是打包错误而不是开机故障。
//
// SSoT: 0.0.5 方案 §14.1、§14.15。
// ===========================================================================

//! 引导期会话选择入口。

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::process::{Command, ExitCode};

use boot_tui::pick::{self, Choice, Opts};
use boot_tui::root;

/// 会话选择独占的终端。
const TTY: &str = "/dev/tty1";

/// 拉起 TUI 会话的入口。
const SYSTEMCTL: &str = "/usr/bin/systemctl";

fn main() -> ExitCode {
    let opts = match pick::parse_args(std::env::args().skip(1)) {
        Ok(opts) => opts,
        Err(err) => {
            eprintln!("AIRYMAX-SESSION parse={err}");
            return ExitCode::from(2);
        }
    };
    let (choice, reason) = decide(&opts);
    match pick::write_choice(&opts.dir, choice) {
        Ok(()) => eprintln!("AIRYMAX-SESSION choice={} reason={reason}", choice.word()),
        Err(err) => eprintln!(
            "AIRYMAX-SESSION choice={} reason={reason} write={err}",
            choice.word()
        ),
    }
    remember(choice, reason);
    if choice == Choice::Tui {
        start_tui();
    }
    ExitCode::SUCCESS
}

/// 命令行开关优先，其次无键盘，再次交互；都拿不到就取默认。
///
/// 有上次选择时窗口缩到 `--repeat-timeout`，超时落点就是上次的选择；
/// 第一次开机仍用完整窗口与 `--default`。
fn decide(opts: &Opts) -> (Choice, &'static str) {
    let cmdline = fs::read_to_string(root::path("/proc/cmdline")).unwrap_or_default();
    if pick::cmdline_off(&cmdline) {
        return (Choice::Gui, "cmdline");
    }
    if pick::keyboard_at(&root::path(pick::INPUTS)) == Some(false) {
        return (Choice::Gui, "no-keyboard");
    }
    match pick::read_preferred(&root::path(pick::PREFERRED)) {
        Some(last) => {
            let (choice, reason) = interactive(opts.repeat, last);
            let reason = if reason == "timeout" {
                "remembered"
            } else {
                reason
            };
            (choice, reason)
        }
        None => interactive(opts.timeout, opts.default),
    }
}

/// 把交互结论记成下次的起点；命令行旁路与无键盘不落盘。
///
/// 写失败只记一行：记住与否影响的是下次开机的窗口长度，不影响本次结论。
fn remember(choice: Choice, reason: &str) {
    if !matches!(reason, "key" | "timeout") {
        return;
    }
    if let Err(err) = pick::write_preferred(&root::path(pick::PREFERRED), choice) {
        eprintln!("AIRYMAX-SESSION preferred={err}");
    }
}

/// 打开 tty1 并在原始终端里等一个键。
fn interactive(secs: u64, fallback: Choice) -> (Choice, &'static str) {
    let mut tty = match open_tty() {
        Ok(tty) => tty,
        Err(_) => return (fallback, "no-tty"),
    };
    let saved = match rustix::termios::tcgetattr(&tty) {
        Ok(saved) => saved,
        Err(_) => return (fallback, "no-termios"),
    };
    let mut raw = saved.clone();
    raw.make_raw();
    if rustix::termios::tcsetattr(&tty, rustix::termios::OptionalActions::Now, &raw).is_err() {
        return (fallback, "no-raw");
    }
    let outcome = wait_key(&mut tty, secs, fallback);
    let _ = rustix::termios::tcsetattr(&tty, rustix::termios::OptionalActions::Now, &saved);
    let _ = rustix::termios::tcflush(&tty, rustix::termios::QueueSelector::IFlush);
    let _ = write!(tty, "\x1b[2J\x1b[H");
    let _ = tty.flush();
    outcome
}

/// 倒计时与按键循环；每轮最多阻塞一秒，便于按秒重绘。
fn wait_key(tty: &mut File, secs: u64, fallback: Choice) -> (Choice, &'static str) {
    draw_all(tty, secs, fallback);
    if secs == 0 {
        return (fallback, "timeout");
    }
    let mut left = secs;
    loop {
        let mut fds = [rustix::event::PollFd::new(
            &*tty,
            rustix::event::PollFlags::IN,
        )];
        let tick = rustix::event::Timespec {
            tv_sec: 1,
            tv_nsec: 0,
        };
        match rustix::event::poll(&mut fds, Some(&tick)) {
            Ok(0) => {
                left -= 1;
                draw_left(tty, left, fallback);
                if left == 0 {
                    return (fallback, "timeout");
                }
            }
            Ok(_) => {
                let mut byte = [0u8; 1];
                if matches!(tty.read(&mut byte), Ok(1)) {
                    if let Some(choice) = pick::judge(byte[0]) {
                        return (choice, "key");
                    }
                }
            }
            Err(_) => return (fallback, "poll"),
        }
    }
}

/// 首屏：固定说明一次画完。
fn draw_all(tty: &mut File, secs: u64, fallback: Choice) {
    let _ = write!(
        tty,
        "\x1b[2J\x1b[HAirymaxOS 0.0.5  会话选择\r\n\r\n\
         \x20 [T] 进入 TUI 终端会话\r\n\
         \x20 [G/Enter] 进入图形桌面\r\n\r\n"
    );
    draw_left(tty, secs, fallback);
}

/// 只重绘倒计时那一行，避免整屏闪烁。
fn draw_left(tty: &mut File, secs: u64, fallback: Choice) {
    let _ = write!(
        tty,
        "\x1b[6;1H\x1b[2K{}",
        pick::countdown_line(secs, fallback)
    );
    let _ = tty.flush();
}

/// tty1 只以读写方式打开；打开失败视为没有可交互终端。
fn open_tty() -> io::Result<File> {
    OpenOptions::new()
        .read(true)
        .write(true)
        .open(root::path(TTY))
}

/// TUI 分支显式拉起会话单元；失败只记一行，不改变已经落盘的结论。
fn start_tui() {
    match Command::new(root::path(SYSTEMCTL))
        .args(["--no-block", "start", "boot-tui.service"])
        .status()
    {
        Ok(status) if status.success() => {}
        Ok(status) => eprintln!("AIRYMAX-SESSION start=boot-tui status={status}"),
        Err(err) => eprintln!("AIRYMAX-SESSION start=boot-tui error={err}"),
    }
}
