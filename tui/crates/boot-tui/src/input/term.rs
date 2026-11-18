// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 原始终端与键盘事件源
//
// 引导期只有 /dev/tty 一个输入面：没有 X、没有 logind，也没有可依赖的
// 终端库。这里只做三件事——打开控制终端、切到 raw、按截止时间等待字节；
// 解码交给 escape/key，事件组装由本模块的 `Pump` 完成。
//
// raw 属性在 Drop 里恢复：会话异常退出也不能把控制台留在无回显状态。
// SSoT: 30-interfaces/11-input-ingress.md §10.2。
// ===========================================================================

//! `/dev/tty` 的 raw 模式读取与按键事件源。

use std::collections::VecDeque;
use std::fs::File;
use std::io::Read;
use std::path::Path;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use rustix::event::{poll, PollFd, PollFlags, Timespec};
use rustix::termios::{tcgetattr, tcsetattr, OptionalActions, SpecialCodeIndex, Termios};

use super::escape::Decoder;
use super::event::{KeyEvent, MonoTime, RawBody, RawEvent, SourceKind, TraceId, Trust};
use super::source::{InputSource, SourceError};

/// 控制终端路径；引导期没有别处能读到键盘。
const TTY: &str = "/dev/tty";

/// 单次读取的字节上限；按键输入不会有更大的突发。
const READ_MAX: usize = 256;

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

    /// 读取当前可用字节；`VMIN=0` 使无数据时立即返回 0，不阻塞。
    pub fn read(&mut self, buf: &mut [u8]) -> Result<usize, SourceError> {
        self.tty.read(buf).map_err(|_| lost())
    }

    fn at(path: &Path) -> Result<Self, SourceError> {
        let tty = File::open(path).map_err(|_| lost())?;
        let saved = tcgetattr(&tty).map_err(|_| lost())?;
        let mut raw = saved.clone();
        raw.make_raw();
        raw.special_codes[SpecialCodeIndex::VMIN] = 0;
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
    SourceError::unavailable("tty")
}

/// 事件时钟：单调时刻与追踪号都从这里取。
///
/// 追踪号由启动时刻与自增序号拼成 16 字节：同一引导内唯一，重启后也不
/// 复用；它只用于关联，不承担不可预测性。
struct Ticks {
    base: Instant,
    seed: u64,
    seq: u64,
}

impl Ticks {
    fn new() -> Self {
        let seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|since| since.as_nanos() as u64)
            .unwrap_or_default();
        Self {
            base: Instant::now(),
            seed,
            seq: 0,
        }
    }

    fn now(&self) -> MonoTime {
        MonoTime(self.base.elapsed().as_nanos() as u64)
    }

    fn next(&mut self) -> TraceId {
        self.seq = self.seq.wrapping_add(1);
        let mut raw = [0u8; 16];
        raw[..8].copy_from_slice(&self.seed.to_be_bytes());
        raw[8..].copy_from_slice(&self.seq.to_be_bytes());
        TraceId(raw)
    }
}

/// 解码与配号：把字节流变成带元数据的按键事件。
///
/// 与终端解耦，超时定键与跨读取拼接都只依赖已到达的字节，因而可逐段
/// 回放验证。
struct Pump {
    dec: Decoder,
    queue: VecDeque<KeyEvent>,
    ticks: Ticks,
}

impl Pump {
    fn new() -> Self {
        Self {
            dec: Decoder::new(),
            queue: VecDeque::new(),
            ticks: Ticks::new(),
        }
    }

    fn feed(&mut self, bytes: &[u8]) {
        let mut out = Vec::new();
        self.dec.feed(bytes, &mut out);
        self.queue.extend(out);
    }

    /// 等待窗口结束时调用：孤立 ESC 在此定为 Esc 键。
    fn flush(&mut self) {
        let mut out = Vec::new();
        self.dec.flush(&mut out);
        self.queue.extend(out);
    }

