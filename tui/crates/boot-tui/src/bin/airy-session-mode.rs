// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// airy-session-mode — 运行期会话切换入口
//
// 两个角色，同一份代码：
//   * 用户侧 `airy-session-mode <gui|tui>`：只做校验与提交作业，随后立即
//     返回。切换会停掉调用者所在的会话进程，等 `systemctl` 返回永远等不到。
//   * root 侧 `--switch <gui|tui>`：由 `airy-session-switch@.service` 调用，
//     按固定次序执行切换并在失败时限时回滚。用户直接带这个开关调用没有意义：
//     停启 lightdm／boot-tui 的权限不在 polkit 规则的放行范围内。
//
// 顺序（§14.15 不变量 4）：写 `desired` → 停旧并确认 inactive → 起新 → 等
// 就绪 → 写 `mode` 并同事务对齐 `desired` → 落 journal。先写 `mode` 会把
// 「正在切」记成「已就绪」，失败后盘上的状态与屏幕相反。
//
// 就绪只认既有标记，不新增标记：GUI 认本次 boot 新出现的
// `AIRYMAX-DESKTOP-READY` 且 lightdm active；TUI 认 boot-tui active
// （`Type=notify` 的 READY 在首帧后到达）且 tty1 前台进程组就是它。
//
// SSoT: 0.0.5 方案 §14.15 不变量 1–8。
// ===========================================================================

//! 运行期会话切换入口。

use std::fs;
use std::io;
use std::process::{Command, ExitCode, Output};
use std::thread::sleep;
use std::time::Duration;

use boot_tui::pick::{Choice, DIR};
use boot_tui::switch::{
    fail_line, fallback, mode_line, read_mode, ready_state, wait_secs, write_pages, write_want,
};

/// 单元控制入口。
const SYSTEMCTL: &str = "/usr/bin/systemctl";
/// 只读内核日志的入口；GUI 就绪判据从这里取既有标记。
const JOURNAL: &str = "/usr/bin/journalctl";
/// 切前台。
const CHVT: &str = "/usr/bin/chvt";
/// 就绪探针打印的标记（0.0.4 既有）。
const MARK: &str = "AIRYMAX-DESKTOP-READY";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [word] if word == "status" => status(),
        [flag, word] if flag == "--switch" => worker(word),
        [word] => user(word),
        _ => usage(),
    }
}

/// 用法错误与非法参数同码：都不是开机故障，是调用方写错。
fn usage() -> ExitCode {
    eprintln!("用法: airy-session-mode <gui|tui|status>");
    ExitCode::from(2)
}

/// 打印实际态；缺文件即 `none`，由调用方决定怎么用。
fn status() -> ExitCode {
    let word = read_mode(std::path::Path::new(DIR)).map_or("none", Choice::word);
    println!("mode={word}");
    ExitCode::SUCCESS
}

/// 用户侧：校验系统状态与当前模式，然后只提交作业。
fn user(word: &str) -> ExitCode {
    let want = match Choice::parse(word) {
        Some(want) => want,
        None => return usage(),
    };
    let state = out(SYSTEMCTL, &["is-system-running"]);
    if !ready_state(&state) {
        eprintln!("AIRYMAX-SESSION-MODE refused=not-ready state={state}");
        return ExitCode::from(4);
    }
    if read_mode(std::path::Path::new(DIR)) == Some(want) {
        println!("mode={} state=unchanged", want.word());
        return ExitCode::SUCCESS;
    }
    let unit = unit_of(want);
    match run(SYSTEMCTL, &["--no-block", "start", &unit]) {
        Ok(out) if out.status.success() => {
            println!("mode={} state=switching", want.word());
            ExitCode::SUCCESS
        }
        Ok(out) => {
            eprintln!("AIRYMAX-SESSION-MODE start={unit} status={}", out.status);
            ExitCode::from(1)
        }
        Err(err) => {
            eprintln!("AIRYMAX-SESSION-MODE start={unit} error={err}");
            ExitCode::from(1)
        }
    }
}

