// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 帧预算基准
//
// QG-08 的判据在 tests/frame_budget.rs，本二进制给出同一组读数供发行前
// 在目标机上复跑。stable Rust 没有 #[bench]，故用 harness = false 的独立
// 二进制：整屏重绘若干帧、样本排序取 p99，超预算即以退出码 1 报告。
//
// SSoT: 0.0.5 方案 §14.2、§9 QG-08。
// ===========================================================================

//! 85×24 整屏重绘的帧时与常驻堆基准。

use std::hint::black_box;
use std::process::ExitCode;
use std::time::{Duration, Instant};

use boot_tui::frame::{App, SLOW_MS};
use boot_tui::input::{
    KeyAction, KeyEvent, Mods, MonoTime, RawBody, RawEvent, SourceKind, TraceId, Trust, KEY_F5,
};

/// 基准终端尺寸；§14.2 的不变量就按这个尺寸定。
const COLS: u16 = 85;

/// 基准终端行数。
const ROWS: u16 = 24;

/// 采样帧数；第 99/100 个样本作 p99。
const RUNS: usize = 2000;

/// 常驻堆上限，64 KiB。
const HEAP: usize = 64 * 1024;

fn main() -> ExitCode {
    let mut app = App::new(COLS, ROWS);
    app.draw();
    let mut samples = Vec::with_capacity(RUNS);
    let mut bytes = 0;
    for _ in 0..RUNS {
        app.step(&[key(KEY_F5)]);
        let started = Instant::now();
        app.draw();
        samples.push(started.elapsed());
        bytes = app.bytes().len();
        black_box(bytes);
    }
    samples.sort_unstable();
    let p50 = samples[RUNS / 2];
    let p99 = samples[RUNS * 99 / 100];
    let heap = app.heap_bytes();
    println!(
        "frame: {COLS}x{ROWS} runs={RUNS} p50={:.3}ms p99={:.3}ms heap={heap}B bytes={bytes}",
        ms(p50),
        ms(p99)
    );
    if p99 > Duration::from_millis(SLOW_MS as u64) {
        eprintln!("frame: p99 超出预算 {SLOW_MS}ms");
        return ExitCode::FAILURE;
    }
    if heap > HEAP {
        eprintln!("frame: 常驻堆超出预算 {HEAP}B");
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

/// 毫秒读数，保留三位小数。
fn ms(span: Duration) -> f64 {
    span.as_secs_f64() * 1000.0
}

/// F5 按下事件：触发整屏重绘，是帧时最坏的一路。
fn key(code: u16) -> RawEvent {
    RawEvent {
        trace_id: TraceId([0; 16]),
        source: SourceKind::Key,
        trust: Trust::T0,
        at: MonoTime(0),
        body: RawBody::Key(KeyEvent {
            code,
            mods: Mods {
                shift: false,
                ctrl: false,
                alt: false,
            },
            action: KeyAction::Press,
        }),
    }
}