    fn take(&mut self, max: usize) -> Vec<RawEvent> {
        let mut events = Vec::new();
        while events.len() < max {
            let Some(key) = self.queue.pop_front() else {
                break;
            };
            events.push(RawEvent {
                trace_id: self.ticks.next(),
                source: SourceKind::Key,
                trust: Trust::T0,
                at: self.ticks.now(),
                body: RawBody::Key(key),
            });
        }
        events
    }
}

/// 键盘输入源：独占控制终端，按需取出按键事件。
pub struct Keys {
    term: Option<Term>,
    pump: Pump,
}

impl Keys {
    /// 打开控制终端；失败即无可用的键盘来源。
    pub fn open() -> Result<Self, SourceError> {
        Ok(Self {
            term: Some(Term::open()?),
            pump: Pump::new(),
        })
    }

    /// 最多等 `wait_ms` 毫秒；超时后把孤立 ESC 定键并返回 false。
    pub fn wait(&mut self, wait_ms: i32) -> Result<bool, SourceError> {
        let ready = self.term_mut()?.wait(wait_ms)?;
        if !ready {
            self.pump.flush();
        }
        Ok(ready)
    }

    /// 读取当前可用字节；终端已关闭时什么也不做。
    fn fill(&mut self) -> Result<(), SourceError> {
        let Some(term) = self.term.as_mut() else {
            return Ok(());
        };
        let mut buf = [0u8; READ_MAX];
        let got = term.read(&mut buf)?;
        self.pump.feed(&buf[..got]);
        Ok(())
    }

    fn term_mut(&mut self) -> Result<&mut Term, SourceError> {
        self.term.as_mut().ok_or_else(lost)
    }
}

impl InputSource for Keys {
    fn kind(&self) -> SourceKind {
        SourceKind::Key
    }

    fn drain(&mut self, max: usize) -> Result<Vec<RawEvent>, SourceError> {
        self.fill()?;
        Ok(self.pump.take(max))
    }

    fn close(&mut self) -> Result<(), SourceError> {
        self.term = None;
        self.pump.queue.clear();
        Ok(())
    }
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

    #[test]
    fn pump_carries_keys_in_arrival_order() {
        let mut pump = Pump::new();
        pump.feed(b"a\x1b[A");
        let events = pump.take(8);
        assert_eq!(events.len(), 2, "一个可打印字节加一个方向键");
        assert_eq!(events[0].source, SourceKind::Key);
        assert_eq!(events[0].trust, Trust::T0);
        assert_eq!(code_of(&events[0]), 30, "字节 a 定为 KEY_A");
        assert_eq!(
            code_of(&events[1]),
            super::super::key::KEY_UP,
            "ESC [ A 定为上方向键"
        );
        assert!(events[0].at <= events[1].at, "时刻不得倒退");
    }

    #[test]
    fn pump_holds_an_unfinished_sequence_until_flush() {
        let mut pump = Pump::new();
        pump.feed(b"\x1b");
        assert!(pump.take(4).is_empty(), "半条序列不得提前定键");
        pump.flush();
        let events = pump.take(4);
        assert_eq!(events.len(), 1);
        assert_eq!(code_of(&events[0]), super::super::key::KEY_ESC);
    }

    fn code_of(event: &RawEvent) -> u16 {
        match &event.body {
            RawBody::Key(key) => key.code,
            _ => panic!("键盘源只产按键"),
        }
    }

    #[test]
    fn pump_respects_the_batch_limit() {
        let mut pump = Pump::new();
        pump.feed(b"abc");
        assert_eq!(pump.take(2).len(), 2);
        assert_eq!(pump.take(2).len(), 1, "余下的按键留到下一次");
        assert!(pump.take(2).is_empty());
    }

    #[test]
    fn trace_ids_are_unique_and_well_formed() {
        let mut pump = Pump::new();
        pump.feed(b"abcdefgh");
        let mut seen = Vec::new();
        for event in pump.take(8) {
            assert_eq!(event.trace_id.to_hex().len(), TraceId::HEX_LEN);
            assert!(!seen.contains(&event.trace_id), "追踪号不得复用");
            seen.push(event.trace_id);
        }
    }
}
