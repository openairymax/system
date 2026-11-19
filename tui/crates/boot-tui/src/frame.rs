// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
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

use std::time::Instant;

use crate::input::{RawBody, RawEvent};
use crate::keymap::{self, Action};
use crate::layout::{Layout, Rect};
use crate::paint::Painter;
use crate::palette::{Command, Palette};
use crate::screen::{fits, CellGrid, Style};
use crate::scrollback::Scroll;
use crate::session::{Role, Session, Stamp};
use crate::session_store::Record;
use crate::status::Face;
use crate::theme::Theme;
use crate::wcwidth::{clip_cols, display_width};

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
const KEYS: &str = "按键：q / Esc / Ctrl-C 退出，PgUp/PgDn 回看，F5 重绘，F1 命令。";

/// 常态状态栏。
const FOOT: &str = "q 退出 · PgUp/PgDn 回看 · F1 命令";

/// 回看中的状态栏；位置必须可见，否则用户不知道自己在哪一页。
const FOOT_BACK: &str = "PgDn/End 回底 · 回看 -";

/// 慢帧状态栏；降级必须可见，不得静默丢帧。
const FOOT_SLOW: &str = "render=slow · q 退出";

/// 终端小于最小尺寸时的单行提示。
const SMALL: &str = "终端过小（最小 40×12），请调整窗口后按 F5";

/// 引导期会话的状态与版面。
pub struct App {
    grid: CellGrid,
    painter: Painter,
    theme: Theme,
    out: Vec<u8>,
    scroll: Scroll,
    session: Session,
    palette: Palette,
    started: Instant,
    quit: bool,
    slow: bool,
    asr: Option<String>,
}

impl App {
    /// 按终端尺寸建版面，使用内置主题。
    pub fn new(cols: u16, rows: u16) -> Self {
        Self::with(cols, rows, Theme::builtin())
    }

    /// 按终端尺寸与主题建版面。
    pub fn with(cols: u16, rows: u16, theme: Theme) -> Self {
        let mut app = Self {
            grid: CellGrid::new(cols, rows),
            painter: Painter::new(cols, rows),
            theme,
            out: Vec::new(),
            scroll: Scroll::new(),
            session: Session::new(),
            palette: Palette::new(),
            started: Instant::now(),
            quit: false,
            slow: false,
            asr: None,
        };
        app.record(Role::System, READY);
        app.scroll.push(String::new());
        app.record(Role::System, INPUT);
        app.record(Role::System, KEYS);
        app
    }

    /// 收下状态面并重排。
    pub fn set_face(&mut self, face: Face) {
        self.scroll.push(String::new());
        self.record(Role::System, "状态面");
        self.record(Role::System, face.probe.line());
        self.record(Role::System, face.rt.line());
        self.scroll.push(String::new());
        self.record(Role::System, "追踪");
        for line in face.trace.lines() {
            self.record(Role::System, line);
        }
        self.grid.touch_all();
    }

    /// 上次会话未正常结束时，把结论写进会话流。
    pub fn show_recovery(&mut self, prior: &Record) {
        self.record(
            Role::System,
            format!(
                "上次会话未正常结束：{}（{}）",
                prior.state.word(),
                prior.note
            ),
        );
        self.grid.touch_all();
    }

