// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 执行面的入口白名单与请求模型
//
// 意图只能落到打包清单里的 9 个入口：名字不在表内即拒绝，不猜相近动作。
// 实参按 argv 原样传递，不经 shell，也不做任何字符串拼接。本片（A-02-01）
// 只钉住名单与请求形状；子进程建立、管道限长与审计留给后续片。
//
// 名字与入口放在同一张表的一行里，解析与反查都只读这张表，不另设一份
// 名字清单去对齐。
//
// SSoT: 30-interfaces/11-input-ingress.md §5.1；0.0.5 §14.9（F-04b）。
// ===========================================================================

//! 白名单入口与一次执行请求的形状。

use std::io::Read;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use intent::{FailKind, TraceId};
use rustix::process::{prlimit, Pid, Resource, Rlimit};

use crate::normalize::{Captured, Outcome, PIPE_LIMIT};

/// 入口的安装目录；子进程拉起的绝对路径由它与入口名拼成。
pub const BIN_DIR: &str = "/usr/bin";

/// 子进程的 `PATH`；固定值，不继承调用方，防止换掉 `PATH` 换掉依赖。
const CHILD_PATH: &str = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";

/// 子进程的 locale；本项目只保证这一种。
const CHILD_LANG: &str = "C.UTF-8";

/// 子进程的终端类型；boot TUI 独占 tty1。
const CHILD_TERM: &str = "linux";

/// 单个动作的端到端预算。
pub const BUDGET: Duration = Duration::from_secs(10);

/// 等待子进程的轮询间隔；只影响超时的判定粒度。
const POLL: Duration = Duration::from_millis(20);

/// 单个子进程的 CPU 秒数上限；与端到端预算同量级，兜住死循环。
const CPU_SECS: u64 = 10;

/// 单个子进程可打开的文件数；入口只需少量文件与两条管道。
const FILE_MAX: u64 = 64;

/// 单个实参的字节上限；超过即拒绝，参数不是数据通道。
pub const ARG_MAX: usize = 64 << 10;

/// 取不到具体 errno 时的兜底返回码。
const EIO: i32 = 5;

/// 白名单内的入口。
#[repr(usize)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Prog {
    /// 屏幕与键盘背光。
    Bright = 0,
    /// deb 现场转 RPM。
    Deb = 1,
    /// 分区、挂载与磁盘健康。
    Disk = 2,
    /// 本地推理服务。
    Model = 3,
    /// 本地包分派。
    Pkg = 4,
    /// 电源档位。
    Power = 5,
    /// 追踪与诊断。
    Probe = 6,
    /// 实时带宽与换页上限。
    RtCap = 7,
    /// 系统更新。
    Update = 8,
}

/// 名字与入口的一一对应；行序即判别值，`airy-rt-lat.c` 是源文件不是入口。
pub const PROGRAMS: [(&str, Prog); 9] = [
    ("airy-bright", Prog::Bright),
    ("airy-deb", Prog::Deb),
    ("airy-disk", Prog::Disk),
    ("airy-model", Prog::Model),
    ("airy-pkg", Prog::Pkg),
    ("airy-power", Prog::Power),
    ("airy-probe", Prog::Probe),
    ("airy-rt-cap", Prog::RtCap),
    ("airy-update", Prog::Update),
];

impl Prog {
    /// 入口名，与打包清单逐条对应。
    pub fn name(self) -> &'static str {
        PROGRAMS[self as usize].0
    }

    /// 按名字匹配白名单；不在表内返回 `None`，调用方据此拒绝。
    pub fn parse(name: &str) -> Option<Self> {
        PROGRAMS
            .iter()
            .find(|(item, _)| *item == name)
            .map(|(_, prog)| *prog)
    }
}

/// 一次执行请求；实参已由路由层按动作定义生成，执行面不再解释语义。
#[derive(Clone, Debug)]
pub struct Request {
    /// 白名单入口。
    pub prog: Prog,
    /// 实参；原样传给子进程，不解释、不拼接、不经 shell。
    pub args: Vec<String>,
    /// 追踪号；失败与审计行都要带它。
    pub trace: TraceId,
}

