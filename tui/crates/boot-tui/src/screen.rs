// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 屏幕单元与尺寸口径
//
// 引导期只有 ANSI 终端，没有终端库可依赖：单元编码、样式与尺寸下限都在
// 这里定死。一个单元压进 8 字节，宽字符占两格（第二格记为宽度 0 的尾格），
// 列宽只按东亚宽度计算，歧义宽度固定为 1 列。
//
// 本片落单元编码、字符网格与尺寸下限；绘制与帧循环随后续片填入。
// SSoT: 0.0.5 方案 §14.2、§14.4。
// ===========================================================================

//! 屏幕单元、样式与尺寸下限。

use unicode_segmentation::UnicodeSegmentation;

use crate::wcwidth::display_width;

/// 可绘制的最小列数；小于此值只显示单行提示。
pub const MIN_COLS: u16 = 40;

/// 可绘制的最小行数。
pub const MIN_ROWS: u16 = 12;

/// 终端尺寸是否够画一帧。
pub fn fits(cols: u16, rows: u16) -> bool {
    cols >= MIN_COLS && rows >= MIN_ROWS
}

/// 单元颜色：终端默认色或 256 色索引。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum Color {
    /// 终端默认色。
    #[default]
    Default,
    /// 256 色索引。
    Idx(u8),
}

/// 字形属性位。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Attr(u8);

impl Attr {
    /// 无属性。
    pub const NONE: Self = Self(0);
    /// 加粗。
    pub const BOLD: Self = Self(1);
    /// 减淡。
    pub const DIM: Self = Self(2);
    /// 下划线。
    pub const UNDER: Self = Self(4);
    /// 反显。
    pub const REV: Self = Self(8);

    /// 叠加一个属性位。
    pub const fn with(self, other: Self) -> Self {
        Self(self.0 | other.0)
    }

    /// 是否含某个属性位。
    pub const fn has(self, other: Self) -> bool {
        self.0 & other.0 == other.0
    }

    /// 原始位，供编码使用。
    pub const fn bits(self) -> u8 {
        self.0
    }
}

/// 一个单元的颜色与属性。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Style {
    /// 前景色。
    pub fg: Color,
    /// 背景色。
    pub bg: Color,
    /// 字形属性。
    pub attr: Attr,
}

impl Style {
    /// 默认前景、默认背景、无属性。
    pub const DEFAULT: Self = Self {
        fg: Color::Default,
        bg: Color::Default,
        attr: Attr::NONE,
    };
}

/// 帧缓冲的一个单元；宽度只在 8 字节内紧凑编码。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Cell(u64);

/// `char` 的位宽。
const CHAR_BITS: u32 = 21;
/// 单个颜色的位宽。
const COLOR_BITS: u32 = 9;
/// 属性位宽。
const ATTR_BITS: u32 = 8;
/// 宽度位宽。
const WIDTH_BITS: u32 = 2;

/// 前景色起始位。
const FG_SHIFT: u32 = CHAR_BITS;
/// 背景色起始位。
const BG_SHIFT: u32 = FG_SHIFT + COLOR_BITS;
/// 属性起始位。
const ATTR_SHIFT: u32 = BG_SHIFT + COLOR_BITS;
/// 宽度起始位。
const WIDTH_SHIFT: u32 = ATTR_SHIFT + ATTR_BITS;

/// `char` 掩码。
const CHAR_MASK: u64 = (1 << CHAR_BITS) - 1;
/// 颜色掩码。
const COLOR_MASK: u64 = (1 << COLOR_BITS) - 1;
/// 宽度掩码。
const WIDTH_MASK: u64 = (1 << WIDTH_BITS) - 1;

impl Cell {
    /// 用一个字符、样式与显示宽度构造单元。
    pub const fn new(ch: char, style: Style, width: u8) -> Self {
        let bits = (ch as u64 & CHAR_MASK)
            | (color_bits(style.fg) << FG_SHIFT)
            | (color_bits(style.bg) << BG_SHIFT)
            | ((style.attr.bits() as u64) << ATTR_SHIFT)
            | ((width as u64 & WIDTH_MASK) << WIDTH_SHIFT);
        Self(bits)
    }

