// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 脏行绘制
//
// 网格与终端之间只走一条路：Painter 留着上一帧已经呈现的网格，逐脏行
// 生成 ANSI 转义写进调用方的字节缓冲，调用方只做一次 write_all。SGR 只在
// 样式变化时发，宽字符的尾格直接跳过——写两遍会把右邻的列顶掉。
//
// 上一帧结束时一定回到默认样式，因此每行都从默认样式起算。
// SSoT: 0.0.5 方案 §14.2「只重绘脏行、单次写出」。
// ===========================================================================

//! 把字符网格的脏行画成 ANSI 字节。

use crate::screen::{Attr, CellGrid, Color, Style};

/// 一帧的绘制器；`front` 是终端上已经呈现的那一帧。
pub struct Painter {
    front: CellGrid,
    full: bool,
}

impl Painter {
    /// 以空屏作为上一帧；首帧整屏写出。
    pub fn new(cols: u16, rows: u16) -> Self {
        Self {
            front: CellGrid::new(cols, rows),
            full: true,
        }
    }

    /// 下一帧整屏重画，用于自救或用户显式重绘。
    pub fn repaint(&mut self) {
        self.full = true;
    }

    /// 上一帧网格占用的常驻堆字节数，供帧预算门禁读数。
    pub fn heap(&self) -> usize {
        self.front.heap()
    }

    /// 画出脏行，返回写出的行数；尺寸变化时整屏重画。
    ///
    /// 脏标记只是提示：与上一帧逐格相同就不必写出。整屏重画时不做这层
    /// 比较——屏幕上的旧内容未必与本进程记下的上一帧一致（改窗口大小
    /// 后终端可能重排过），只有重写才能保证清干净。
    pub fn paint(&mut self, back: &mut CellGrid, out: &mut Vec<u8>) -> usize {
        self.sync_size(back);
        let mut painted = 0;
        for y in 0..back.rows() {
            if !back.dirty(y) {
                continue;
            }
            if !self.full && back.row(y) == self.front.row(y) {
                back.clean(y);
                continue;
            }
            self.write_row(back, y, out);
            self.front.sync_row(y, back.row(y));
            back.clean(y);
            painted += 1;
        }
        self.full = false;
        painted
    }

    /// 网格尺寸变了就换一张上一帧并整屏重画。
    fn sync_size(&mut self, back: &mut CellGrid) {
        if self.front.cols() == back.cols() && self.front.rows() == back.rows() {
            return;
        }
        self.front = CellGrid::new(back.cols(), back.rows());
        self.full = true;
        back.touch_all();
    }

    /// 写一行：先定位，再按样式变化切 SGR。
    fn write_row(&self, back: &CellGrid, y: u16, out: &mut Vec<u8>) {
        move_to(out, y);
        let mut current = Style::DEFAULT;
        for cell in back.row(y) {
            if cell.is_tail() {
                continue;
            }
            if cell.style() != current {
                push_sgr(out, cell.style());
                current = cell.style();
            }
            push_char(out, cell.ch());
        }
        if current != Style::DEFAULT {
            out.extend_from_slice(b"\x1b[0m");
        }
    }
}

/// 光标移到第 `y` 行第一列。
fn move_to(out: &mut Vec<u8>, y: u16) {
    out.extend_from_slice(b"\x1b[");
    push_uint(out, u32::from(y) + 1);
    out.extend_from_slice(b";1H");
}

/// 样式切换：先复位，再叠加属性与颜色。
fn push_sgr(out: &mut Vec<u8>, style: Style) {
    out.extend_from_slice(b"\x1b[0");
    push_attr(out, style.attr);
    push_color(out, b'3', style.fg);
    push_color(out, b'4', style.bg);
    out.push(b'm');
}

/// 属性位按固定顺序展开，便于基线比对。
fn push_attr(out: &mut Vec<u8>, attr: Attr) {
    let flags = [
        (Attr::BOLD, 1u8),
        (Attr::DIM, 2),
        (Attr::UNDER, 4),
        (Attr::REV, 7),
    ];
    for (flag, code) in flags {
        if attr.has(flag) {
            out.push(b';');
            push_uint(out, u32::from(code));
        }
    }
}

