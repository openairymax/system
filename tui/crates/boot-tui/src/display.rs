// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 终端显示面
//
// 一帧只做一次写出：进备用屏、藏光标，之后整帧字节一次 write_all；Drop
// 里无条件恢复光标与主屏，异常退出也不把控制台留在备用屏。尺寸每帧查一次，
// 窗口变化不装信号处理器——被打断的 poll 已按「本轮无输入」处理。
//
// 首帧写出后按 NOTIFY_SOCKET 通知 systemd；没有该变量就静默跳过，开发机
// 上直接跑二进制时不需要 systemd 在场。抽象命名空间的套接字 std 无法寻址，
// 跳过而不是猜一个路径。
//
// SSoT: 0.0.5 方案 §14.3、§14.5。
// ===========================================================================

//! 备用屏、尺寸查询与一次性写出。

use std::fs::File;
use std::io::Write;
use std::os::unix::net::UnixDatagram;
use std::path::Path;

use intent::Refusal;
use rustix::termios::{isatty, tcgetwinsize};

/// 控制终端路径。
const TTY: &str = "/dev/tty";

/// 进备用屏、藏光标，并把样式复位。
const ENTER: &[u8] = b"\x1b[0m\x1b[?1049h\x1b[?25l";

/// 显示光标、回主屏。
const LEAVE: &[u8] = b"\x1b[?25h\x1b[?1049l";

/// 内核还没报出真实尺寸时的列数。
const FALLBACK_COLS: u16 = 80;

/// 内核还没报出真实尺寸时的行数。
const FALLBACK_ROWS: u16 = 24;

/// 写侧的终端；Drop 时恢复主屏。
pub struct Display {
    tty: File,
}

impl Display {
    /// 打开控制终端并进入备用屏。
    pub fn open() -> Result<Self, Refusal> {
        Self::at(Path::new(TTY))
    }

    /// 当前窗口的列数与行数；内核报 0 时取开发终端常见的 80×24。
    pub fn size(&self) -> Result<(u16, u16), Refusal> {
        let winsize = tcgetwinsize(&self.tty).map_err(|_| lost())?;
        Ok((
            or_default(winsize.ws_col, FALLBACK_COLS),
            or_default(winsize.ws_row, FALLBACK_ROWS),
        ))
    }

    /// 整帧写出并立即刷新，保证帧预算测到的是真实上屏时刻。
    pub fn present(&mut self, frame: &[u8]) -> Result<(), Refusal> {
        self.tty.write_all(frame).map_err(|_| lost())?;
        self.tty.flush().map_err(|_| lost())
    }

    fn at(path: &Path) -> Result<Self, Refusal> {
        let mut tty = File::options().write(true).open(path).map_err(|_| lost())?;
        if !isatty(&tty) {
            return Err(lost());
        }
        tty.write_all(ENTER).map_err(|_| lost())?;
        Ok(Self { tty })
    }
}

impl Drop for Display {
    fn drop(&mut self) {
        let _ = self.tty.write_all(LEAVE);
        let _ = self.tty.flush();
    }
}

fn lost() -> Refusal {
    Refusal::unavailable("tty")
}

/// 内核报 0 时取默认值；真实尺寸原样返回。
fn or_default(value: u16, fallback: u16) -> u16 {
    if value == 0 {
        fallback
    } else {
        value
    }
}

/// 首帧写出后通知 systemd；未配置或不可寻址时静默返回。
pub fn notify_ready() {
    let Ok(socket) = std::env::var("NOTIFY_SOCKET") else {
        return;
    };
    let Some(target) = notify_target(&socket) else {
        return;
    };
    let Ok(dgram) = UnixDatagram::unbound() else {
        return;
    };
    let _ = dgram.send_to(b"READY=1", target);
}

/// 抽象套接字以 `@` 开头，std 的 `send_to` 只能走文件系统路径。
fn notify_target(socket: &str) -> Option<&str> {
    if socket.is_empty() || socket.starts_with('@') {
        return None;
    }
    Some(socket)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_zero_winsize_takes_the_default() {
        assert_eq!(or_default(0, FALLBACK_COLS), FALLBACK_COLS);
        assert_eq!(or_default(132, FALLBACK_COLS), 132);
    }

    #[test]
    fn a_plain_file_is_refused_before_any_escape() {
        let err = Display::at(Path::new("/dev/null"))
            .err()
            .expect("非 tty 必须拒绝");
        assert_eq!(err.status(), "unavailable");
    }

    #[test]
    fn abstract_notify_sockets_are_skipped() {
        assert_eq!(notify_target(""), None);
        assert_eq!(notify_target("@/run/notify"), None);
        assert_eq!(
            notify_target("/run/systemd/notify"),
            Some("/run/systemd/notify")
        );
    }
}