    /// 该样式的空格。
    pub const fn blank(style: Style) -> Self {
        Self::new(' ', style, 1)
    }

    /// 宽字符占用的第二格。
    pub const fn tail(style: Style) -> Self {
        Self::new(' ', style, 0)
    }

    /// 单元里的字符。
    pub fn ch(self) -> char {
        char::from_u32((self.0 & CHAR_MASK) as u32).unwrap_or(' ')
    }

    /// 单元的颜色与属性。
    pub fn style(self) -> Style {
        Style {
            fg: color_at((self.0 >> FG_SHIFT) & COLOR_MASK),
            bg: color_at((self.0 >> BG_SHIFT) & COLOR_MASK),
            attr: Attr(((self.0 >> ATTR_SHIFT) & 0xFF) as u8),
        }
    }

    /// 显示宽度：0 为宽字符尾格，1 为普通字符，2 为宽字符首格。
    pub const fn width(self) -> u8 {
        ((self.0 >> WIDTH_SHIFT) & WIDTH_MASK) as u8
    }

    /// 是否为宽字符尾格；绘制时跳过。
    pub const fn is_tail(self) -> bool {
        self.width() == 0
    }
}

/// 默认色在 9 位编码里的取值；256 个索引之外留出的第一个空位。
const COLOR_DEFAULT: u64 = 1 << (COLOR_BITS - 1);

/// 颜色的位编码：索引取原值，默认色取 [`COLOR_DEFAULT`]。
const fn color_bits(color: Color) -> u64 {
    match color {
        Color::Default => COLOR_DEFAULT,
        Color::Idx(index) => index as u64,
    }
}

/// 位编码还原颜色。
fn color_at(bits: u64) -> Color {
    if bits == COLOR_DEFAULT {
        Color::Default
    } else {
        Color::Idx(bits as u8)
    }
}

/// 一帧的字符网格：写入时标脏，绘制时只遍历脏行。
#[derive(Clone, Debug)]
pub struct CellGrid {
    cols: u16,
    rows: u16,
    cells: Vec<Cell>,
    dirty: Vec<bool>,
}

impl CellGrid {
    /// 建一张最小 1×1 的空网格；尺寸合法性由 [`fits`] 判断。
    pub fn new(cols: u16, rows: u16) -> Self {
        let cols = cols.max(1);
        let rows = rows.max(1);
        let blank = Cell::blank(Style::DEFAULT);
        Self {
            cols,
            rows,
            cells: vec![blank; usize::from(cols) * usize::from(rows)],
            dirty: vec![true; usize::from(rows)],
        }
    }

    /// 列数。
    pub fn cols(&self) -> u16 {
        self.cols
    }

    /// 行数。
    pub fn rows(&self) -> u16 {
        self.rows
    }

    /// 这张网格占用的常驻堆字节数，供帧预算门禁读数。
    pub fn heap(&self) -> usize {
        self.cells.capacity() * std::mem::size_of::<Cell>() + self.dirty.capacity()
    }

    /// 改尺寸；变化时整屏重画并返回 `true`。
    pub fn resize(&mut self, cols: u16, rows: u16) -> bool {
        if cols.max(1) == self.cols && rows.max(1) == self.rows {
            return false;
        }
        *self = Self::new(cols, rows);
        true
    }

    /// 整屏填成同一底色，用于每帧重排版面。
    pub fn clear(&mut self, style: Style) {
        for y in 0..self.rows {
            self.fill_row(y, style);
        }
    }

    /// 把一行填成空格；只有内容变化时标脏。
    pub fn fill_row(&mut self, y: u16, style: Style) {
        if y >= self.rows {
            return;
        }
        let blank = Cell::blank(style);
        let start = self.index(0, y);
        let mut changed = false;
        for cell in &mut self.cells[start..start + usize::from(self.cols)] {
            if *cell != blank {
                *cell = blank;
                changed = true;
            }
        }
        self.dirty[usize::from(y)] |= changed;
    }

