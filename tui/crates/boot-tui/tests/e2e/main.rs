// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// V-02 会话选择与运行期切换的端到端取证
//
// 这里启动的是 CARGO_BIN_EXE_* 指向的真实二进制，而不是把 switch.rs 的
// 编排在测试里重写一遍——重写只能证明测试自己会演戏，证明不了产品路径。
// 「写 desired → 停旧确认 inactive → 起新 → 等就绪 → 写 mode → 落 journal
// → 失败回滚」这套次序必须由真实进程跑一遍。
//
// 真实二进制通过唯一的 AIRY_SESSION_ROOT 接缝（见 src/root.rs）把
// systemctl／journalctl／chvt／loginctl／fgconsole／ps、会话状态目录、
// /dev/tty1 与 /proc/cmdline 指向临时根；根里放的是会记录状态的假命令。
// 断言只读假命令留下的调用记录、单元状态与 journal，不读被测进程的
// 内部状态。
//
// 覆盖 AC-A10：冷启动两侧选择、命令行旁路、双向切换、注销回落、忙碌拒绝、
// 失败回滚、幂等；全程不重启机器。
// ===========================================================================

//! 会话选择与运行期切换的端到端判据。

use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

/// 被测的运行期切换入口。
const MODE: &str = env!("CARGO_BIN_EXE_airy-session-mode");
/// 被测的引导期选择入口。
const PICK: &str = env!("CARGO_BIN_EXE_airy-session-pick");
/// 临时根内会话状态目录的相对位置。
const SESSION: &str = "run/airymaxos/session";

static SEQ: AtomicU64 = AtomicU64::new(0);

/// 每个用例独占的临时根；析构时整棵删掉，断言失败也不留垃圾。
struct Root {
    dir: PathBuf,
}

impl Root {
    fn new(tag: &str) -> Self {
        let seq = SEQ.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |elapsed| elapsed.as_nanos());
        let dir = std::env::temp_dir().join(format!(
            "airy-e2e-{tag}-{}-{seq}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).expect("建临时根");
        let root = Self { dir };
        root.prepare();
        root
    }

    /// 铺好假命令、命令行、tty1 与假命令的初始状态。
    fn prepare(&self) {
        self.write("proc/cmdline", "\n");
        self.write("dev/tty1", "");
        self.write("fake/journal.log", "");
        self.write("fake/fg", "tty1\n");
        self.write("fake/mainpid", "4242\n");
        self.write("fake/tpgid", "4242\n");
        self.write("fake/system-state", "running\n");
        for (name, body) in FAKES {
            self.install(name, body);
        }
    }

    fn at(&self, rel: &str) -> PathBuf {
        self.dir.join(rel)
    }

    fn write(&self, rel: &str, text: &str) {
        let path = self.at(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).expect("建目录");
        }
        fs::write(&path, text).expect("写文件");
    }

    /// 假命令必须是可执行的：真实二进制用 Command 直接拉起它们。
    fn install(&self, name: &str, body: &str) {
        let rel = format!("usr/bin/{name}");
        self.write(&rel, body);
        fs::set_permissions(self.at(&rel), fs::Permissions::from_mode(0o755)).expect("赋可执行位");
    }

    fn text(&self, rel: &str) -> String {
        fs::read_to_string(self.at(rel)).unwrap_or_default()
    }

    /// 读状态目录里的一页（mode／desired）。
    fn page(&self, name: &str) -> String {
        self.text(&format!("{SESSION}/{name}"))
    }

    /// 预置状态页，模拟上一次开机留下的实际态。
    fn seed_page(&self, name: &str, word: &str) {
        self.write(&format!("{SESSION}/{name}"), &format!("{word}\n"));
    }

    /// 预置单元状态，让「停旧」真的有东西可停。
    fn unit(&self, name: &str, state: &str) {
        self.write(&format!("fake/units/{name}"), &format!("{state}\n"));
    }

    /// `--dir` 要的是绝对路径，这里换成字符串交给命令行。
    fn session(&self) -> String {
        self.at(SESSION).to_string_lossy().into_owned()
    }

    fn calls(&self) -> String {
        self.text("fake/calls.log")
    }

    fn called(&self, needle: &str) -> bool {
        self.calls().lines().any(|line| line.contains(needle))
    }

    fn mode(&self, args: &[&str]) -> Output {
        self.spawn(MODE, args)
    }

    fn pick(&self, args: &[&str]) -> Output {
        self.spawn(PICK, args)
    }

    fn spawn(&self, exe: &str, args: &[&str]) -> Output {
        Command::new(exe)
            .args(args)
            .env("AIRY_SESSION_ROOT", &self.dir)
            .output()
            .expect("启动被测二进制")
    }
}

impl Drop for Root {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.dir);
    }
}

fn code(out: &Output) -> i32 {
    out.status.code().unwrap_or(-1)
}

fn stderr(out: &Output) -> String {
    String::from_utf8_lossy(&out.stderr).into_owned()
}

fn stdout(out: &Output) -> String {
    String::from_utf8_lossy(&out.stdout).into_owned()
}

