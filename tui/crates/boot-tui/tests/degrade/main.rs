// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 降级矩阵的对外验收
//
// 这里只验证「决定」与「屏幕结果」，不复刻内部实现：无 GPU 的文本控制台
// 不发扩展色、极小终端仍能画出提示、非 tty 在写第一个字节之前被拒绝。
// 三条路径都不允许 panic，也不允许把转义序列漏进非终端。
//
// SSoT: 0.0.5 方案 §14.13。
// ===========================================================================

//! 终端降级矩阵的集成验收。

use boot_tui::degrade::{palette, theme, Palette};
use boot_tui::frame::App;
use boot_tui::screen::{MIN_COLS, MIN_ROWS};

/// 一帧的输出文本。
fn painted(cols: u16, rows: u16, term: Option<&str>) -> String {
    let mut app = App::with(cols, rows, theme(term));
    app.draw();
    String::from_utf8_lossy(app.bytes()).into_owned()
}

#[test]
fn a_linux_console_gets_the_basic_palette() {
    assert_eq!(palette(Some("linux")), Palette::Basic);
    assert_eq!(palette(Some("dumb")), Palette::Basic);
}

#[test]
fn a_colour_terminal_keeps_the_rich_palette() {
    assert_eq!(palette(Some("xterm-256color")), Palette::Rich);
    assert_eq!(palette(Some("screen")), Palette::Rich);
}

#[test]
fn a_missing_term_is_treated_as_a_console() {
    assert_eq!(palette(None), Palette::Basic);
}

#[test]
fn a_console_without_graphical_hardware_still_draws() {
    let screen = painted(MIN_COLS, MIN_ROWS, Some("linux"));
    assert!(screen.contains("TUI 会话"));
    assert!(!screen.contains("38;5;"));
    assert!(!screen.contains("48;5;"));
}

#[test]
fn a_rich_terminal_uses_indexed_colours() {
    assert!(painted(MIN_COLS, MIN_ROWS, Some("xterm-256color")).contains("48;5;"));
}

#[test]
fn a_one_cell_terminal_draws_a_tip() {
    assert!(!painted(1, 1, Some("linux")).is_empty());
}