    /// 从 `(x, y)` 起写一段文本，返回停下的列；宽字符按两列计。
    pub fn put(&mut self, x: u16, y: u16, text: &str, style: Style) -> u16 {
        if y >= self.rows {
            return x;
        }
        let mut col = x;
        for grapheme in text.graphemes(true) {
            let span = display_width(grapheme);
            if span == 0 {
                continue;
            }
            let span = span as u16;
            if col.saturating_add(span) > self.cols {
                break;
            }
            self.stamp(col, y, grapheme, style, span as u8);
            col += span;
        }
        col
    }

    /// 第 `y` 行的单元，供绘制与比对。
    pub fn row(&self, y: u16) -> &[Cell] {
        if y >= self.rows {
            return &[];
        }
        let start = self.index(0, y);
        &self.cells[start..start + usize::from(self.cols)]
    }

    /// 行是否要重画。
    pub fn dirty(&self, y: u16) -> bool {
        self.dirty.get(usize::from(y)).copied().unwrap_or(false)
    }

    /// 画完一行后清脏。
    pub fn clean(&mut self, y: u16) {
        if let Some(flag) = self.dirty.get_mut(usize::from(y)) {
            *flag = false;
        }
    }

    /// 全部标脏，用于整屏重画（首帧、尺寸变化、F5）。
    pub fn touch_all(&mut self) {
        self.dirty.fill(true);
    }

    /// 只标脏一行，用于局部状态变化。
    pub fn touch(&mut self, y: u16) {
        if let Some(flag) = self.dirty.get_mut(usize::from(y)) {
            *flag = true;
        }
    }

    /// 把已画出的行抄回本网格，供下一帧比对；尺寸不符时不抄。
    pub fn sync_row(&mut self, y: u16, src: &[Cell]) {
        if y >= self.rows || src.len() != usize::from(self.cols) {
            return;
        }
        let start = self.index(0, y);
        self.cells[start..start + src.len()].copy_from_slice(src);
    }

    /// 写入一个字形首格；宽字符同时写尾格。
    fn stamp(&mut self, col: u16, y: u16, text: &str, style: Style, span: u8) {
        let ch = text.chars().next().unwrap_or(' ');
        let index = self.index(col, y);
        self.assign(index, y, Cell::new(ch, style, span));
        if span == 2 {
            self.assign(index + 1, y, Cell::tail(style));
        }
    }

    /// 单元有变化才落盘并标脏。
    fn assign(&mut self, index: usize, y: u16, cell: Cell) {
        if self.cells[index] == cell {
            return;
        }
        self.cells[index] = cell;
        self.dirty[usize::from(y)] = true;
    }

