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

use boot_tui::display::{notify_ready, Display};
use boot_tui::frame::{App, FRAME_MS, MAX_BATCH, SLOW_MS};
use boot_tui::input::{InputSource, Keys, Refusal};
use boot_tui::status::{self, Face};

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("boot-tui: {} ({})", err.status(), err.detail());
            ExitCode::FAILURE
        }
    }
}

/// 一次会话从首帧走到用户退出。
fn run() -> Result<(), Refusal> {
    let mut keys = Keys::open()?;
    let mut display = Display::open()?;
    let mut app = start(&mut display)?;
    let face = watch();
    while !app.done() {
        frame(&mut app, &mut keys, &mut display, &face)?;
    }
    Ok(())
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
    let mut app = App::new(cols, rows);
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
