// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 原始终端
//
// 引导期只有 /dev/tty 一个输入面：没有 X、没有 logind，也没有可依赖的
// 终端库。这里只做三件事——打开控制终端、切到 raw、按截止时间等待字节；
// 解码交给 escape/key，事件组装交给适配器。
//
// raw 属性在 Drop 里恢复：会话异常退出也不能把控制台留在无回显状态。
// SSoT: 30-interfaces/11-input-ingress.md §10.2。
// ===========================================================================

//! `/dev/tty` 的 raw 模式读取。

use std::fs::File;
use std::io::Read;
use std::path::Path;

use rustix::event::{poll, PollFd, PollFlags, Timespec};
use rustix::termios::{tcgetattr, tcsetattr, OptionalActions, Termios};

use super::SourceError;

/// 控制终端路径；引导期没有别处能读到键盘。
const TTY: &str = "/dev/tty";

/// 已切到 raw 的控制终端；丢弃时恢复原有属性。
pub struct Term {
    tty: File,
    saved: Termios,
}

impl Term {
    /// 打开控制终端并切到 raw 模式。
    pub fn open() -> Result<Self, SourceError> {
        Self::at(Path::new(TTY))
    }

    /// 最多等待 `wait_ms` 毫秒，返回是否有字节可读；负值表示无限等待。
    pub fn wait(&mut self, wait_ms: i32) -> Result<bool, SourceError> {
        let mut fds = [PollFd::new(&self.tty, PollFlags::IN)];
        let timeout = (wait_ms >= 0).then(|| Timespec {
            tv_sec: i64::from(wait_ms / 1000),
            tv_nsec: i64::from(wait_ms % 1000) * 1_000_000,
        });
        let ready = poll(&mut fds, timeout.as_ref()).map_err(|_| lost())?;
        Ok(ready > 0 && fds[0].revents().contains(PollFlags::IN))
    }

    /// 读取当前可用的字节；raw 模式下不等待，无数据时返回 0。
    pub fn read(&mut self, buf: &mut [u8]) -> Result<usize, SourceError> {
        self.tty.read(buf).map_err(|_| lost())
    }

    fn at(path: &Path) -> Result<Self, SourceError> {
        let tty = File::open(path).map_err(|_| lost())?;
        let saved = tcgetattr(&tty).map_err(|_| lost())?;
        let mut raw = saved.clone();
        raw.make_raw();
        tcsetattr(&tty, OptionalActions::Now, &raw).map_err(|_| lost())?;
        Ok(Self { tty, saved })
    }
}

impl Drop for Term {
    fn drop(&mut self) {
        let _ = tcsetattr(&self.tty, OptionalActions::Now, &self.saved);
    }
}

fn lost() -> SourceError {
    SourceError::Unavailable { component: "tty" }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn non_tty_reports_unavailable() {
        let err = Term::at(Path::new("/dev/null"))
            .err()
            .expect("/dev/null 不是终端");
        assert_eq!(err.status(), "unavailable");
        assert_eq!(err.detail(), "tty");
        assert_eq!(err.errno(), -95);
    }
}
