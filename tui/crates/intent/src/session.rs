// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 会话状态机
//
// 会话层回答两个问题，且只在这一个进程里回答：
// ① 当前是否有在途请求——有则切换被拒（`busy`），在途意图不被打断；
// ② 这个追踪号是否已经被用过——重试必须复用，新请求不得复用。
//
// 为什么不做跨进程可见的「忙碌标志」：状态目录只允许 `mode` 与 `desired`
// 两页（§14.15 不变量 1），失败与瞬时态只进 journal。因此忙碌只能在
// 本进程内判定：切换动作由 TUI 自己发起时先问这里，问到 `busy` 就本地
// 拒绝，不再提交切换作业。GUI 侧没有等价的在途追踪，GUI→TUI 不判忙碌，
// 在途任务随会话终止（§14.15「忙碌判定：TUI 有，GUI 没有」）。
//
// 查重窗口有界：`SEEN` 条。超过窗口的旧追踪号不再被记住——生成端按
// UUID 出号，窗口只负责拦住「同一次失败被重试链路重复受理」这类近邻
// 复用，不承担全局唯一性。窗口大小与淘汰顺序由用例钉住，不是实现细节。
//
// SSoT: 0.0.5 方案 §14.15、§2.7；30-interfaces/11-input-ingress.md §4.4。
// ===========================================================================

//! 会话内的在途追踪与追踪号唯一性。

use std::collections::VecDeque;

use crate::schema::TraceId;
use crate::status::FailKind;

/// 记住多少个已受理的追踪号。
///
/// 只拦近邻复用；取值同时是内存上界：`SEEN × 24` 字节。改这个值等于改
/// 契约窗口，必须同步改用例。
pub const SEEN: usize = 64;

/// 会话状态；判据只认「等不等空闲」，不区分在途请求的类别。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SessionState {
    /// 没有在途请求，可以切换会话。
    Idle,
    /// 有且只有一个在途请求。
    Running,
}

/// 拒绝受理的原因。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Refuse {
    /// 已有在途请求，携带占用者。
    Busy(TraceId),
    /// 追踪号被新请求复用。
    Reused {
        /// 被复用的追踪号。
        trace: TraceId,
        /// 首次使用它的受理序号。
        first: u64,
    },
}

impl Refuse {
    /// 契约 §7 的失败类别：忙碌是 `busy`，复用是 `invalid`。
    pub fn kind(self) -> FailKind {
        match self {
            Self::Busy(_) => FailKind::Busy,
            Self::Reused { .. } => FailKind::Invalid,
        }
    }
}

impl core::fmt::Display for Refuse {
    fn fmt(&self, out: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::Busy(held) => write!(out, "busy: 在途 {}", held.to_hex()),
            Self::Reused { trace, first } => write!(
                out,
                "trace 复用: {} 已在第 {first} 次请求用过",
                trace.to_hex()
            ),
        }
    }
}

/// 结束请求时对不上在途记录的原因。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Unmatched {
    /// 没有任何在途请求。
    Idle,
    /// 在途的是另一个追踪号。
    Other(TraceId),
}

impl core::fmt::Display for Unmatched {
    fn fmt(&self, out: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            Self::Idle => out.write_str("没有在途请求"),
            Self::Other(held) => write!(out, "在途的是 {}", held.to_hex()),
        }
    }
}

/// 会话的在途账本；`Default` 即空闲。
///
/// 名字避开 `Session`：那个名字在 [`crate::schema`] 里已经是请求自带的
/// 会话描述，两者不是一回事。
#[derive(Debug)]
pub struct Ledger {
    state: SessionState,
    current: Option<TraceId>,
    seen: VecDeque<(TraceId, u64)>,
    serial: u64,
}

impl Default for Ledger {
    fn default() -> Self {
        Self::new()
    }
}

impl Ledger {
    /// 新建一个空闲会话。
    pub fn new() -> Self {
        Self {
            state: SessionState::Idle,
            current: None,
            seen: VecDeque::with_capacity(SEEN),
            serial: 0,
        }
    }

    /// 当前状态。
    pub fn state(&self) -> SessionState {
        self.state
    }

    /// 在途请求的追踪号；空闲时为 `None`。
    pub fn held(&self) -> Option<TraceId> {
        self.current
    }

    /// 已受理的请求数；也是复用报错里那个「第几次」的来源。
    pub fn admitted(&self) -> u64 {
        self.serial
    }

    /// 受理一个请求，返回它的受理序号（从 1 起）。
    ///
    /// 先判忙碌再判复用：有在途请求时，「忙」是更准确的那条结论，
    /// 复用与否此时不影响调用方的下一步。
    pub fn begin(&mut self, trace: TraceId) -> Result<u64, Refuse> {
        if let Some(held) = self.current {
            return Err(Refuse::Busy(held));
        }
        if let Some(first) = self.first_use(trace) {
            return Err(Refuse::Reused { trace, first });
        }
        let serial = self.serial.saturating_add(1);
        self.serial = serial;
        if self.seen.len() == SEEN {
            self.seen.pop_front();
        }
        self.seen.push_back((trace, serial));
        self.state = SessionState::Running;
        self.current = Some(trace);
        Ok(serial)
    }

    /// 结束在途请求；追踪号必须与受理时一致。
    ///
    /// 不一致按编程错误处理而不是静默清空：静默清空会让「请求 A 结束」
    /// 把请求 B 的在途记录抹掉，随后切会话就不再判忙碌。
    pub fn finish(&mut self, trace: TraceId) -> Result<(), Unmatched> {
        match self.current {
            None => Err(Unmatched::Idle),
            Some(held) if held != trace => Err(Unmatched::Other(held)),
            Some(_) => {
                self.state = SessionState::Idle;
                self.current = None;
                Ok(())
            }
        }
    }

