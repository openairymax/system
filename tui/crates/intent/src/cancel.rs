// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 取消信号的发起与传播
//
// 取消（超时、用户按 Esc/Ctrl-C、通路切换或注销）可以在执行中的任意时刻
// 到达，判定与执行不在同一线程上，所以这里只放一个共享的「首个原因」：
// 谁先到达谁生效，后到的原因不覆盖先到的。先到者决定这次终止的语义——
// 预算耗尽记录 `timeout`，用户取消只回到空闲，不留下一条假失败。
//
// 契约 §7 的失败表是封闭的九行，没有 `cancelled` 这一行。因此「取消」不能
// 自造状态串：只有预算耗尽能落到 `timeout` 行，用户取消与关机不是失败，
// 不发失败记录，也不写返回码。调用方据此决定「是否入审计失败表」，而不
// 是拿一个编出来的状态串去凑三列。
//
// 终止执行后必须回到会话台账：取消只负责让在途请求停止，释放占用仍走
// `Ledger::finish`。两件事分开，是因为「停下来」与「记完账」的时机不同，
// 合成一个调用会让人以为取消已经完成了收尾。
//
// SSoT: 30-interfaces/11-input-ingress.md §4.4、§7；0.0.5 §14.9。
// ===========================================================================

//! 取消原因、共享信号与契约归类。

use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Arc;

use crate::status::FailKind;

/// 取消的发起方；判别值即共享标记里存的字节。
#[repr(u8)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Cause {
    /// 端到端预算耗尽，对应契约 §7 的 `timeout`。
    Budget = 1,
    /// 用户在会话层取消；契约没有对应的失败行。
    User = 2,
    /// 通路切换、注销或进程退出；同样不是失败。
    Shutdown = 3,
}

impl Cause {
    /// 契约 §7 的失败类别；没有对应行的原因返回 `None`。
    pub fn kind(self) -> Option<FailKind> {
        match self {
            Self::Budget => Some(FailKind::Timeout),
            Self::User | Self::Shutdown => None,
        }
    }
}

/// 跨线程共享的取消信号；首个原因生效。
#[derive(Clone, Debug, Default)]
pub struct Cancel(Arc<AtomicU8>);

impl Cancel {
    /// 尚未取消的信号。
    pub fn new() -> Self {
        Self::default()
    }

    /// 请求取消；返回是否由本次请求定下原因（后到者返回 `false`）。
    pub fn request(&self, cause: Cause) -> bool {
        self.0
            .compare_exchange(0, cause as u8, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
    }

    /// 首个原因；尚未取消时为 `None`。
    pub fn cause(&self) -> Option<Cause> {
        match self.0.load(Ordering::SeqCst) {
            1 => Some(Cause::Budget),
            2 => Some(Cause::User),
            3 => Some(Cause::Shutdown),
            _ => None,
        }
    }

    /// 是否已请求取消。
    pub fn is_set(&self) -> bool {
        self.cause().is_some()
    }

    /// 与契约 §7 对齐的失败类别；用户取消与关机不产生状态串。
    pub fn kind(&self) -> Option<FailKind> {
        self.cause().and_then(Cause::kind)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_cause_wins() {
        let cancel = Cancel::new();
        assert!(!cancel.is_set());
        assert!(cancel.request(Cause::User));
        assert!(!cancel.request(Cause::Budget), "后到者不得覆盖");
        assert_eq!(cancel.cause(), Some(Cause::User));
    }

    #[test]
    fn budget_maps_to_contract_timeout() {
        let cancel = Cancel::new();
        cancel.request(Cause::Budget);
        assert_eq!(cancel.kind(), Some(FailKind::Timeout));
        assert_eq!(cancel.kind().map(FailKind::status), Some("timeout"));
        assert_eq!(cancel.kind().map(FailKind::errno), Some(-110));
    }

    #[test]
    fn user_cancel_has_no_failure_row() {
        let cancel = Cancel::new();
        cancel.request(Cause::User);
        assert_eq!(cancel.kind(), None, "cancel 不得自造状态串");
    }

    #[test]
    fn cloned_token_shares_the_cause() {
        let cancel = Cancel::new();
        let peer = cancel.clone();
        cancel.request(Cause::Shutdown);
        assert_eq!(peer.cause(), Some(Cause::Shutdown));
    }
}