    /// 二维坐标折成单元下标。
    fn index(&self, col: u16, y: u16) -> usize {
        usize::from(y) * usize::from(self.cols) + usize::from(col)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh() -> CellGrid {
        let mut grid = CellGrid::new(MIN_COLS, MIN_ROWS);
        for y in 0..grid.rows() {
            grid.clean(y);
        }
        grid
    }

    #[test]
    fn size_floor_is_enforced() {
        assert!(fits(MIN_COLS, MIN_ROWS));
        assert!(!fits(MIN_COLS - 1, MIN_ROWS));
        assert!(!fits(MIN_COLS, MIN_ROWS - 1));
    }

    #[test]
    fn a_cell_round_trips_char_style_and_width() {
        let style = Style {
            fg: Color::Idx(196),
            bg: Color::Idx(17),
            attr: Attr::BOLD.with(Attr::UNDER),
        };
        let cell = Cell::new('极', style, 2);
        assert_eq!(cell.ch(), '极');
        assert_eq!(cell.style(), style);
        assert_eq!(cell.width(), 2);
        assert!(!cell.is_tail());
    }

    #[test]
    fn default_colors_survive_the_round_trip() {
        let cell = Cell::new('a', Style::DEFAULT, 1);
        assert_eq!(cell.style(), Style::DEFAULT);
    }

    #[test]
    fn a_tail_cell_has_zero_width() {
        let cell = Cell::tail(Style::DEFAULT);
        assert!(cell.is_tail());
        assert_eq!(cell.width(), 0);
    }

    #[test]
    fn a_blank_cell_is_one_column_wide() {
        let cell = Cell::blank(Style::DEFAULT);
        assert_eq!(cell.ch(), ' ');
        assert_eq!(cell.width(), 1);
    }

    #[test]
    fn attributes_accumulate() {
        let attr = Attr::BOLD.with(Attr::REV);
        assert!(attr.has(Attr::BOLD));
        assert!(attr.has(Attr::REV));
        assert!(!attr.has(Attr::DIM));
        assert_eq!(attr.bits(), Attr::BOLD.bits() | Attr::REV.bits());
    }

    #[test]
    fn a_wide_char_owns_a_tail_cell() {
        let mut grid = fresh();
        assert_eq!(grid.put(0, 0, "极", Style::DEFAULT), 2);
        assert_eq!(grid.row(0)[0].width(), 2);
        assert!(grid.row(0)[1].is_tail());
    }

    #[test]
    fn a_wide_char_never_straddles_the_edge() {
        let mut grid = fresh();
        let last = MIN_COLS - 1;
        assert_eq!(grid.put(last, 0, "极", Style::DEFAULT), last);
        assert_eq!(grid.row(0)[usize::from(last)], Cell::blank(Style::DEFAULT));
    }

    #[test]
    fn writing_stops_at_the_right_edge() {
        let mut grid = fresh();
        assert_eq!(grid.put(38, 0, "abc", Style::DEFAULT), MIN_COLS);
        assert_eq!(grid.row(0)[39].ch(), 'b');
    }

    #[test]
    fn a_write_marks_only_its_row_dirty() {
        let mut grid = fresh();
        grid.put(1, 2, "ok", Style::DEFAULT);
        assert!(grid.dirty(2));
        assert!(!grid.dirty(1));
    }

    #[test]
    fn an_identical_write_keeps_the_row_clean() {
        let mut grid = fresh();
        grid.put(0, 0, "a", Style::DEFAULT);
        grid.clean(0);
        grid.put(0, 0, "a", Style::DEFAULT);
        assert!(!grid.dirty(0));
    }

    #[test]
    fn touching_one_row_leaves_the_rest_clean() {
        let mut grid = fresh();
        grid.touch(5);
        assert!(grid.dirty(5));
        assert!(!grid.dirty(4));
    }

    #[test]
    fn clearing_dirties_only_changed_rows() {
        let mut grid = fresh();
        grid.put(0, 0, "a", Style::DEFAULT);
        grid.clean(0);
        grid.clear(Style::DEFAULT);
        assert!(grid.dirty(0));
        assert!(!grid.dirty(1));
    }

    #[test]
    fn resize_rebuilds_and_dirties_every_row() {
        let mut grid = fresh();
        assert!(!grid.resize(MIN_COLS, MIN_ROWS));
        assert!(grid.resize(80, 24));
        assert_eq!(grid.cols(), 80);
        assert!(grid.dirty(23));
    }

    #[test]
    fn sync_row_copies_a_painted_row() {
        let mut src = fresh();
        src.put(0, 0, "x", Style::DEFAULT);
        let mut dst = CellGrid::new(MIN_COLS, MIN_ROWS);
        let row = src.row(0).to_vec();
        dst.sync_row(0, &row);
        assert_eq!(dst.row(0), src.row(0));
    }

    #[test]
    fn a_row_outside_the_grid_is_untouched() {
        let mut grid = fresh();
        assert_eq!(grid.put(3, MIN_ROWS, "x", Style::DEFAULT), 3);
        assert!(grid.row(MIN_ROWS).is_empty());
    }
}
