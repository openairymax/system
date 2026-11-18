// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 子进程的资源边界
//
// 入口都是短命的本地命令，不需要在整机上自保，但不能因为一个死循环或
// 文件泄漏拖垮会话。这里只做两件可核对的事：CPU 秒数与可打开文件数，
// 软硬上限同值，子进程无法自行调高。
//
// 降限在 execve 之后按 pid 施加（prlimit），不用 pre_exec 回调，工作区
// 的 unsafe 禁令因此不需要为它开口子。施加失败说明进程已不可控，调用方
// 应当终止它，而不是继续等一个没有边界的进程。
//
// SSoT: 0.0.5 §14.9；30-interfaces/11-input-ingress.md §5.1。
// ===========================================================================

//! 子进程的 CPU 与文件描述符上限。

use std::io;
use std::process::Child;

use rustix::process::{prlimit, Pid, Resource, Rlimit};

/// 单个子进程的 CPU 秒数上限；与端到端预算同量级，兜住死循环。
pub const CPU_SECS: u64 = 10;

/// 单个子进程可打开的文件数；入口只需少量文件与两条管道。
pub const FILE_MAX: u64 = 64;

/// 取不到子进程 pid 时的兜底返回码。
const EIO: i32 = 5;

/// 给子进程施加两条上限；降限不需要特权，失败即返回内核 errno。
pub fn apply(child: &Child) -> io::Result<()> {
    let pid = Pid::from_raw(child.id() as i32).ok_or_else(|| io::Error::from_raw_os_error(EIO))?;
    prlimit(Some(pid), Resource::Cpu, cap(CPU_SECS))?;
    prlimit(Some(pid), Resource::Nofile, cap(FILE_MAX))?;
    Ok(())
}

/// 软硬上限同值：子进程既不能越过，也不能自己调高。
fn cap(value: u64) -> Rlimit {
    Rlimit {
        current: Some(value),
        maximum: Some(value),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::{Command, Stdio};

    /// 取 `/proc/<pid>/limits` 一行里的软限数字：`Max cpu time <软> <硬> 单位`。
    fn soft(text: &str, prefix: &str) -> u64 {
        let line = text
            .lines()
            .find(|line| line.starts_with(prefix))
            .unwrap_or_else(|| panic!("缺少 {prefix}"));
        let field = line.split_whitespace().nth(3).expect("软限字段");
        field.parse().expect("软限是数字")
    }

    #[test]
    fn limits_reach_the_child() {
        let mut child = match Command::new("/bin/sleep")
            .arg("5")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .spawn()
        {
            Ok(child) => child,
            Err(_) => return,
        };
        apply(&child).expect("降限不需要特权");
        let text = std::fs::read_to_string(format!("/proc/{}/limits", child.id()));
        let _ = child.kill();
        let _ = child.wait();
        let text = text.expect("procfs 可读");
        assert_eq!(soft(&text, "Max cpu time"), CPU_SECS);
        assert_eq!(soft(&text, "Max open files"), FILE_MAX);
    }
}
