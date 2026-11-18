// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 执行面的入口白名单与请求模型
//
// 意图只能落到打包清单里的 9 个入口：名字不在表内即拒绝，不猜相近动作。
// 实参按 argv 原样传递，不经 shell，也不做任何字符串拼接。审批与资源
// 边界分别见 `approve` 与 `sandbox`，本模块只负责拉起、读取与归约。
//
// 名字与入口放在同一张表的一行里，解析与反查都只读这张表，不另设一份
// 名字清单去对齐。
//
// SSoT: 30-interfaces/11-input-ingress.md §5.1；0.0.5 §14.9（F-04b）。
// ===========================================================================

//! 白名单入口、一次执行请求的形状与结果归约。

use std::io::Read;
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use intent::{sha256, FailKind, TraceId};

use crate::approve;
use crate::normalize::{Captured, Outcome, PIPE_LIMIT};
use crate::sandbox;

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

/// 动作的风险级别；高危动作必须有 root 预置的批准文件。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Risk {
    /// 只读或可回退的动作。
    Normal,
    /// 改变系统状态、执行前必须命中批准文件。
    High,
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
    /// 动作名；批准文件按 `<action>.conf` 命名。
    pub action: &'static str,
    /// 风险级别；决定是否必须过审批链。
    pub risk: Risk,
}

/// 已注册动作：固定入口、固定实参与风险级别。
///
/// 动作名与实参写在同一行，发起方无法追加、改写或替换参数；这既收窄了
/// 注入面，也让「同一条命令」与「同一个批准摘要」一一对应。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Action {
    /// 动作名，形如 `disk.status`。
    pub name: &'static str,
    /// 白名单入口。
    pub prog: Prog,
    /// 固定实参，按 argv 原样传给入口。
    pub argv: &'static [&'static str],
    /// 风险级别；只读动作均为普通级别。
    pub risk: Risk,
}

/// 只读动作的行构造：入口与实参成对写死，风险恒为普通。
const fn readonly(name: &'static str, prog: Prog, argv: &'static [&'static str]) -> Action {
    Action {
        name,
        prog,
        argv,
        risk: Risk::Normal,
    }
}

/// 注册表：只登记实测跑通的只读动作。
///
/// 未知动作一律拒绝（契约 §7 判 `unsupported`），不按前缀或相似度猜一个
/// 相近动作。会改状态的动作需要先有逐动作的参数校验与审批路径，故不在
/// 本表内登记。
pub const ACTIONS: [Action; 8] = [
    readonly("bright.status", Prog::Bright, &["status", "--json"]),
    readonly("disk.status", Prog::Disk, &["status", "--json"]),
    readonly("disk.layout", Prog::Disk, &["layout", "--json"]),
    readonly("disk.health", Prog::Disk, &["health", "--json"]),
    readonly("model.status", Prog::Model, &["status", "--json"]),
    readonly("power.status", Prog::Power, &["status", "--json"]),
    readonly("probe.status", Prog::Probe, &["status", "--json"]),
    readonly("rtcap.matrix", Prog::RtCap, &["matrix", "--json"]),
];

impl Action {
    /// 按动作名精确匹配；不在表内返回 `None`。
    pub fn find(name: &str) -> Option<&'static Self> {
        ACTIONS.iter().find(|item| item.name == name)
    }

    /// 以固定实参构造执行请求；追踪号随行，动作名即批准文件名的主干。
    pub fn request(&self, trace: TraceId) -> Request {
        Request {
            prog: self.prog,
            args: self.argv.iter().map(|arg| (*arg).to_string()).collect(),
            trace,
            action: self.name,
            risk: self.risk,
        }
    }
}

/// 审批与审计共用的摘要：入口名与实参按 NUL 连接后取 SHA-256。
pub fn digest(req: &Request) -> String {
    let mut bytes = req.prog.name().as_bytes().to_vec();
    for arg in &req.args {
        bytes.push(0);
        bytes.extend_from_slice(arg.as_bytes());
    }
    sha256::hex(&bytes)
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
    if let Some(refused) = check_args(req) {
        return refused;
    }
    let permit = match permit_of(req) {
        Ok(permit) => permit,
        Err(reason) => return Outcome::refused(reason, req.trace),
    };
    let mut outcome = launch(req);
    outcome.approval = permit;
    outcome
}

/// 高危动作过审批链；普通动作直接放行，返回命中的批准文件名。
fn permit_of(req: &Request) -> Result<Option<String>, approve::Refusal> {
    if req.risk == Risk::Normal {
        return Ok(None);
    }
    approve::check(req.action, &digest(req)).map(|permit| Some(permit.file))
}

/// 拉起子进程并收集结果；调用前实参与审批均已通过。
fn launch(req: &Request) -> Outcome {
    match start(&path_of(req.prog), &req.args) {
        Ok(mut child) => match sandbox::apply(&child) {
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
fn check_args(req: &Request) -> Option<Outcome> {
    let bad = req
        .args
        .iter()
        .any(|arg| arg.len() > ARG_MAX || arg.as_bytes().contains(&0));
    bad.then(|| Outcome::failed(FailKind::Invalid, req.trace))
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
            action: "test.action",
            risk: Risk::Normal,
        }
    }

    #[test]
    fn digest_covers_the_program_and_every_argument() {
        let mut req = request(vec!["grow".into(), "/dev/sda".into()]);
        req.action = "disk.grow";
        assert_eq!(
            digest(&req),
            "bd59c37587ac7c73673722b0044c4c6b2527684c937fccfe2ac6a7e876790667"
        );
    }

    #[test]
    fn registered_actions_form_a_closed_read_only_table() {
        let mut seen = Vec::new();
        for action in &ACTIONS {
            assert!(!action.argv.is_empty(), "{}", action.name);
            assert_eq!(
                action.argv.last(),
                Some(&"--json"),
                "{}：状态面只消费 JSON 输出",
                action.name
            );
            assert_eq!(action.risk, Risk::Normal, "{}", action.name);
            assert_eq!(Prog::parse(action.prog.name()), Some(action.prog));
            assert!(
                seen.iter().all(|name| *name != action.name),
                "{}",
                action.name
            );
            seen.push(action.name);
        }
    }

    #[test]
    fn actions_resolve_by_exact_name_only() {
        for action in &ACTIONS {
            assert_eq!(
                Action::find(action.name).map(|item| item.name),
                Some(action.name)
            );
        }
        for name in ["disk", "disk.stat", "disk.status ", "Disk.status", ""] {
            assert!(Action::find(name).is_none(), "{name:?}");
        }
    }

    #[test]
    fn request_carries_the_registered_argv() {
        let action = Action::find("disk.status").expect("已注册");
        let req = action.request(trace());
        assert_eq!(req.prog, Prog::Disk);
        assert_eq!(req.args, ["status", "--json"]);
        assert_eq!(req.action, "disk.status");
        assert_eq!(req.risk, Risk::Normal);
        assert_eq!(req.trace, trace());
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
        let nul = check_args(&request(vec!["a\0b".into()])).expect("NUL 必须拒绝");
        assert_eq!(nul.status, "invalid");
        let big = check_args(&request(vec!["x".repeat(ARG_MAX + 1)])).expect("超长必须拒绝");
        assert_eq!(big.errno, -22);
        assert!(
            check_args(&request(vec!["x".repeat(ARG_MAX)])).is_none(),
            "上限本身允许"
        );
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
