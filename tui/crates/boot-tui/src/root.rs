// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 会话入口的根前缀接缝
//
// 两个会话二进制把 systemctl／journalctl／chvt／loginctl／fgconsole／ps、
// 会话状态目录、/dev/tty1 与 /proc/cmdline 写成绝对路径。端到端测试要让
// 真实二进制跑在临时根里，才能验证「写 desired → 停旧确认 → 起新 → 等
// 就绪 → 写 mode → 落 journal → 失败回滚」这套编排本身；测试若自己重写
// 一遍编排，验证到的只是桩，不是产品路径。
//
// 接缝只有一个环境变量 AIRY_SESSION_ROOT（未设置即真实根）。除路径前缀
// 外不改变任何行为：根为空时拼出的路径与原先的硬编码逐字相同。
// ===========================================================================

//! 会话入口的根前缀接缝。

use std::path::PathBuf;

/// 临时根的变量名；生产环境不设置即等价于真实根。
pub const ROOT_ENV: &str = "AIRY_SESSION_ROOT";

/// 把绝对路径挂到根前缀下；根为空或 `/` 时原样返回，不产生 `//`。
pub fn path(p: &str) -> PathBuf {
    let root = std::env::var(ROOT_ENV).unwrap_or_default();
    join(&root, p)
}

/// 纯拼接：去掉根尾部的 `/` 后直接相连；空根保留被测路径的原始写法。
fn join(root: &str, p: &str) -> PathBuf {
    let root = root.trim_end_matches('/');
    if root.is_empty() {
        PathBuf::from(p)
    } else {
        PathBuf::from(format!("{root}{p}"))
    }
}

#[cfg(test)]
mod tests {
    use super::join;
    use std::path::PathBuf;

    #[test]
    fn an_empty_root_keeps_the_path_verbatim() {
        assert_eq!(
            join("", "/usr/bin/systemctl"),
            PathBuf::from("/usr/bin/systemctl")
        );
        assert_eq!(join("/", "/dev/tty1"), PathBuf::from("/dev/tty1"));
    }

    #[test]
    fn a_root_prefix_is_joined_without_a_double_slash() {
        assert_eq!(
            join("/tmp/x", "/run/airymaxos/session"),
            PathBuf::from("/tmp/x/run/airymaxos/session")
        );
        assert_eq!(
            join("/tmp/x/", "/dev/tty1"),
            PathBuf::from("/tmp/x/dev/tty1")
        );
    }
}