    /// 切换会话前的判定：空闲才放行，否则带出占用者。
    ///
    /// 只允许在发起切换的进程内调用——结果不落盘，跨进程读不到。
    pub fn can_switch(&self) -> Result<(), Refuse> {
        match self.current {
            None => Ok(()),
            Some(held) => Err(Refuse::Busy(held)),
        }
    }

    /// 这个追踪号在本窗口内是否用过，返回首次受理序号。
    fn first_use(&self, trace: TraceId) -> Option<u64> {
        self.seen
            .iter()
            .find(|(seen, _)| *seen == trace)
            .map(|(_, serial)| *serial)
    }
}

#[cfg(test)]
mod tests {
    use super::{Ledger, Refuse, SessionState, Unmatched, SEEN};
    use crate::schema::TraceId;
    use crate::status::FailKind;

    /// 第 `n` 个追踪号；与真实出号无关，只要求互不相同。
    fn trace(n: u8) -> TraceId {
        let mut bytes = [0u8; 16];
        bytes[15] = n;
        TraceId(bytes)
    }

    #[test]
    fn a_new_session_is_idle() {
        let session = Ledger::new();
        assert_eq!(session.state(), SessionState::Idle);
        assert_eq!(session.held(), None);
        assert_eq!(session.admitted(), 0);
        assert_eq!(session.can_switch(), Ok(()));
    }

    #[test]
    fn one_request_at_a_time() {
        let mut session = Ledger::new();
        let first = trace(1);
        assert_eq!(session.begin(first), Ok(1));
        assert_eq!(session.state(), SessionState::Running);
        assert_eq!(session.held(), Some(first));
        assert_eq!(session.begin(trace(2)), Err(Refuse::Busy(first)));
        assert_eq!(session.admitted(), 1, "被拒的请求不计入受理数");
    }

    #[test]
    fn finishing_returns_to_idle() {
        let mut session = Ledger::new();
        let id = trace(7);
        session.begin(id).expect("受理");
        assert_eq!(session.finish(id), Ok(()));
        assert_eq!(session.state(), SessionState::Idle);
        assert_eq!(session.can_switch(), Ok(()));
    }

    #[test]
    fn finishing_the_wrong_trace_is_reported() {
        let mut session = Ledger::new();
        let held = trace(3);
        session.begin(held).expect("受理");
        assert_eq!(session.finish(trace(4)), Err(Unmatched::Other(held)));
        assert_eq!(session.state(), SessionState::Running, "状态不得被误清");
        assert_eq!(session.held(), Some(held));
    }

    #[test]
    fn finishing_while_idle_is_reported() {
        let mut session = Ledger::new();
        assert_eq!(session.finish(trace(5)), Err(Unmatched::Idle));
    }

    #[test]
    fn a_switch_is_refused_while_running() {
        let mut session = Ledger::new();
        let held = trace(9);
        session.begin(held).expect("受理");
        assert_eq!(session.can_switch(), Err(Refuse::Busy(held)));
    }

    #[test]
    fn a_finished_trace_cannot_be_reused() {
        let mut session = Ledger::new();
        let id = trace(11);
        session.begin(id).expect("受理");
        session.finish(id).expect("结束");
        assert_eq!(
            session.begin(id),
            Err(Refuse::Reused {
                trace: id,
                first: 1
            })
        );
        assert_eq!(session.admitted(), 1);
    }

    #[test]
    fn an_in_flight_trace_reports_busy_not_reused() {
        let mut session = Ledger::new();
        let id = trace(12);
        session.begin(id).expect("受理");
        assert_eq!(session.begin(id), Err(Refuse::Busy(id)));
    }

    #[test]
    fn serials_advance_only_on_acceptance() {
        let mut session = Ledger::new();
        session.begin(trace(1)).expect("受理");
        session.finish(trace(1)).expect("结束");
        assert_eq!(
            session.begin(trace(1)),
            Err(Refuse::Reused {
                trace: trace(1),
                first: 1,
            })
        );
        assert_eq!(session.begin(trace(2)), Ok(2));
    }

    #[test]
    fn the_window_evicts_oldest_first() {
        let mut session = Ledger::new();
        let mut ids = Vec::new();
        for n in 0..=SEEN {
            let id = trace(n as u8);
            ids.push(id);
            session.begin(id).expect("受理");
            session.finish(id).expect("结束");
        }
        // 最早那个已滑出窗口，可以重新受理；最后一个仍在窗口内。
        assert!(session.begin(ids[0]).is_ok(), "最旧的应已淘汰");
        session.finish(ids[0]).expect("结束");
        assert_eq!(
            session.begin(ids[SEEN]),
            Err(Refuse::Reused {
                trace: ids[SEEN],
                first: (SEEN + 1) as u64,
            })
        );
    }

    #[test]
    fn refusals_map_to_the_contract_table() {
        let busy = Refuse::Busy(trace(1));
        assert_eq!(busy.kind(), FailKind::Busy);
        assert_eq!(busy.kind().status(), "busy");
        let reused = Refuse::Reused {
            trace: trace(1),
            first: 1,
        };
        assert_eq!(reused.kind(), FailKind::Invalid);
        assert_eq!(reused.kind().status(), "invalid");
    }

    #[test]
    fn messages_carry_the_hex_trace() {
        let id = trace(0x2a);
        assert!(Refuse::Busy(id).to_string().contains(&id.to_hex()));
        assert!(Refuse::Reused {
            trace: id,
            first: 9
        }
        .to_string()
        .contains(&id.to_hex()));
        assert!(Unmatched::Other(id).to_string().contains(&id.to_hex()));
        assert!(!Unmatched::Idle.to_string().is_empty());
    }
}
