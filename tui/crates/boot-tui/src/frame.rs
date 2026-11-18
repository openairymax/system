// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 引导期会话的帧循环
//
// 一帧只做四件事：等输入窗口、取一批事件、重排版面、把脏行交给绘制器。
// 版面每帧整体重排而绘制只走脏行——状态变化自然反映到屏上，静止时一帧
// 写不出一个字节。单帧最多吃 64 个事件，余下的留到下一帧，粘贴风暴不会
// 把帧时打爆；帧时超标只降级为标记，不丢事件也不静默跳帧。
//
// SSoT: 0.0.5 方案 §14.2、§14.5。
// ===========================================================================

//! 版面、事件处理与单帧绘制。

use crate::input::{KeyAction, KeyEvent, RawBody, RawEvent, KEY_C, KEY_ESC, KEY_F5, KEY_Q};
use crate::layout::Layout;
use crate::paint::Painter;
use crate::screen::{fits, Attr, CellGrid, Color, Style};
use crate::wcwidth::clip_cols;

/// 单帧最多处理的输入事件，余下的留到下一帧。
pub const MAX_BATCH: usize = 64;

/// 单帧的等待窗口，毫秒。
pub const FRAME_MS: i32 = 16;

/// 超过此值的帧时在状态栏标记 `render=slow`。
pub const SLOW_MS: u128 = 33;

/// 顶栏文字。
const TITLE: &str = "AirymaxOS 极境 · TUI 会话";

/// 就绪提示。
const READY: &str = "会话层已就绪。";

/// 输入通道现状。
const INPUT: &str = "输入通道：键盘。";

/// 按键提示。
const KEYS: &str = "按键：q / Esc / Ctrl-C 退出，F5 重绘。";

/// 常态状态栏。
const FOOT: &str = "q 退出 · F5 重绘";

/// 慢帧状态栏；降级必须可见，不得静默丢帧。
const FOOT_SLOW: &str = "render=slow · q 退出";

/// 终端小于最小尺寸时的单行提示。
const SMALL: &str = "终端过小（最小 40×12），请调整窗口后按 F5";

/// 顶栏底色；256 色里的深蓝，保证浅色与深色主题下都压得住。
const BAR: Style = Style {
    fg: Color::Default,
    bg: Color::Idx(17),
    attr: Attr::BOLD,
};

/// 状态栏样式。
const FOOT_STYLE: Style = Style {
    fg: Color::Default,
    bg: Color::Default,
    attr: Attr::REV,
};

/// 引导期会话的状态与版面。
pub struct App {
    grid: CellGrid,
    painter: Painter,
    out: Vec<u8>,
    quit: bool,
    slow: bool,
}

impl App {
    /// 按终端尺寸建版面。
    pub fn new(cols: u16, rows: u16) -> Self {
        Self {
            grid: CellGrid::new(cols, rows),
            painter: Painter::new(cols, rows),
            out: Vec::new(),
            quit: false,
            slow: false,
        }
    }

    /// 尺寸变化后重排并整屏重画。
    pub fn resize(&mut self, cols: u16, rows: u16) {
        self.grid.resize(cols, rows);
    }

    /// 处理一批输入事件。
    pub fn step(&mut self, events: &[RawEvent]) {
        for event in events {
            let RawBody::Key(key) = &event.body else {
                continue;
            };
            self.on_key(key);
        }
    }

    /// 重排版面并生成这一帧的字节。
    pub fn draw(&mut self) {
        self.compose();
        self.out.clear();
        let Self {
            grid, painter, out, ..
        } = self;
        painter.paint(grid, out);
    }

    /// 这一帧要写出的字节；空表示屏幕无需变化。
    pub fn bytes(&self) -> &[u8] {
        &self.out
    }

    /// 用户是否已要求退出。
    pub fn done(&self) -> bool {
        self.quit
    }

    /// 记录本帧是否超出预算；标记变化时只重画状态栏。
    pub fn mark(&mut self, slow: bool) {
        if slow == self.slow {
            return;
        }
        self.slow = slow;
        self.grid.touch(self.grid.rows().saturating_sub(1));
    }

    /// 退出键：q、Esc 与 Ctrl-C；F5 只重画。
    fn on_key(&mut self, key: &KeyEvent) {
        if key.action == KeyAction::Release {
            return;
        }
        if key.code == KEY_F5 {
            self.grid.touch_all();
            self.painter.repaint();
            return;
        }
        if quit_key(key) {
            self.quit = true;
        }
    }

