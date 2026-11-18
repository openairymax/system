// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 失败语义表
//
// 契约 §7 把「稳定状态串、返回码、可执行建议」三列钉在同一条失败语义上。
// 接入侧、会话层与执行面都会失败，但写进日志、状态栏与审计行的必须是同
// 一份三列，否则同一种失败在两个组件里显示不同的状态串，用户无法据此采
// 取下一步，审计也无法对齐。
//
// 三列因此只在这里定义一次：其余层按语义取用，不各写一份 match。判别值
// 就是表行号，新增类别时两处必须一起改；文件末尾的常量断言在漏行时直接
// 让编译失败。
//
// SSoT: 30-interfaces/11-input-ingress.md §7。
// ===========================================================================

/// 失败类别；一个类别只对应契约里的一条失败语义。
#[repr(usize)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FailKind {
    /// 语法或必填字段不通过，`status=invalid`。
    Invalid = 0,
    /// 动作未注册，`status=unsupported`。
    Unsupported = 1,
    /// 信任级别不足，`status=denied`。
    Denied = 2,
    /// 白名单未命中或黑名单命中，`status=denied`。
    Forbidden = 3,
    /// 审批缺失或已过期，`status=approval-required`。
    Approval = 4,
    /// 端到端预算耗尽，`status=timeout`。
    Timeout = 5,
    /// 未完成请求达到上限，`status=busy`。
    Busy = 6,
    /// 依赖组件不可用，`status=unavailable`。
    Unavailable = 7,
    /// 结果不可序列化，`status=internal`。
    Internal = 8,
}

/// 契约 §7 的一行：稳定状态串、返回码与可执行建议。
struct Meta {
    status: &'static str,
    errno: i32,
    advice: &'static str,
}

impl Meta {
    const fn new(status: &'static str, errno: i32, advice: &'static str) -> Self {
        Self {
            status,
            errno,
            advice,
        }
    }
}

/// 契约 §7 用到的 errno 值；Linux 通用编号，x86、ARM、RISC-V 一致。
const EPERM: i32 = 1;
const EIO: i32 = 5;
const EAGAIN: i32 = 11;
const EACCES: i32 = 13;
const EINVAL: i32 = 22;
/// `EOPNOTSUPP` 的同义词，本系统统一使用 `ENOTSUP` 这一名字。
const ENOTSUP: i32 = 95;
const ETIMEDOUT: i32 = 110;

/// 按 [`FailKind`] 的判别值排列；一行对应契约表的一行。
const META: [Meta; 9] = [
    Meta::new("invalid", -EINVAL, "检查字段与语法后重试"),
    Meta::new("unsupported", -EINVAL, "改用已注册的动作"),
    Meta::new("denied", -EACCES, "提升级别或改用允许的通道"),
    Meta::new("denied", -EPERM, "从 deny 移除该动作"),
    Meta::new("approval-required", -EACCES, "完成本地审批后重试"),
    Meta::new("timeout", -ETIMEDOUT, "放宽预算或缩小范围"),
    Meta::new("busy", -EAGAIN, "等待在途请求返回后重试"),
    Meta::new("unavailable", -ENOTSUP, "确认依赖组件已启动"),
    Meta::new("internal", -EIO, "记录 trace_id 并上报缺陷"),
];

/// 表长必须覆盖最后一个类别的行号；新增类别漏行时在此编译失败。
const _: () = assert!(META.len() == FailKind::Internal as usize + 1);

impl FailKind {
    /// 稳定的状态串，可直接写进日志与状态栏。
    pub fn status(self) -> &'static str {
        self.meta().status
    }

    /// 契约 §7 的返回码，统一取负值。
    pub fn errno(self) -> i32 {
        self.meta().errno
    }

    /// 可执行建议；不含推测，只给本机能做的一步。
    pub fn advice(self) -> &'static str {
        self.meta().advice
    }

    fn meta(self) -> &'static Meta {
        &META[self as usize]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ALL: [FailKind; 9] = [
        FailKind::Invalid,
        FailKind::Unsupported,
        FailKind::Denied,
        FailKind::Forbidden,
        FailKind::Approval,
        FailKind::Timeout,
        FailKind::Busy,
        FailKind::Unavailable,
        FailKind::Internal,
    ];

    #[test]
    fn order_matches_the_table() {
        for (row, kind) in ALL.iter().enumerate() {
            assert_eq!(*kind as usize, row, "类别顺序必须与元数据表一致");
        }
    }

    #[test]
    fn three_columns_are_present() {
        for kind in ALL {
            assert!(!kind.status().is_empty(), "{kind:?}");
            assert!(!kind.advice().is_empty(), "{kind:?}");
            assert!(kind.errno() < 0, "{kind:?}");
        }
    }
}
