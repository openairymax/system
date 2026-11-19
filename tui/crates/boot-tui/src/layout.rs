// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 版面分区
//
// 一帧只由三个横向分区拼成：顶栏一行、状态栏一行、中间正文。分区几何
// 集中在这里算，绘制代码只拿矩形，不再自己加行号——否则「正文从第几行
// 开始」会散落在每个绘制点上，改一次留白就要改十几处，且改名之后很容易
// 出现正文压到状态栏的重叠。
//
// 正文留一列左右边距与顶栏下一行空白，让中文在这样的窄边距里也不贴边。
// 终端小于最小尺寸时正文为空，绘制段先判 [`crate::screen::fits`]，因此
// 空矩形只用于兜底，不表示「可以画」。
//
// SSoT: 0.0.5 方案 §14.2。
// ===========================================================================

//! 版面分区：顶栏、正文与状态栏。

/// 一个矩形区域，原点在网格左上角。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Rect {
    /// 左边界列号。
    pub x: u16,
    /// 上边界行号。
    pub y: u16,
    /// 宽度（列）。
    pub w: u16,
    /// 高度（行）。
    pub h: u16,
}

impl Rect {
    /// 按左上角与尺寸构造。
    pub const fn new(x: u16, y: u16, w: u16, h: u16) -> Self {
        Self { x, y, w, h }
    }

    /// 区域内没有任何单元。
    pub const fn is_empty(&self) -> bool {
        self.w == 0 || self.h == 0
    }
}

/// 一帧的三个分区。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Layout {
    /// 顶栏。
    pub head: Rect,
    /// 正文；已含左右边距与顶栏下的一行空白。
    pub body: Rect,
    /// 状态栏。
    pub foot: Rect,
}

impl Layout {
    /// 按终端尺寸分区；尺寸过小时相关分区为空。
    pub const fn of(cols: u16, rows: u16) -> Self {
        let head = Rect::new(0, 0, cols, head_h(rows));
        let foot = Rect::new(0, rows.saturating_sub(1), cols, foot_h(rows));
        let body = Rect::new(
            if cols == 0 { 0 } else { 1 },
            if rows < 2 { rows } else { 2 },
            cols.saturating_sub(2),
            rows.saturating_sub(3),
        );
        Self { head, body, foot }
    }
}

/// 顶栏高度：有行就占一行。
const fn head_h(rows: u16) -> u16 {
    if rows == 0 {
        0
    } else {
        1
    }
}

/// 状态栏高度：至少要给顶栏留一行。
const fn foot_h(rows: u16) -> u16 {
    if rows > 1 {
        1
    } else {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::Layout;

    #[test]
    fn an_eighty_five_by_twenty_four_grid_splits_into_three_bands() {
        let area = Layout::of(85, 24);
        assert_eq!(
            (area.head.x, area.head.y, area.head.w, area.head.h),
            (0, 0, 85, 1)
        );
        assert_eq!(
            (area.body.x, area.body.y, area.body.w, area.body.h),
            (1, 2, 83, 21)
        );
        assert_eq!(
            (area.foot.x, area.foot.y, area.foot.w, area.foot.h),
            (0, 23, 85, 1)
        );
    }

    #[test]
    fn the_smallest_supported_grid_keeps_a_body() {
        let area = Layout::of(40, 12);
        assert_eq!(area.body.h, 9);
        assert_eq!(area.foot.y, 11);
    }

    #[test]
    fn a_one_row_grid_has_no_state_bar() {
        let area = Layout::of(20, 1);
        assert!(area.foot.is_empty());
        assert!(area.body.is_empty());
        assert_eq!(area.head.h, 1);
    }

    #[test]
    fn bands_never_exceed_the_grid() {
        for rows in 0..30 {
            let area = Layout::of(30, rows);
            assert!(u32::from(area.foot.y) + u32::from(area.foot.h) <= u32::from(rows));
            assert!(u32::from(area.body.y) + u32::from(area.body.h) <= u32::from(rows));
        }
    }
}