/// 子进程的结束方式；与结果里的状态列一一对应。
enum End {
    /// 已退出；被信号终止时退出码为 `None`。
    Code(Option<i32>),
    /// 预算耗尽，已强制终止。
    TimedOut,
    /// 等不到子进程状态，属于执行面自身故障。
    Lost,
}

/// 执行一次已校验的请求：argv 直传、环境清洗、输出限长、预算兜底。
///
/// 进程没跑起来时不会伪造结果：状态列取 `unavailable`，返回码保留内核
/// 给出的具体 errno，调用方据此区分「程序缺失」与「权限不足」。
pub fn run(req: &Request) -> Outcome {
    if let Err(refused) = check_args(req) {
        return refused;
    }
    match start(&path_of(req.prog), &req.args) {
        Ok(mut child) => match limit(&child) {
            Ok(()) => collect(req.trace, child, BUDGET),
            Err(err) => {
                let _ = child.kill();
                let _ = child.wait();
                failed_spawn(req.trace, FailKind::Internal, &err)
            }
        },
        Err(err) => failed_spawn(req.trace, FailKind::Unavailable, &err),
    }
}

/// 实参边界：单条不超过 [`ARG_MAX`]，且不含 NUL。
///
/// 两者都会让内核截断或拒绝参数，等到 execve 再报错就分不清是入口的
/// 问题还是参数的问题；在这里挡下，状态列仍是 `invalid`。
fn check_args(req: &Request) -> Result<(), Outcome> {
    for arg in &req.args {
        if arg.len() > ARG_MAX || arg.as_bytes().contains(&0) {
            return Err(Outcome::failed(FailKind::Invalid, req.trace));
        }
    }
    Ok(())
}

/// 给子进程设资源上限；降限不需要特权，失败说明进程已不可控，直接放弃。
fn limit(child: &Child) -> std::io::Result<()> {
    let pid =
        Pid::from_raw(child.id() as i32).ok_or_else(|| std::io::Error::from_raw_os_error(EIO))?;
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

/// 拉起或约束失败的结果：状态列由调用方指定，返回码保留内核给出的具体值。
fn failed_spawn(trace: TraceId, kind: FailKind, err: &std::io::Error) -> Outcome {
    let mut outcome = Outcome::failed(kind, trace);
    outcome.errno = -err.raw_os_error().unwrap_or(EIO);
    outcome
}

/// 白名单入口的绝对路径；`prog` 已由类型限定，不来自文本。
fn path_of(prog: Prog) -> std::path::PathBuf {
    Path::new(BIN_DIR).join(prog.name())
}

/// 拉起子进程；只允许 argv 直传，不提供 shell、不拼接字符串。
fn start(path: &Path, args: &[String]) -> std::io::Result<Child> {
    let home = std::env::var_os("HOME").unwrap_or_else(|| "/".into());
    Command::new(path)
        .args(args)
        .env_clear()
        .env("PATH", CHILD_PATH)
        .env("LANG", CHILD_LANG)
        .env("TERM", CHILD_TERM)
        .env("HOME", home)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
}

/// 收集输出并在预算内等待退出；两条管道同时读，避免写满后互锁。
fn collect(trace: TraceId, mut child: Child, budget: Duration) -> Outcome {
    let out = drain(child.stdout.take());
    let err = drain(child.stderr.take());
    let end = wait_child(&mut child, budget);
    let mut outcome = match end {
        End::Code(code) => Outcome::spawned(code, trace),
        End::TimedOut => Outcome::failed(FailKind::Timeout, trace),
        End::Lost => Outcome::failed(FailKind::Internal, trace),
    };
    outcome.stdout = out.join().unwrap_or_default();
    outcome.stderr = err.join().unwrap_or_default();
    outcome
}

/// 独占一个读线程，最多保留 [`PIPE_LIMIT`] 字节；余量继续读完再丢弃。
fn drain<R: Read + Send + 'static>(pipe: Option<R>) -> thread::JoinHandle<Captured> {
    thread::spawn(move || {
        let Some(mut pipe) = pipe else {
            return Captured::default();
        };
        let mut text = Vec::with_capacity(8 << 10);
        let mut chunk = [0u8; 8 << 10];
        let mut truncated = false;
        loop {
            match pipe.read(&mut chunk) {
                Ok(0) | Err(_) => break,
                Ok(got) => {
                    let room = PIPE_LIMIT - text.len();
                    truncated |= got > room;
                    text.extend_from_slice(&chunk[..got.min(room)]);
                }
            }
        }
        Captured {
            text: String::from_utf8_lossy(&text).into_owned(),
            truncated,
        }
    })
}

