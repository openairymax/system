// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// CLI 结果归一
//
// 九个入口的 `--json` 输出是冻结契约：本层只消费，不改写、不猜测。一次
// 执行要能同时回答三件事——进程怎么结束的、输出是否被截断、失败该显示
// 哪三列（状态串、返回码、建议）。三者放进同一条记录，状态栏与审计行都
// 从这里取字段，避免两处各拼一份。
//
// 状态串不取自 CLI 的自由文本，只来自契约 §7 的失败语义表：值域收窄到
// 一张表，日志才可被机器比对。
//
// SSoT: 30-interfaces/11-input-ingress.md §5.1、§7；0.0.5 §14.9。
// ===========================================================================

//! 一次执行的结果模型与被截断的管道内容。

use intent::{FailKind, TraceId};

use crate::approve::Refusal;

/// 单条管道最多保留的字节数；超出即截断并标记，不再增长。
pub const PIPE_LIMIT: usize = 1 << 20;

/// 被限长的管道内容。
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Captured {
    /// 文本；非法 UTF-8 以替换字符呈现，不丢字节数。
    pub text: String,
    /// 是否因超过 [`PIPE_LIMIT`] 被截断。
    pub truncated: bool,
}

/// 一次执行的归一结果。
#[derive(Clone, Debug)]
pub struct Outcome {
    /// 子进程退出码；被信号终止时为 `None`。
    pub code: Option<i32>,
    /// 契约 §7 的状态串；成功为 [`Outcome::OK`]。
    pub status: &'static str,
    /// 与状态串配对的返回码；成功为 0。
    pub errno: i32,
    /// 可执行建议；成功时为空串。
    pub advice: &'static str,
    /// 追踪号；失败时必须有。
    pub trace: TraceId,
    /// 审批拒绝原因；非审批失败为 `None`。
    pub reason: Option<&'static str>,
    /// 命中的批准文件名；未过审批链或未命中为 `None`。
    pub approval: Option<String>,
    /// 标准输出。
    pub stdout: Captured,
    /// 标准错误。
    pub stderr: Captured,
}

impl Outcome {
    /// 成功状态串；失败一律取自 [`FailKind`]。
    pub const OK: &'static str = "ok";

    /// 是否成功。
    pub fn is_ok(&self) -> bool {
        self.status == Self::OK
    }

    /// 子进程正常结束；退出码原样保留，由调用方按各入口契约解释。
    pub fn spawned(code: Option<i32>, trace: TraceId) -> Self {
        Self {
            code,
            status: Self::OK,
            errno: 0,
            advice: "",
            trace,
            reason: None,
            approval: None,
            stdout: Captured::default(),
            stderr: Captured::default(),
        }
    }

    /// 由失败类别生成一条结果：三列取自同一张表，追踪号随行。
    pub fn failed(kind: FailKind, trace: TraceId) -> Self {
        Self {
            code: None,
            status: kind.status(),
            errno: kind.errno(),
            advice: kind.advice(),
            trace,
            reason: None,
            approval: None,
            stdout: Captured::default(),
            stderr: Captured::default(),
        }
    }

    /// 审批链拒绝；三列取「需要批准」，并记下具体原因供审计行使用。
    pub fn refused(reason: Refusal, trace: TraceId) -> Self {
        let mut outcome = Self::failed(FailKind::Approval, trace);
        outcome.reason = Some(reason.reason());
        outcome
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TRACE: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";

    #[test]
    fn failure_carries_the_three_columns() {
        let trace = TraceId::parse(TRACE).expect("合法追踪号");
        let outcome = Outcome::failed(FailKind::Approval, trace);
        assert!(!outcome.is_ok());
        assert_eq!(outcome.status, "approval-required");
        assert_eq!(outcome.errno, -13);
        assert!(!outcome.advice.is_empty());
        assert_eq!(outcome.trace, trace);
        assert!(outcome.code.is_none(), "未跑起来的进程没有退出码");
    }

    #[test]
    fn captured_text_keeps_its_flag() {
        let empty = Captured::default();
        assert!(!empty.truncated);
        assert!(empty.text.is_empty());
    }
}
