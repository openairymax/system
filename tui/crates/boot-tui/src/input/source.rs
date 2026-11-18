// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// L1 适配器接口与错误码
//
// 适配器只负责产事件与关闭；组合、审批与执行都不在适配器内。错误码与
// 接入契约 §7 的稳定状态串一一对应，每个变体只表达一种失败语义。
//
// SSoT: 30-interfaces/11-input-ingress.md §7、§10.2。
// ===========================================================================

use super::{RawEvent, SourceKind, Trust};

/// 输入适配器的统一接口。
pub trait InputSource: Send {
    /// 本适配器的来源种类，恒定不变。
    fn kind(&self) -> SourceKind;

    /// 最多取出 `max` 条事件；空结果表示当前无可取事件。
    fn drain(&mut self, max: usize) -> Result<Vec<RawEvent>, SourceError>;

    /// 关闭来源并释放其独占资源；重复关闭不得报错。
    fn close(&mut self) -> Result<(), SourceError>;
}

/// 适配器错误；一个变体只对应契约里的一条失败语义。
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SourceError {
    /// 语法或必填字段不通过，`status=invalid`。
    Invalid {
        /// 首个不通过的字段名。
        field: &'static str,
    },
    /// 动作未注册，`status=unsupported`。
    Unsupported {
        /// 未注册的动作名。
        action: String,
    },
    /// 信任级别不足，`status=denied`。
    Denied {
        /// 所需的最低级别。
        need: Trust,
    },
    /// 白名单未命中或黑名单命中，`status=denied`。
    Forbidden {
        /// 命中的动作类别。
        item: String,
    },
    /// 审批缺失或已过期，`status=approval-required`。
    Approval {
        /// 需要审批的动作类别。
        action: String,
    },
    /// 端到端预算耗尽，`status=timeout`。
    Timeout {
        /// 预算毫秒数。
        budget_ms: u32,
    },
    /// 未完成请求达到上限，`status=busy`。
    Busy {
        /// 当前未完成数。
        pending: usize,
    },
    /// 依赖组件不可用，`status=unavailable`。
    Unavailable {
        /// 组件名，如 `ime`、`asr`。
        component: &'static str,
    },
    /// 结果不可序列化，`status=internal`。
    Internal {
        /// 出错的事件序号。
        seq: u64,
    },
}
