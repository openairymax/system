// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// QG-08 帧预算
//
// §14.2 的两条不变量在这里变成可判据的读数：85×24 下整屏重绘 p99 ≤ 33 ms、
// 帧常驻堆 ≤ 64 KiB。stable Rust 没有分配器钩子（`GlobalAlloc` 要写 unsafe，
// 工作区禁写），因此「单帧分配」以常驻堆表达：输出缓冲、前后两张网格与
// 脏标记的容量之和。每帧的临时字符串在同一帧内释放，不改变这份读数；
// 判据是它不超标，且一万帧后一个字节都不长。
//
// SSoT: 0.0.5 方案 §14.2、§9 QG-08。
// ===========================================================================

//! 帧预算的集成判据：帧时 p99 与常驻堆。

use std::hint::black_box;
use std::time::{Duration, Instant};

use boot_tui::frame::{App, MAX_BATCH, SLOW_MS};
use boot_tui::input::{
    KeyAction, KeyEvent, Mods, MonoTime, RawBody, RawEvent, SourceKind, TraceId, Trust, KEY_F5,
};

/// 不变量规定的终端尺寸。
const COLS: u16 = 85;

/// 不变量规定的终端行数。
const ROWS: u16 = 24;

/// 每项判据的采样帧数。
const RUNS: usize = 600;

/// 常驻堆上限，64 KiB。
const HEAP: usize = 64 * 1024;

/// 单帧帧时上限。
const BUDGET: Duration = Duration::from_millis(SLOW_MS as u64);

#[test]
fn full_repaints_stay_under_budget() {
    let mut app = App::new(COLS, ROWS);
    app.draw();
    let mut samples = Vec::with_capacity(RUNS);
    for _ in 0..RUNS {
        app.step(&[key()]);
        let started = Instant::now();
        app.draw();
        samples.push(started.elapsed());
        black_box(app.bytes().len());
    }
    let p99 = p99_of(samples);
    assert!(p99 <= BUDGET, "p99 {p99:?} 超出 {BUDGET:?}");
}

#[test]
fn a_full_event_batch_stays_under_budget() {
    let mut app = App::new(COLS, ROWS);
    let batch = vec![key(); MAX_BATCH];
    app.draw();
    let mut samples = Vec::with_capacity(RUNS);
    for _ in 0..RUNS {
        let started = Instant::now();
        app.step(&batch);
        app.draw();
        samples.push(started.elapsed());
        black_box(app.bytes().len());
    }
    let p99 = p99_of(samples);
    assert!(
        p99 <= BUDGET,
        "整批 {MAX_BATCH} 事件 p99 {p99:?} 超出 {BUDGET:?}"
    );
}

#[test]
fn a_settled_frame_writes_nothing() {
    let mut app = App::new(COLS, ROWS);
    app.draw();
    app.draw();
    for _ in 0..100 {
        app.draw();
        assert!(app.bytes().is_empty(), "静止帧不应写出字节");
    }
}

#[test]
fn the_steady_heap_stays_under_budget() {
    let mut app = App::new(COLS, ROWS);
    app.draw();
    let settled = app.heap_bytes();
    assert!(settled <= HEAP, "常驻堆 {settled}B 超出 {HEAP}B");
    for _ in 0..10_000 {
        app.step(&[key()]);
        app.draw();
    }
    assert_eq!(app.heap_bytes(), settled, "帧间常驻堆增长");
}

/// 第 99/100 个样本；样本会就地排序。
fn p99_of(mut samples: Vec<Duration>) -> Duration {
    samples.sort_unstable();
    samples[samples.len() * 99 / 100]
}

/// F5 按下事件：触发整屏重绘，是帧时最坏的一路。
fn key() -> RawEvent {
    RawEvent {
        trace_id: TraceId([0; 16]),
        source: SourceKind::Key,
        trust: Trust::T0,
        at: MonoTime(0),
        body: RawBody::Key(KeyEvent {
            code: KEY_F5,
            mods: Mods {
                shift: false,
                ctrl: false,
                alt: false,
            },
            action: KeyAction::Press,
        }),
    }
}
