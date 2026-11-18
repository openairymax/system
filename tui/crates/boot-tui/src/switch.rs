// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 运行期会话切换
//
// 引导期选择只决定这一次开机进哪个会话；运行期还需要一条不重启机器的双向
// 通路。本模块只放判定与状态页读写，进程编排（起停单元、等就绪、切前台）在
// 二进制 `airy-session-mode` 里，因此判定部分可在没有 systemd 的测试里断言。
//
// 两页的分工：`desired` 是意图，只在启单元前写，唯一的读者是单元的
// `ExecCondition`；`mode` 是实际态，只在会话真正就绪后写，其余程序只读它。
// 这里不提供读 `desired` 的入口：运行期入口若读了意图面，就会在切换进行中
// 把「已在切」判成「已就绪」。
//
// 失败只落 journal，不在状态目录留第三页：任何第三个文件都会变成第二处
// 真相（§A.2.2），而 S-6 的判据本身也只认 journal 的 `switch=failed:`。
//
// SSoT: 0.0.5 方案 §14.15 不变量 1–8。
// ===========================================================================

//! 运行期会话切换的判定与状态页读写。

use std::fs;
use std::io;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

use crate::pick::Choice;

/// GUI 方向等就绪的秒数；与就绪探针自己的 90 秒窗口一致。
pub const GUI_WAIT: u64 = 90;

/// TUI 方向等就绪的秒数：`Type=notify` 的 READY 在首帧渲染后到达。
pub const TUI_WAIT: u64 = 15;

/// lightdm 就绪后仍未见前台切换时的兜底等待。
pub const FG_WAIT: u64 = 30;

/// 状态目录里的实际态；缺文件或无法解析都返回 `None`。
pub fn read_mode(dir: &Path) -> Option<Choice> {
    let text = fs::read_to_string(dir.join("mode")).ok()?;
    Choice::parse(text.trim())
}

/// 只写意图面：切换开始前落盘，让 `ExecCondition` 立即按新目标判定。
pub fn write_want(dir: &Path, choice: Choice) -> io::Result<()> {
    write_page(dir, "desired", choice)
}

/// 成对写两页：`mode` 先落，`desired` 随后对齐为同一值。
pub fn write_pages(dir: &Path, choice: Choice) -> io::Result<()> {
    write_page(dir, "mode", choice)?;
    write_page(dir, "desired", choice)
}

/// 同目录临时文件加 rename：读方要么看到旧值，要么看到新值，不会读到半行。
fn write_page(dir: &Path, name: &str, choice: Choice) -> io::Result<()> {
    fs::create_dir_all(dir)?;
    fs::set_permissions(dir, fs::Permissions::from_mode(0o755))?;
    let temp = dir.join(format!(".{name}.new"));
    fs::write(&temp, format!("{}\n", choice.word()))?;
    fs::rename(&temp, dir.join(name))
}

/// 该方向等就绪的秒数。
pub fn wait_secs(want: Choice) -> u64 {
    match want {
        Choice::Gui => GUI_WAIT,
        Choice::Tui => TUI_WAIT,
    }
}

/// 回滚目标：旧模式缺失即 GUI，与「缺文件即 GUI」的失败安全默认同向。
pub fn fallback(old: Option<Choice>) -> Choice {
    old.unwrap_or(Choice::Gui)
}

/// 只有 `running` 与 `degraded` 允许切换：`starting`／`stopping` 期间启停
/// 单元会与关机或早期引导抢同一个作业队列。
pub fn ready_state(state: &str) -> bool {
    matches!(state.trim(), "running" | "degraded")
}

/// 切换成功后的 journal 行。
pub fn mode_line(from: Option<Choice>, to: Choice, reason: &str) -> String {
    let from = from.map_or("none", Choice::word);
    format!(
        "AIRYMAX-SESSION-MODE from={from} to={} reason={reason}",
        to.word()
    )
}

/// 回滚时的 journal 行；`cause` 是判负原因而不是退出码。
pub fn fail_line(to: Choice, cause: &str) -> String {
    format!(
        "AIRYMAX-SESSION-SWITCH to={} switch=failed:{cause}",
        to.word()
    )
}

#[cfg(test)]
mod tests {
    use super::{
        fail_line, fallback, mode_line, read_mode, ready_state, wait_secs, write_pages, write_want,
        GUI_WAIT, TUI_WAIT,
    };
    use crate::pick::{Choice, DIR};
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    fn scratch(tag: &str) -> PathBuf {
        std::env::temp_dir().join(format!("boot-tui-switch-{tag}-{}", std::process::id()))
    }

    #[test]
    fn a_missing_or_bad_page_reads_as_none() {
        let dir = scratch("read");
        assert_eq!(read_mode(&dir), None, "目录都不存在");
        std::fs::create_dir_all(&dir).expect("建目录");
        std::fs::write(dir.join("mode"), "desktop\n").expect("写入");
        assert_eq!(read_mode(&dir), None, "非两词的取值不猜");
        std::fs::write(dir.join("mode"), "tui\n").expect("写入");
        assert_eq!(read_mode(&dir), Some(Choice::Tui));
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn both_pages_are_written_atomically() {
        let dir = scratch("both");
        write_pages(&dir, Choice::Gui).expect("写入");
        for name in ["mode", "desired"] {
            assert_eq!(
                std::fs::read_to_string(dir.join(name)).expect("读取"),
                "gui\n",
                "{name}"
            );
            assert!(
                !dir.join(format!(".{name}.new")).exists(),
                "{name} 临时文件残留"
            );
        }
        let perms = std::fs::metadata(&dir)
            .expect("元数据")
            .permissions()
            .mode();
        assert_eq!(perms & 0o777, 0o755, "状态目录对用户只读");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_intent_page_is_written_alone() {
        let dir = scratch("want");
        write_pages(&dir, Choice::Gui).expect("先写两页");
        write_want(&dir, Choice::Tui).expect("只写意图");
        assert_eq!(read_mode(&dir), Some(Choice::Gui), "实际态不能被意图态改写");
        assert_eq!(
            std::fs::read_to_string(dir.join("desired")).expect("读取"),
            "tui\n"
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn each_direction_has_its_own_window() {
        assert_eq!(wait_secs(Choice::Gui), GUI_WAIT);
        assert_eq!(wait_secs(Choice::Tui), TUI_WAIT);
    }

    #[test]
    fn only_settled_states_may_switch() {
        for state in ["running", "degraded", "running\n"] {
            assert!(ready_state(state), "{state:?}");
        }
        for state in ["starting", "stopping", "offline", "maintenance", ""] {
            assert!(!ready_state(state), "{state:?}");
        }
    }

    #[test]
    fn rollback_target_defaults_to_gui() {
        assert_eq!(fallback(None), Choice::Gui);
        assert_eq!(fallback(Some(Choice::Tui)), Choice::Tui);
    }

    #[test]
    fn journal_lines_carry_the_fields_the_gate_reads() {
        let line = mode_line(Some(Choice::Tui), Choice::Gui, "logout");
        assert_eq!(line, "AIRYMAX-SESSION-MODE from=tui to=gui reason=logout");
        assert!(mode_line(None, Choice::Tui, "key").contains("from=none"));
        assert_eq!(
            fail_line(Choice::Gui, "ready-timeout"),
            "AIRYMAX-SESSION-SWITCH to=gui switch=failed:ready-timeout"
        );
        assert_eq!(DIR, "/run/airymaxos/session");
    }
}