fn expect_code(out: &Output, want: i32, what: &str) {
    let got = code(out);
    assert_eq!(
        got,
        want,
        "{what}：期望退出码 {want}，实际 {got}；stderr={}",
        stderr(out)
    );
}

/// 状态页按词比较：写盘实现总是带一个换行。
fn expect_page(root: &Root, name: &str, want: &str) {
    let got = root.page(name);
    assert_eq!(got.trim(), want, "期望 {name}={want}，实际 {got:?}");
}

fn expect_no_start(root: &Root) {
    assert!(
        !root.called("start"),
        "不应发生任何单元启动，实际调用：{}",
        root.calls()
    );
}

#[test]
fn 冷启动默认进桌面() {
    let root = Root::new("pick-gui");
    let dir = root.session();
    let out = root.pick(&["--dir", &dir, "--timeout", "0", "--default", "gui"]);
    expect_code(&out, 0, "冷启动默认 GUI");
    expect_page(&root, "desired", "gui");
    expect_no_start(&root);
}

#[test]
fn 冷启动选择终端界面() {
    let root = Root::new("pick-tui");
    let dir = root.session();
    let out = root.pick(&["--dir", &dir, "--timeout", "0", "--default", "tui"]);
    expect_code(&out, 0, "冷启动选择 TUI");
    expect_page(&root, "desired", "tui");
    assert!(
        root.called("systemctl --no-block start boot-tui.service"),
        "TUI 分支必须显式拉起 boot-tui，实际调用：{}",
        root.calls()
    );
}

#[test]
fn 命令行旁路强制桌面() {
    let root = Root::new("pick-off");
    root.write("proc/cmdline", "ro quiet airymax.session=off\n");
    let dir = root.session();
    let out = root.pick(&["--dir", &dir, "--timeout", "0", "--default", "tui"]);
    expect_code(&out, 0, "命令行旁路");
    expect_page(&root, "desired", "gui");
    assert!(
        stderr(&out).contains("reason=cmdline"),
        "期望 journal 记 reason=cmdline，实际 stderr={}",
        stderr(&out)
    );
    expect_no_start(&root);
}

#[test]
fn 终端切到桌面成功() {
    let root = Root::new("to-gui");
    root.seed_page("mode", "tui");
    root.seed_page("desired", "tui");
    root.unit("boot-tui.service", "active");
    let out = root.mode(&["--switch", "gui"]);
    expect_code(&out, 0, "TUI→GUI");
    expect_page(&root, "mode", "gui");
    expect_page(&root, "desired", "gui");
    assert!(
        root.called("systemctl stop boot-tui.service"),
        "必须停掉 TUI，实际调用：{}",
        root.calls()
    );
    assert!(
        root.called("start airymax-desktop-ready.service lightdm.service"),
        "必须拉起探针与座位，实际调用：{}",
        root.calls()
    );
    assert!(
        root.called("chvt 7"),
        "前台未自动切换时必须兜底 chvt 7，实际调用：{}",
        root.calls()
    );
    assert!(
        stderr(&out).contains("AIRYMAX-SESSION-MODE from=tui to=gui reason=switch"),
        "期望 journal 成功行，实际 stderr={}",
        stderr(&out)
    );
}

#[test]
fn 桌面切到终端成功() {
    let root = Root::new("to-tui");
    root.seed_page("mode", "gui");
    root.seed_page("desired", "gui");
    root.unit("lightdm.service", "active");
    let out = root.mode(&["--switch", "tui"]);
    expect_code(&out, 0, "GUI→TUI");
    expect_page(&root, "mode", "tui");
    expect_page(&root, "desired", "tui");
    assert!(
        root.called("systemctl stop lightdm.service"),
        "必须停掉桌面，实际调用：{}",
        root.calls()
    );
    assert!(
        root.called("start boot-tui.service"),
        "必须拉起 TUI，实际调用：{}",
        root.calls()
    );
    assert!(
        root.called("chvt 1"),
        "就绪后必须把 tty1 切到前台，实际调用：{}",
        root.calls()
    );
    assert!(
        stderr(&out).contains("AIRYMAX-SESSION-MODE from=gui to=tui reason=switch"),
        "期望 journal 成功行，实际 stderr={}",
        stderr(&out)
    );
}

#[test]
fn 注销回落记录原因() {
    let root = Root::new("logout");
    root.seed_page("mode", "gui");
    root.seed_page("desired", "gui");
    root.write("run/airymaxos/session/switch-reason", "logout\n");
    let out = root.mode(&["--switch", "gui"]);
    expect_code(&out, 0, "注销回落");
    expect_page(&root, "mode", "gui");
    assert!(
        stderr(&out).contains("reason=logout"),
        "期望 journal 记 reason=logout，实际 stderr={}",
        stderr(&out)
    );
}

#[test]
fn 系统忙时拒绝切换() {
    let root = Root::new("busy");
    root.seed_page("mode", "gui");
    root.seed_page("desired", "gui");
    root.write("fake/system-state", "starting\n");
    let out = root.mode(&["tui"]);
    expect_code(&out, 4, "系统忙时拒绝切换");
    expect_page(&root, "mode", "gui");
    expect_page(&root, "desired", "gui");
    expect_no_start(&root);
}