    /// 全屏重排：顶栏、正文与状态栏；尺寸不足时只留一行提示。
    fn compose(&mut self) {
        let cols = self.grid.cols();
        let rows = self.grid.rows();
        self.grid.clear(Style::DEFAULT);
        if !fits(cols, rows) {
            self.grid.put(0, 0, clip_cols(SMALL, cols), Style::DEFAULT);
            return;
        }
        let area = Layout::of(cols, rows);
        self.grid.fill_row(area.head.y, BAR);
        self.grid.put(
            area.head.x + 1,
            area.head.y,
            clip_cols(TITLE, area.head.w.saturating_sub(1)),
            BAR,
        );
        for (at, line) in [READY, "", INPUT, "", KEYS].iter().enumerate() {
            let at = at as u16;
            if at >= area.body.h {
                break;
            }
            self.grid.put(
                area.body.x,
                area.body.y + at,
                clip_cols(line, area.body.w),
                Style::DEFAULT,
            );
        }
        if area.foot.is_empty() {
            return;
        }
        self.grid.fill_row(area.foot.y, FOOT_STYLE);
        self.grid.put(
            area.foot.x + 1,
            area.foot.y,
            clip_cols(self.footer(), area.foot.w.saturating_sub(2)),
            FOOT_STYLE,
        );
    }

    /// 状态栏文字随慢帧标记切换。
    fn footer(&self) -> &'static str {
        if self.slow {
            FOOT_SLOW
        } else {
            FOOT
        }
    }
}

/// 是否是退出键。
fn quit_key(key: &KeyEvent) -> bool {
    match key.code {
        KEY_ESC => true,
        KEY_Q => !key.mods.ctrl && !key.mods.alt,
        KEY_C => key.mods.ctrl,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::input::{Mods, MonoTime, SourceKind, TraceId, Trust};
    use crate::screen::{MIN_COLS, MIN_ROWS};

    fn event(key: KeyEvent) -> RawEvent {
        RawEvent {
            trace_id: TraceId([0; 16]),
            source: SourceKind::Key,
            trust: Trust::T0,
            at: MonoTime(0),
            body: RawBody::Key(key),
        }
    }

    fn key(code: u16, ctrl: bool) -> RawEvent {
        let mods = Mods {
            shift: false,
            ctrl,
            alt: false,
        };
        event(KeyEvent {
            code,
            mods,
            action: KeyAction::Press,
        })
    }

    fn app() -> App {
        App::new(MIN_COLS, MIN_ROWS)
    }

    fn text(app: &App) -> String {
        String::from_utf8_lossy(app.bytes()).into_owned()
    }

    #[test]
    fn the_first_frame_draws_the_title() {
        let mut app = app();
        app.draw();
        assert!(text(&app).contains(TITLE));
    }

    #[test]
    fn a_settled_screen_writes_nothing() {
        let mut app = app();
        app.draw();
        app.draw();
        assert!(app.bytes().is_empty());
    }

    #[test]
    fn a_release_does_not_quit() {
        let mut app = app();
        app.step(&[event(KeyEvent {
            code: KEY_Q,
            mods: Mods::default(),
            action: KeyAction::Release,
        })]);
        assert!(!app.done());
    }

    #[test]
    fn q_and_escape_end_the_session() {
        for key in [key(KEY_Q, false), key(KEY_ESC, false), key(KEY_C, true)] {
            let mut app = app();
            app.step(&[key]);
            assert!(app.done());
        }
    }

    #[test]
    fn a_plain_c_does_not_quit() {
        let mut app = app();
        app.step(&[key(KEY_C, false)]);
        assert!(!app.done());
    }

    #[test]
    fn f5_repaints_every_row() {
        let mut app = app();
        app.draw();
        app.step(&[key(KEY_F5, false)]);
        app.draw();
        assert!(!app.bytes().is_empty());
    }

    #[test]
    fn a_slow_frame_is_visible_in_the_status_bar() {
        let mut app = app();
        app.draw();
        app.mark(true);
        app.draw();
        assert!(text(&app).contains(FOOT_SLOW));
    }

    #[test]
    fn a_small_terminal_shows_one_line() {
        let mut app = App::new(MIN_COLS - 4, MIN_ROWS - 2);
        app.draw();
        assert!(text(&app).contains("终端过小"));
    }

    #[test]
    fn a_resize_redraws_at_the_new_width() {
        let mut app = app();
        app.draw();
        app.resize(80, 24);
        app.draw();
        assert!(text(&app).contains(TITLE));
        assert!(text(&app).contains(FOOT));
    }
}