    /// 追加一条会话消息并送到屏上。
    fn record(&mut self, role: Role, text: impl Into<String>) {
        let at = Stamp::of(self.started.elapsed());
        for line in self.session.push(at, role, text) {
            self.scroll.push(line);
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

    /// 帧缓冲的常驻堆字节数：输出缓冲、前后两张网格与脏标记。
    ///
    /// 每帧的临时字符串不进这个数——它们在同一帧内分配并释放，容量
    /// 不随帧数增长；QG-08 判的是这份常驻堆不超标、帧间不增长。
    pub fn heap_bytes(&self) -> usize {
        self.out.capacity() + self.grid.heap() + self.painter.heap()
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

    /// 收下语音通道的降级状态；就绪传 `None`。
    ///
    /// 取值是网关给出的三个词之一（`unavailable` / `busy` / `empty`）。
    /// 降级必须出现在状态栏：静默掉它，用户只会觉得「按了没反应」，
    /// 而真正的原因（没有模型、设备被别的实例占着）再也查不到。
    pub fn set_asr(&mut self, status: Option<String>) {
        if status == self.asr {
            return;
        }
        self.asr = status;
        self.grid.touch(self.grid.rows().saturating_sub(1));
    }

    /// 面板打开时按键先归面板；否则直接执行动作。
    fn on_key(&mut self, key: &crate::input::KeyEvent) {
        let Some(action) = keymap::lookup(key) else {
            return;
        };
        if self.palette.is_open() {
            self.on_menu(action);
            return;
        }
        self.perform(action);
    }

    /// 面板内的按键：上下选、回车执行、Esc 或 F1 关闭。
    fn on_menu(&mut self, action: Action) {
        match action {
            Action::Cancel | Action::Menu | Action::Quit => self.palette.close(),
            Action::Up => self.palette.up(),
            Action::Down => self.palette.down(),
            Action::Enter => {
                let chosen = self.palette.chosen();
                self.palette.close();
                if let Some(command) = chosen {
                    self.perform(command.action());
                }
            }
            _ => return,
        }
        self.grid.touch_all();
    }

    /// 执行一个动作；直接按键与面板选中走同一条路径。
    fn perform(&mut self, action: Action) {
        match action {
            Action::Quit | Action::Cancel => self.quit = true,
            Action::Redraw => {
                self.grid.touch_all();
                self.painter.repaint();
            }
            Action::PageUp => self.scroll.page_up(),
            Action::PageDown => self.scroll.page_dn(),
            Action::Top => self.scroll.home(),
            Action::Bottom => self.scroll.tail(),
            Action::Menu => self.palette.toggle(),
            Action::Up | Action::Down | Action::Enter => return,
        }
        self.grid.touch_all();
    }

    /// 全屏重排：顶栏、会话流与状态栏；尺寸不足时只留一行提示。
    fn compose(&mut self) {
        let cols = self.grid.cols();
        let rows = self.grid.rows();
        self.grid.clear(Style::DEFAULT);
        if !fits(cols, rows) {
            self.grid.put(0, 0, clip_cols(SMALL, cols), Style::DEFAULT);
            return;
        }
        let area = Layout::of(cols, rows);
        self.grid.fill_row(area.head.y, self.theme.bar);
        self.grid.put(
            area.head.x + 1,
            area.head.y,
            clip_cols(TITLE, area.head.w.saturating_sub(1)),
            self.theme.bar,
        );
        let lines = self.scroll.view(area.body.w, area.body.h);
        for (at, line) in lines.iter().enumerate() {
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
        if self.palette.is_open() {
            self.paint_menu(area.body);
        }
        if area.foot.is_empty() {
            return;
        }
        self.grid.fill_row(area.foot.y, self.theme.foot);
        let foot = self.footer();
        self.grid.put(
            area.foot.x + 1,
            area.foot.y,
            clip_cols(&foot, area.foot.w.saturating_sub(2)),
            self.theme.foot,
        );
    }

    /// 状态栏文字随慢帧与回看位置切换，语音降级时追加 `asr=<状态>`。
    fn footer(&self) -> String {
        let base = if self.slow {
            FOOT_SLOW.to_owned()
        } else if self.scroll.is_tail() {
            FOOT.to_owned()
        } else {
            format!("{FOOT_BACK}{}", self.scroll.back())
        };
        match &self.asr {
            Some(status) => format!("{base} · asr={status}"),
            None => base,
        }
    }

    /// 在正文区域居中画出命令面板；放不下时宁可不画，也不压掉正文。
    fn paint_menu(&mut self, body: Rect) {
        let h = Command::ALL.len() as u16 + 2;
        let w = body.w.min(34);
        if w < 16 || body.h < h {
            return;
        }
        let x = body.x + (body.w - w) / 2;
        let y = body.y + (body.h - h) / 2;
        let inner = w - 2;
        self.grid.put(x, y, &top_rule(inner), Style::DEFAULT);
        for (at, command) in Command::ALL.iter().enumerate() {
            let style = if at == self.palette.at() {
                self.theme.foot
            } else {
                Style::DEFAULT
            };
            let row = menu_row(*command, inner);
            self.grid
                .put(x, y + 1 + at as u16, &format!("│{row}│"), style);
        }
        self.grid
            .put(x, y + h - 1, &bottom_rule(inner), Style::DEFAULT);
    }
}

/// 面板上边框，带标题。
fn top_rule(inner: u16) -> String {
    let title = "─ 命令 ";
    let rest = usize::from(inner).saturating_sub(display_width(title));
    format!("┌{title}{}┐", "─".repeat(rest))
}

/// 面板下边框。
fn bottom_rule(inner: u16) -> String {
    format!("└{}┘", "─".repeat(usize::from(inner)))
}

/// 一行命令：左侧名称、右侧直接键；宽度不足时先截断再补空格。
fn menu_row(command: Command, inner: u16) -> String {
    let left = format!(" {}", command.label());
    let right = format!("{} ", command.hint());
    let used = display_width(&left) + display_width(&right);
    if used <= usize::from(inner) {
        return format!("{left}{}{right}", " ".repeat(usize::from(inner) - used));
    }
    let text = format!(" {} {}", command.label(), command.hint());
    let clipped = clip_cols(&text, inner).to_owned();
    let pad = usize::from(inner).saturating_sub(display_width(&clipped));
    format!("{clipped}{}", " ".repeat(pad))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::input::{
        KeyAction, KeyEvent, Mods, MonoTime, SourceKind, TraceId, Trust, KEY_C, KEY_DOWN, KEY_END,
        KEY_ENTER, KEY_ESC, KEY_F1, KEY_F5, KEY_PAGEUP, KEY_Q,
    };
    use crate::screen::{MIN_COLS, MIN_ROWS};
    use crate::session_store::{Record, State};
    use crate::status::Item;
    use crate::trace_view::View;

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
    fn a_degraded_voice_channel_is_visible_and_clearable() {
        let mut app = App::new(80, 24);
        app.draw();
        app.set_asr(Some("busy".to_owned()));
        app.draw();
        assert!(text(&app).contains("asr=busy"));
        app.set_asr(Some("busy".to_owned()));
        app.draw();
        assert!(app.bytes().is_empty(), "同一状态不该重画");
        app.set_asr(None);
        app.draw();
        assert!(!text(&app).contains("asr="));
    }

    #[test]
    fn a_small_terminal_shows_one_line() {
        let mut app = App::new(MIN_COLS - 4, MIN_ROWS - 2);
        app.draw();
        assert!(text(&app).contains("终端过小"));
    }

    #[test]
    fn paging_up_and_end_move_the_view() {
        let mut app = app();
        let face = || Face {
            probe: Item {
                name: "探针",
                text: "ok".to_owned(),
            },
            rt: Item {
                name: "实时",
                text: "ok".to_owned(),
            },
            trace: View::failed("未安装"),
        };
        for _ in 0..5 {
            app.set_face(face());
        }
        app.draw();
        app.step(&[key(KEY_PAGEUP, false)]);
        app.draw();
        assert!(text(&app).contains("回看 -"));
        app.step(&[key(KEY_END, false)]);
        app.draw();
        assert!(text(&app).contains(FOOT));
        assert!(!text(&app).contains("回看 -"));
    }

    #[test]
    fn f1_opens_the_command_menu() {
        let mut app = app();
        app.draw();
        app.step(&[key(KEY_F1, false)]);
        app.draw();
        let screen = text(&app);
        assert!(screen.contains("命令"));
        assert!(screen.contains("退出会话"));
    }

    #[test]
    fn escape_closes_the_menu_without_quitting() {
        let mut app = app();
        app.step(&[key(KEY_F1, false)]);
        app.step(&[key(KEY_ESC, false)]);
        assert!(!app.done());
        app.draw();
        assert!(!text(&app).contains("退出会话"));
    }

    #[test]
    fn enter_runs_the_selected_command() {
        let mut app = app();
        app.step(&[key(KEY_F1, false)]);
        for _ in 0..3 {
            app.step(&[key(KEY_DOWN, false)]);
        }
        app.step(&[key(KEY_ENTER, false)]);
        assert!(app.done());
    }

    #[test]
    fn a_previous_bad_session_is_named_on_screen() {
        let mut app = app();
        app.show_recovery(&Record {
            state: State::Fallback,
            note: "panic: gone".to_owned(),
        });
        app.draw();
        let screen = text(&app);
        assert!(screen.contains("上次会话未正常结束"));
        assert!(screen.contains("panic: gone"));
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