/// root 侧：执行切换，失败限时回滚。
fn worker(word: &str) -> ExitCode {
    let want = match Choice::parse(word) {
        Some(want) => want,
        None => return usage(),
    };
    let dir = std::path::Path::new(DIR);
    let old = read_mode(dir);
    let reason = take_reason(dir);
    if let Err(err) = write_want(dir, want) {
        eprintln!("AIRYMAX-SESSION-MODE write=desired error={err}");
        return ExitCode::from(1);
    }
    if old == Some(want) {
        // 已在目标上：只确保会话还在（TUI 退出后 getty 可能已接管 tty1）。
        let _ = match want {
            Choice::Gui => start_gui(),
            Choice::Tui => start_tui(),
        };
        return settle(dir, old, want, &reason);
    }
    match apply(want) {
        Ok(()) => settle(dir, old, want, &reason),
        Err(cause) => rollback(dir, old, want, cause),
    }
}

/// 切换成功：写两页并落 journal。
fn settle(dir: &std::path::Path, old: Option<Choice>, want: Choice, reason: &str) -> ExitCode {
    if let Err(err) = write_pages(dir, want) {
        eprintln!("AIRYMAX-SESSION-MODE write=mode error={err}");
        return ExitCode::from(1);
    }
    eprintln!("{}", mode_line(old, want, reason));
    ExitCode::SUCCESS
}

/// 回滚：先改 `desired`，再启回旧单元，最后写两页——顺序与正向一致。
fn rollback(dir: &std::path::Path, old: Option<Choice>, want: Choice, cause: &str) -> ExitCode {
    let back = fallback(old);
    let _ = write_want(dir, back);
    let _ = match back {
        Choice::Gui => start_gui(),
        Choice::Tui => start_tui(),
    };
    if let Err(err) = write_pages(dir, back) {
        eprintln!("AIRYMAX-SESSION-MODE write=mode error={err}");
    }
    eprintln!("{}", fail_line(want, cause));
    ExitCode::from(1)
}

/// 一个方向的完整切换；返回判负原因，全部来自实测而不是猜测。
fn apply(want: Choice) -> Result<(), &'static str> {
    match want {
        Choice::Gui => switch_gui(),
        Choice::Tui => switch_tui(),
    }
}

/// TUI→GUI：停 TUI，拉起探针与座位，等新标记出现，再校正前台。
fn switch_gui() -> Result<(), &'static str> {
    stop("boot-tui.service")?;
    let base = marks();
    start_units(&["airymax-desktop-ready.service", "lightdm.service"])?;
    wait_gui(wait_secs(Choice::Gui), base)?;
    fg_gui()
}

/// GUI→TUI：停桌面，拉起 TUI，等 READY 与前台进程组一致。
fn switch_tui() -> Result<(), &'static str> {
    stop("lightdm.service")?;
    start_units(&["boot-tui.service"])?;
    wait_on("boot-tui.service", wait_secs(Choice::Tui))?;
    if !tpgid_ok() {
        return Err("tty1-owner");
    }
    chvt("1")
}

/// 起回 GUI：探针与座位一起拉起，窗口与正向一致。
fn start_gui() -> Result<(), &'static str> {
    let base = marks();
    start_units(&["airymax-desktop-ready.service", "lightdm.service"])?;
    wait_gui(wait_secs(Choice::Gui), base)
}

/// 起回 TUI：就绪后把它放回前台。
fn start_tui() -> Result<(), &'static str> {
    start_units(&["boot-tui.service"])?;
    wait_on("boot-tui.service", wait_secs(Choice::Tui))?;
    chvt("1")
}

/// 停一个单元：`systemctl stop` 本身等作业结束，返回后再核一次实际态。
fn stop(unit: &str) -> Result<(), &'static str> {
    run(SYSTEMCTL, &["stop", unit]).map_err(|_| "stop-failed")?;
    if active(unit) {
        return Err("still-active");
    }
    Ok(())
}

/// GUI 就绪：lightdm 已激活，且探针在本次切换后又打印过一次标记。
fn wait_gui(secs: u64, base: u32) -> Result<(), &'static str> {
    let mut left = secs;
    loop {
        if active("lightdm.service") && marks() > base {
            return Ok(());
        }
        if left == 0 {
            return Err("ready-timeout");
        }
        sleep(Duration::from_secs(1));
        left -= 1;
    }
}