/// 256 色写成 `;38;5;n` 或 `;48;5;n`；默认色交给复位。
fn push_color(out: &mut Vec<u8>, base: u8, color: Color) {
    let Color::Idx(index) = color else {
        return;
    };
    out.push(b';');
    out.push(base);
    out.extend_from_slice(b"8;5;");
    push_uint(out, u32::from(index));
}

/// 十进制写入，避免为一次格式化引入额外依赖。
fn push_uint(out: &mut Vec<u8>, mut value: u32) {
    let mut digits = [0u8; 10];
    let mut at = digits.len();
    while value > 0 {
        at -= 1;
        digits[at] = b'0' + (value % 10) as u8;
        value /= 10;
    }
    if at == digits.len() {
        out.push(b'0');
    } else {
        out.extend_from_slice(&digits[at..]);
    }
}

/// 一个字符的 UTF-8 字节。
fn push_char(out: &mut Vec<u8>, ch: char) {
    let mut buf = [0u8; 4];
    out.extend_from_slice(ch.encode_utf8(&mut buf).as_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::screen::MIN_COLS;

    const ROWS: u16 = crate::screen::MIN_ROWS;

    fn pair() -> (CellGrid, Painter) {
        (CellGrid::new(MIN_COLS, ROWS), Painter::new(MIN_COLS, ROWS))
    }

    #[test]
    fn the_first_paint_writes_every_row() {
        let (mut grid, mut painter) = pair();
        let mut out = Vec::new();
        assert_eq!(painter.paint(&mut grid, &mut out), usize::from(ROWS));
        assert!(!out.is_empty());
    }

    #[test]
    fn a_second_paint_writes_nothing() {
        let (mut grid, mut painter) = pair();
        let mut out = Vec::new();
        painter.paint(&mut grid, &mut out);
        out.clear();
        assert_eq!(painter.paint(&mut grid, &mut out), 0);
        assert!(out.is_empty());
    }

    #[test]
    fn only_the_changed_row_is_written() {
        let (mut grid, mut painter) = pair();
        let mut out = Vec::new();
        painter.paint(&mut grid, &mut out);
        out.clear();
        grid.put(0, 3, "x", Style::DEFAULT);
        assert_eq!(painter.paint(&mut grid, &mut out), 1);
        assert!(out.windows(4).any(|win| win == b"4;1H"));
    }

    #[test]
    fn a_row_that_returns_to_its_old_content_is_not_written() {
        let (mut grid, mut painter) = pair();
        let mut out = Vec::new();
        painter.paint(&mut grid, &mut out);
        out.clear();
        grid.fill_row(
            0,
            Style {
                bg: Color::Idx(17),
                ..Style::DEFAULT
            },
        );
        grid.fill_row(0, Style::DEFAULT);
        assert_eq!(painter.paint(&mut grid, &mut out), 0);
        assert!(out.is_empty());
    }

    #[test]
    fn a_wide_char_is_written_once() {
        let (mut grid, mut painter) = pair();
        grid.put(0, 0, "极", Style::DEFAULT);
        let mut out = Vec::new();
        painter.paint(&mut grid, &mut out);
        let text = String::from_utf8_lossy(&out);
        assert_eq!(text.matches('极').count(), 1);
    }

    #[test]
    fn an_attribute_wraps_the_text() {
        let (mut grid, mut painter) = pair();
        let style = Style {
            attr: Attr::BOLD,
            ..Style::DEFAULT
        };
        grid.put(0, 0, "B", style);
        let mut out = Vec::new();
        painter.paint(&mut grid, &mut out);
        let text = String::from_utf8_lossy(&out);
        assert!(text.contains("\u{1b}[0;1mB\u{1b}[0m"));
    }

    #[test]
    fn an_indexed_background_uses_256_colour_sgr() {
        let (mut grid, mut painter) = pair();
        let style = Style {
            bg: Color::Idx(17),
            ..Style::DEFAULT
        };
        grid.put(0, 0, "b", style);
        let mut out = Vec::new();
        painter.paint(&mut grid, &mut out);
        let text = String::from_utf8_lossy(&out);
        assert!(text.contains("\u{1b}[0;48;5;17m"));
    }

    #[test]
    fn a_resize_repaints_every_row() {
        let (mut grid, mut painter) = pair();
        let mut out = Vec::new();
        painter.paint(&mut grid, &mut out);
        out.clear();
        grid.resize(80, 24);
        assert_eq!(painter.paint(&mut grid, &mut out), 24);
    }
}