#[test]
fn 停旧失败时回滚() {
    let root = Root::new("rollback");
    root.seed_page("mode", "gui");
    root.seed_page("desired", "gui");
    root.unit("lightdm.service", "active");
    root.write("fake/stop-blocked", "");
    let out = root.mode(&["--switch", "tui"]);
    expect_code(&out, 1, "停旧失败");
    expect_page(&root, "mode", "gui");
    expect_page(&root, "desired", "gui");
    assert!(
        stderr(&out).contains("AIRYMAX-SESSION-SWITCH to=tui switch=failed:still-active"),
        "期望 journal 记失败判据，实际 stderr={}",
        stderr(&out)
    );
    assert!(
        !root.called("start boot-tui.service"),
        "停旧未确认前不得拉起新会话，实际调用：{}",
        root.calls()
    );
}

#[test]
fn 目标模式相同则幂等() {
    let root = Root::new("same");
    root.seed_page("mode", "gui");
    root.seed_page("desired", "gui");
    let out = root.mode(&["gui"]);
    expect_code(&out, 0, "目标模式相同");
    assert!(
        stdout(&out).contains("mode=gui state=unchanged"),
        "期望幂等标记，实际 stdout={}",
        stdout(&out)
    );
    expect_no_start(&root);
}

/// 六个假命令的脚本正文；状态与留痕都落在临时根的 fake/ 下。
const FAKES: [(&str, &str); 6] = [
    ("systemctl", SYSTEMCTL),
    ("journalctl", JOURNALCTL),
    ("chvt", CHVT),
    ("loginctl", LOGINCTL),
    ("fgconsole", FGCONSOLE),
    ("ps", PS),
];

const SYSTEMCTL: &str = r#"#!/bin/sh
# 假 systemctl：单元状态机、调用留痕与就绪标记。
R="${AIRY_SESSION_ROOT:-/}"
F="$R/fake"
mkdir -p "$F/units"
printf 'systemctl %s\n' "$*" >>"$F/calls.log"
put() { printf '%s\n' "$2" >"$F/units/$1"; }
get() { cat "$F/units/$1" 2>/dev/null || printf 'inactive\n'; }
cmd=""
while [ $# -gt 0 ]; do
    case "$1" in
    -*) shift ;;
    *)
        cmd="$1"
        shift
        break
        ;;
    esac
done
case "$cmd" in
is-system-running)
    cat "$F/system-state" 2>/dev/null || printf 'running\n'
    ;;
is-active)
    get "$1"
    ;;
stop)
    [ -e "$F/stop-blocked" ] || put "$1" inactive
    ;;
start)
    for u in "$@"; do
        case "$u" in
        -*) continue ;;
        esac
        if [ -e "$F/no-start-$u" ]; then
            put "$u" failed
        else
            put "$u" active
        fi
        [ "$u" = airymax-desktop-ready.service ] &&
            printf 'AIRYMAX-DESKTOP-READY\n' >>"$F/journal.log"
    done
    ;;
show)
    cat "$F/mainpid" 2>/dev/null || printf '4242\n'
    ;;
esac
exit 0
"#;

const JOURNALCTL: &str = r#"#!/bin/sh
# 假 journalctl：只回放本次切换写入的就绪标记。
R="${AIRY_SESSION_ROOT:-/}"
printf 'journalctl %s\n' "$*" >>"$R/fake/calls.log"
cat "$R/fake/journal.log" 2>/dev/null
exit 0
"#;

const CHVT: &str = r#"#!/bin/sh
# 假 chvt：记录调用并把当前控制台改成目标 VT。
R="${AIRY_SESSION_ROOT:-/}"
printf 'chvt %s\n' "$*" >>"$R/fake/calls.log"
case "$1" in
tty*) printf '%s\n' "$1" >"$R/fake/fg" ;;
*) printf 'tty%s\n' "$1" >"$R/fake/fg" ;;
esac
exit 0
"#;

const LOGINCTL: &str = r#"#!/bin/sh
# 假 loginctl：座位里固定有一个 tty7 会话。
R="${AIRY_SESSION_ROOT:-/}"
printf 'loginctl %s\n' "$*" >>"$R/fake/calls.log"
printf '1 1000 airy seat0 tty7\n'
exit 0
"#;

const FGCONSOLE: &str = r#"#!/bin/sh
# 假 fgconsole：回放当前控制台。
R="${AIRY_SESSION_ROOT:-/}"
printf 'fgconsole\n' >>"$R/fake/calls.log"
cat "$R/fake/fg" 2>/dev/null || printf 'tty1\n'
exit 0
"#;

const PS: &str = r#"#!/bin/sh
# 假 ps：只实现 tpgid 探针，返回预置的进程组。
R="${AIRY_SESSION_ROOT:-/}"
printf 'ps %s\n' "$*" >>"$R/fake/calls.log"
cat "$R/fake/tpgid" 2>/dev/null || printf '%s\n' "${4:-0}"
exit 0
"#;
