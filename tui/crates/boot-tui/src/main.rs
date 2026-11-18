// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 引导期 TUI 会话的二进制入口
//
// 打开键盘与显示面后进入单线程帧循环：等一个输入窗口、取一批事件、重排
// 版面、写出脏行。首帧上屏后按 NOTIFY_SOCKET 上报 READY=1，让绑定 tty1
// 的 unit 从 starting 转入 running；退出码 0 只表示用户主动退出。
//
// 失败只写一行 `status (detail)`：引导期没有日志系统可依赖，写到 stderr
// 就能进 journal。所有恢复动作都在 Drop 里，异常路径同样生效。
//
// SSoT: 0.0.5 方案 §14.1、§14.2。
// ===========================================================================

//! 引导期 TUI 会话入口。

use std::process::ExitCode;
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::Instant;

use boot_tui::degrade;
use boot_tui::display::{notify_ready, Display};
use boot_tui::frame::{App, FRAME_MS, MAX_BATCH, SLOW_MS};
use boot_tui::input::{InputSource, Keys, Refusal};
use boot_tui::recover::{self, Exit};
use boot_tui::session_store::{Record, State, Store};
use boot_tui::status::{self, Face};

fn main() -> ExitCode {
    match recover::guard(run) {
        Ok(()) => ExitCode::SUCCESS,
        Err(exit) => report(exit),
    }
}

/// 失败只写一行；panic 先补记结局再报原因。
fn report(exit: Exit) -> ExitCode {
    match exit {
        Exit::Refused(err) => eprintln!("boot-tui: {} ({})", err.status(), err.detail()),
        Exit::Panicked(why) => {
            save(&Store::system(), State::Fallback, &format!("panic: {why}"));
            eprintln!("boot-tui: fallback (panic: {why})");
        }
    }
    ExitCode::FAILURE
}

/// 一次会话从首帧走到用户退出。
fn run() -> Result<(), Refusal> {
    let store = Store::system();
    let prior = store.load();
    let stop = recover::Stop::arm()?;
    let mut keys = Keys::open()?;
    let mut display = Display::open()?;
    let mut app = start(&mut display)?;
    if let Some(gone) = prior.filter(|record| record.state != State::Done) {
        app.show_recovery(&gone);
    }
    save(&store, State::Ready, "first frame");
    let face = watch();
    while !app.done() {
        if let Some(sig) = stop.taken() {
            save(
                &store,
                State::Fallback,
                &format!("signal {}", recover::name(sig)),
            );
            return Ok(());
        }
        frame(&mut app, &mut keys, &mut display, &face)?;
    }
    save(&store, State::Done, "quit");
    Ok(())
}

/// 写一条会话结局；写不进去也不能影响退出。
fn save(store: &Store, state: State, note: &str) {
    let record = Record {
        state,
        note: note.to_owned(),
    };
    if let Err(err) = store.save(&record) {
        eprintln!("boot-tui: session store ({err})");
    }
}

/// 在后台读状态面；结果经通道回传，读不到时写明原因。
fn watch() -> Receiver<Face> {
    let (tx, rx) = mpsc::channel();
    let fallback = tx.clone();
    let spawned = thread::Builder::new()
        .name("face".to_owned())
        .spawn(move || {
            let _ = tx.send(status::read());
        });
    if spawned.is_err() {
        let _ = fallback.send(status::failed("状态面线程未能启动"));
    }
    rx
}

/// 首帧：按终端尺寸建版面、上屏并上报就绪。
fn start(display: &mut Display) -> Result<App, Refusal> {
    let (cols, rows) = display.size()?;
    let term = std::env::var("TERM").ok();
    let mut app = App::with(cols, rows, degrade::theme(term.as_deref()));
    app.draw();
    display.present(app.bytes())?;
    notify_ready();
    Ok(app)
}

/// 一帧：等输入、收状态、重排版面、写出脏行，最后记下帧时。
fn frame(
    app: &mut App,
    keys: &mut Keys,
    display: &mut Display,
    face: &Receiver<Face>,
) -> Result<(), Refusal> {
    let started = Instant::now();
    keys.wait(FRAME_MS)?;
    let events = keys.drain(MAX_BATCH)?;
    app.step(&events);
    if let Ok(next) = face.try_recv() {
        app.set_face(next);
    }
    let (cols, rows) = display.size()?;
    app.resize(cols, rows);
    app.draw();
    if !app.bytes().is_empty() {
        display.present(app.bytes())?;
    }
    app.mark(started.elapsed().as_millis() > SLOW_MS);
    Ok(())
}