/// 单元进入 active 的等待；`Type=notify` 的单元以 READY 为准。
fn wait_on(unit: &str, secs: u64) -> Result<(), &'static str> {
    let mut left = secs;
    loop {
        if active(unit) {
            return Ok(());
        }
        if left == 0 {
            return Err("ready-timeout");
        }
        sleep(Duration::from_secs(1));
        left -= 1;
    }
}

/// 就绪标记的累计条数；读不到 journal 记 0，判据随之只能靠单元状态。
fn marks() -> u32 {
    let text = out(
        JOURNAL,
        &[
            "-q",
            "-b",
            "-o",
            "cat",
            "-u",
            "airymax-desktop-ready.service",
        ],
    );
    text.lines().filter(|line| line.contains(MARK)).count() as u32
}

/// GUI 方向的画面前台：等座位会话的 VT 成为当前控制台，超时后兜底切一次。
fn fg_gui() -> Result<(), &'static str> {
    let mut left = 5;
    while left > 0 {
        if let Some(tty) = seat_tty() {
            if fg() == Some(tty.clone()) {
                return Ok(());
            }
            if left == 1 {
                chvt(&tty)?;
                return Ok(());
            }
        }
        sleep(Duration::from_secs(1));
        left -= 1;
    }
    Err("no-foreground")
}

/// 座位会话的 VT 编号；取 `loginctl` 的 TTY 列，不解析 `seat-status`。
fn seat_tty() -> Option<String> {
    let lines = out("/usr/bin/loginctl", &["list-sessions", "--no-legend"]);
    lines
        .lines()
        .filter_map(|line| line.split_whitespace().nth(4))
        .find(|tty| tty.starts_with("tty"))
        .map(str::to_owned)
}

/// 当前控制台编号。
fn fg() -> Option<String> {
    let text = out("/usr/bin/fgconsole", &[]);
    (!text.is_empty()).then_some(text)
}

/// tty1 的前台进程组是否就是 boot-tui 主进程。
fn tpgid_ok() -> bool {
    let pid = out(
        SYSTEMCTL,
        &["show", "-p", "MainPID", "--value", "boot-tui.service"],
    );
    if pid.is_empty() || pid == "0" {
        return false;
    }
    let group = out("/usr/bin/ps", &["-o", "tpgid=", "-p", &pid]);
    group.trim() == pid.trim()
}

/// 单元是否处于 active。
fn active(unit: &str) -> bool {
    out(SYSTEMCTL, &["is-active", unit]) == "active"
}

/// 提交若干单元的启动作业；`--no-block` 之后由等待判据决定成败。
fn start_units(units: &[&str]) -> Result<(), &'static str> {
    let mut argv = vec!["--no-block", "start"];
    argv.extend_from_slice(units);
    match run(SYSTEMCTL, &argv) {
        Ok(out) if out.status.success() => Ok(()),
        _ => Err("start-failed"),
    }
}

/// 把某个终端切到前台。
fn chvt(tty: &str) -> Result<(), &'static str> {
    let num = tty.trim_start_matches("tty");
    match run(CHVT, &[num]) {
        Ok(out) if out.status.success() => Ok(()),
        _ => Err("chvt-failed"),
    }
}

/// 读并删除注销钩子留下的原因；没有就记 `switch`。
fn take_reason(dir: &std::path::Path) -> String {
    let path = dir.join("switch-reason");
    let text = fs::read_to_string(&path).unwrap_or_default();
    let _ = fs::remove_file(path);
    let reason = text.trim();
    if reason.is_empty() {
        "switch".to_owned()
    } else {
        reason.to_owned()
    }
}

/// 单元名与目标一一对应。
fn unit_of(want: Choice) -> String {
    format!("airy-session-switch@{}.service", want.word())
}

/// 执行一条命令并保留结果。
fn run(program: &str, args: &[&str]) -> io::Result<Output> {
    Command::new(program).args(args).output()
}

/// 只取标准输出并去掉首尾空白；失败一律当空串，由调用方判空。
fn out(program: &str, args: &[&str]) -> String {
    match run(program, args) {
        Ok(out) => String::from_utf8_lossy(&out.stdout).trim().to_owned(),
        Err(_) => String::new(),
    }
}
