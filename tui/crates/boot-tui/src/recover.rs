// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 退出路径的收敛
//
// 会话只有四种结束方式：用户退出、单行失败、panic、终止信号。前两种由
// 帧循环自己收尾；后两种必须由这里接住，否则控制台会留在 raw 模式或备用
// 屏幕上。信号的处置函数只把「哪一个信号」写进原子量，真正的恢复仍在本
// 线程完成——异步信号上下文里只允许做这一件事。
//
// panic 由 `catch_unwind` 接住：它先让 `Display` 与 `Term` 的 Drop 恢复
// 终端，再回到 `main` 记下结局。SIGSEGV 无法在进程内恢复，由 systemd 的
// `TTYReset` 兜底（§14.3）。
//
// SSoT: 0.0.5 方案 §14.3。
// ===========================================================================

//! 崩溃、终止信号与退出结局。

use std::any::Any;
use std::panic::{self, AssertUnwindSafe};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use intent::Refusal;
use signal_hook::consts::{SIGHUP, SIGINT, SIGTERM};
use signal_hook::flag;

/// 需要接住的终止信号。
pub const SIGS: [i32; 3] = [SIGHUP, SIGINT, SIGTERM];

/// 会话的收尾结论。
#[derive(Debug)]
pub enum Exit {
    /// 带状态码的单行失败。
    Refused(Refusal),
    /// 线程 panic 的文字。
    Panicked(String),
}

/// 接住 panic，并把两种失败分开。
pub fn guard<T>(body: impl FnOnce() -> Result<T, Refusal>) -> Result<T, Exit> {
    match panic::catch_unwind(AssertUnwindSafe(body)) {
        Ok(outcome) => outcome.map_err(Exit::Refused),
        Err(payload) => Err(Exit::Panicked(panic_text(payload.as_ref()))),
    }
}

/// panic 载荷的文字；两种载荷之外不臆造内容。
pub fn panic_text(payload: &(dyn Any + Send)) -> String {
    if let Some(text) = payload.downcast_ref::<&str>() {
        return (*text).to_owned();
    }
    if let Some(text) = payload.downcast_ref::<String>() {
        return text.clone();
    }
    "未知原因".to_owned()
}

/// 终止信号的接收面。
pub struct Stop {
    seen: Arc<AtomicUsize>,
}

impl Stop {
    /// 为三个信号登记处置；任一失败即视为无法保证恢复。
    pub fn arm() -> Result<Self, Refusal> {
        let seen = Arc::new(AtomicUsize::new(0));
        for sig in SIGS {
            flag::register_usize(sig, Arc::clone(&seen), sig as usize)
                .map_err(|_| Refusal::unavailable("signal"))?;
        }
        Ok(Self { seen })
    }

    /// 已收到的信号；未收到为 `None`。
    pub fn taken(&self) -> Option<i32> {
        let sig = self.seen.load(Ordering::Relaxed);
        (sig != 0).then_some(sig as i32)
    }
}

/// 信号的固定写法，用于状态文件与 journal 对齐。
pub fn name(sig: i32) -> &'static str {
    match sig {
        SIGHUP => "SIGHUP",
        SIGINT => "SIGINT",
        SIGTERM => "SIGTERM",
        _ => "signal",
    }
}

#[cfg(test)]
mod tests {
    use super::{guard, name, panic_text, Exit};
    use intent::Refusal;
    use std::panic;

    #[test]
    fn a_str_panic_keeps_its_text() {
        let payload = panic::catch_unwind(|| panic!("boom")).expect_err("必须 panic");
        assert_eq!(panic_text(payload.as_ref()), "boom");
    }

    #[test]
    fn a_string_panic_keeps_its_text() {
        let payload =
            panic::catch_unwind(|| panic!("{}", "grown".to_owned())).expect_err("必须 panic");
        assert_eq!(panic_text(payload.as_ref()), "grown");
    }

    #[test]
    fn guard_passes_a_value_through() {
        assert_eq!(guard(|| Ok::<_, Refusal>(7)).expect("成功"), 7);
    }

    #[test]
    fn guard_keeps_a_refusal_intact() {
        let outcome = guard(|| Err::<(), _>(Refusal::unavailable("tty"))).expect_err("必须失败");
        match outcome {
            Exit::Refused(err) => assert_eq!(err.detail(), "tty"),
            Exit::Panicked(text) => panic!("不应是 panic：{text}"),
        }
    }

    #[test]
    fn guard_turns_a_panic_into_a_text() {
        let outcome = guard(|| -> Result<(), Refusal> { panic!("gone") }).expect_err("必须失败");
        match outcome {
            Exit::Panicked(text) => assert_eq!(text, "gone"),
            Exit::Refused(err) => panic!("不应是失败：{}", err.detail()),
        }
    }

    #[test]
    fn every_intercepted_signal_has_a_name() {
        assert_eq!(name(signal_hook::consts::SIGHUP), "SIGHUP");
        assert_eq!(name(signal_hook::consts::SIGINT), "SIGINT");
        assert_eq!(name(signal_hook::consts::SIGTERM), "SIGTERM");
        assert_eq!(name(-1), "signal");
    }
}