/// 在预算内等待退出；超时即终止进程，不留孤儿。
fn wait_child(child: &mut Child, budget: Duration) -> End {
    let deadline = Instant::now() + budget;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return End::Code(status.code()),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return End::TimedOut;
            }
            Ok(None) => thread::sleep(POLL),
            Err(_) => return End::Lost,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn trace() -> TraceId {
        TraceId::parse("9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40").expect("合法追踪号")
    }

    fn request(args: Vec<String>) -> Request {
        Request {
            prog: Prog::Disk,
            args,
            trace: trace(),
        }
    }

    #[test]
    fn table_rows_match_their_discriminants() {
        for (row, (_, prog)) in PROGRAMS.iter().enumerate() {
            assert_eq!(*prog as usize, row, "{prog:?}");
            assert_eq!(prog.name(), PROGRAMS[row].0, "{prog:?}");
        }
    }

    #[test]
    fn names_round_trip_through_the_whitelist() {
        for (name, prog) in PROGRAMS {
            assert_eq!(Prog::parse(name), Some(prog), "{name}");
        }
    }

    #[test]
    fn names_outside_the_whitelist_are_refused() {
        for name in ["sh", "airy-rt-lat", "airy-disk ", "/usr/bin/airy-disk"] {
            assert_eq!(Prog::parse(name), None, "{name:?}");
        }
    }

    #[test]
    fn spawn_failure_keeps_the_kernel_errno() {
        let out = failed_spawn(
            trace(),
            FailKind::Unavailable,
            &std::io::Error::from_raw_os_error(2),
        );
        assert_eq!(out.status, "unavailable");
        assert_eq!(out.errno, -2, "程序缺失要报 ENOENT，不折叠成通用码");
        assert!(!out.is_ok());
    }

    #[test]
    fn nul_and_oversized_arguments_are_refused() {
        let nul = check_args(&request(vec!["a\0b".into()])).expect_err("NUL 必须拒绝");
        assert_eq!(nul.status, "invalid");
        let big = check_args(&request(vec!["x".repeat(ARG_MAX + 1)])).expect_err("超长必须拒绝");
        assert_eq!(big.errno, -22);
        assert!(
            check_args(&request(vec!["x".repeat(ARG_MAX)])).is_ok(),
            "上限本身允许"
        );
    }

    #[test]
    fn limits_reach_the_child() {
        let path = Path::new("/bin/cat");
        if !path.exists() {
            return;
        }
        let mut child = start(path, &[]).expect("cat 可拉起");
        limit(&child).expect("降限不需要特权");
        let text = std::fs::read_to_string(format!("/proc/{}/limits", child.id()));
        let _ = child.kill();
        let _ = child.wait();
        let text = text.expect("procfs 可读");
        assert_eq!(soft(&text, "Max cpu time"), CPU_SECS);
        assert_eq!(soft(&text, "Max open files"), FILE_MAX);
    }

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
    fn closed_stdin_ends_the_child_at_once() {
        let path = Path::new("/bin/cat");
        if !path.exists() {
            return;
        }
        let child = start(path, &[]).expect("cat 可拉起");
        let out = collect(trace(), child, Duration::from_secs(5));
        assert_eq!(out.status, Outcome::OK);
        assert_eq!(out.code, Some(0));
        assert!(out.stdout.text.is_empty(), "stdin 关闭后 cat 读到 EOF");
        assert!(!out.stdout.truncated);
    }

    #[test]
    fn long_output_is_cut_at_the_limit() {
        let path = Path::new("/usr/bin/yes");
        if !path.exists() {
            return;
        }
        let child = start(path, &[]).expect("yes 可拉起");
        let out = collect(trace(), child, Duration::from_millis(200));
        assert!(out.stdout.truncated, "超过上限必须标记");
        assert_eq!(out.stdout.text.len(), PIPE_LIMIT, "保留量正好到上限");
        assert_eq!(out.status, "timeout");
        assert_eq!(out.errno, -110);
        assert!(out.code.is_none(), "被终止的进程没有退出码");
    }
}
