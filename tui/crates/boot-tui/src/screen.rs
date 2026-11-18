// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 屏幕单元与尺寸口径
//
// 引导期只有 ANSI 终端，没有终端库可依赖：单元编码、样式与尺寸下限都在
// 这里定死。一个单元压进 8 字节，宽字符占两格（第二格记为宽度 0 的尾格），
// 列宽只按东亚宽度计算，歧义宽度固定为 1 列。
//
// 本片只落单元编码与尺寸下限；网格与脏区在下一片填入，绘制与帧循环再往后。
// SSoT: 0.0.5 方案 §14.2、§14.4。
// ===========================================================================

//! 屏幕单元、样式与尺寸下限。

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

#[cfg(test)]
mod tests {
    use super::*;

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
}
