// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 会话回看缓冲
//
// 会话视图要能回看已经滚过去的内容，但不能把整段历史留在帧循环里反复
// 重排。这里把「逻辑行」与「按列宽换行后的显示行」分开存：逻辑行按到达
// 顺序追加，显示行只在宽度变化时整段重算，平时只追加新行的换行结果。
// 视口用「距底部的显示行数」表示，贴着底部时新行自动跟随；正在回看时
// 新行把视口锚在原地，不会把用户正在读的那一行挤走。
//
// 容量有硬上限：超过之后丢最旧一行，并同步丢掉它的显示行。这样长时间
// 开机也不会把内存吃满，且丢弃顺序与阅读顺序一致。
//
// 换行只走 `wcwidth` 的列宽口径，不按字符数或字节数切；宽字符整簇换行，
// 不允许把「家庭 emoji」或「天 + 组合符」切成两半。
//
// SSoT: 0.0.5 方案 §14.2 帧预算、§14.4 东亚宽度。
// ===========================================================================

//! 会话回看：逻辑行、按宽度换行与视口。

use unicode_segmentation::UnicodeSegmentation;

use crate::wcwidth::display_width;

/// 保留的逻辑行上限；超出后从最旧一行丢弃。
pub const CAP: usize = 2000;

/// 一段会话的历史行与视口。
pub struct Scroll {
    lines: Vec<String>,
    rows: Vec<String>,
    spans: Vec<usize>,
    width: u16,
    height: u16,
    back: usize,
}

impl Scroll {
    /// 空会话。
    pub fn new() -> Self {
        Self {
            lines: Vec::new(),
            rows: Vec::new(),
            spans: Vec::new(),
            width: 0,
            height: 0,
            back: 0,
        }
    }

    /// 追加一行；贴着底部时视口跟随，正在回看时视口原地不动。
    pub fn push(&mut self, line: impl Into<String>) {
        self.lines.push(line.into());
        let added = self.append_rows();
        if !self.is_tail() {
            self.back += added;
        }
        if self.lines.len() > CAP {
            self.evict();
        }
        self.clamp();
    }

    /// 距离底部的显示行数；0 表示贴着底部。
    pub fn back(&self) -> usize {
        self.back
    }

    /// 视口是否贴着底部。
    pub fn is_tail(&self) -> bool {
        self.back == 0
    }

    /// 向上翻一页。
    pub fn page_up(&mut self) {
        let page = usize::from(self.height.max(1));
        self.back = self.top().min(self.back + page);
    }

    /// 向下翻一页。
    pub fn page_dn(&mut self) {
        let page = usize::from(self.height.max(1));
        self.back = self.back.saturating_sub(page);
    }

    /// 跳到最早一页。
    pub fn home(&mut self) {
        self.back = self.top();
    }

    /// 回到最新一页。
    pub fn tail(&mut self) {
        self.back = 0;
    }

    /// 当前视口的显示行；宽度或高度变化时先重排。
    pub fn view(&mut self, width: u16, height: u16) -> &[String] {
        self.height = height;
        self.rewrap(width);
        self.clamp();
        if width == 0 || height == 0 {
            return &[];
        }
        let end = self.rows.len().saturating_sub(self.back);
        let start = end.saturating_sub(usize::from(height));
        &self.rows[start..end]
    }

    /// 视口最多能离底部多远：最早一页的顶部对齐正文首行。
    fn top(&self) -> usize {
        self.rows.len().saturating_sub(usize::from(self.height))
    }

    /// 视口越界时拉回可读范围。
    fn clamp(&mut self) {
        self.back = self.back.min(self.top());
    }

    /// 把最新一行换成显示行，返回新增的显示行数。
    fn append_rows(&mut self) -> usize {
        if self.width == 0 {
            self.spans.push(0);
            return 0;
        }
        let wrapped = match self.lines.last() {
            Some(line) => wrap(line, self.width),
            None => return 0,
        };
        let count = wrapped.len();
        self.rows.extend(wrapped);
        self.spans.push(count);
        count
    }

    /// 丢掉最旧一行及其显示行。
    fn evict(&mut self) {
        self.lines.remove(0);
        let drop = self.spans.remove(0);
        self.rows.drain(..drop);
    }

    /// 宽度变化时整段重算显示行；宽度不变时保留增量结果。
    fn rewrap(&mut self, width: u16) {
        if self.width == width {
            return;
        }
        self.width = width;
        self.rows.clear();
        self.spans.clear();
        if width == 0 {
            self.spans.resize(self.lines.len(), 0);
            self.back = 0;
            return;
        }
        for line in &self.lines {
            let wrapped = wrap(line, width);
            self.spans.push(wrapped.len());
            self.rows.extend(wrapped);
        }
    }
}

impl Default for Scroll {
    fn default() -> Self {
        Self::new()
    }
}

/// 按显示列宽换行；切口落在字素簇边界上。
fn wrap(line: &str, width: u16) -> Vec<String> {
    let limit = usize::from(width);
    let mut rows = Vec::new();
    let mut start = 0;
    let mut used = 0;
    for (at, grapheme) in line.grapheme_indices(true) {
        let span = display_width(grapheme);
        if used > 0 && used + span > limit {
            rows.push(line[start..at].to_owned());
            start = at;
            used = 0;
        }
        used += span;
    }
    rows.push(line[start..].to_owned());
    rows
}

#[cfg(test)]
mod tests {
    use super::{wrap, Scroll, CAP};

    fn scroll(lines: &[&str]) -> Scroll {
        let mut scroll = Scroll::new();
        for line in lines {
            scroll.push(*line);
        }
        scroll
    }

    fn shown(scroll: &mut Scroll, width: u16, height: u16) -> Vec<String> {
        scroll.view(width, height).to_vec()
    }

    #[test]
    fn the_newest_rows_are_at_the_bottom() {
        let mut scroll = scroll(&["a", "b", "c"]);
        assert_eq!(shown(&mut scroll, 10, 2), ["b", "c"]);
        assert!(scroll.is_tail());
    }

    #[test]
    fn a_blank_line_still_takes_a_row() {
        let mut scroll = scroll(&["a", ""]);
        assert_eq!(shown(&mut scroll, 10, 5), ["a", ""]);
    }

    #[test]
    fn a_long_line_wraps_on_display_columns() {
        assert_eq!(wrap("极境操作系统", 4), ["极境", "操作", "系统"]);
        assert_eq!(wrap("abcdef", 2), ["ab", "cd", "ef"]);
    }

    #[test]
    fn a_wide_grapheme_is_never_split() {
        let rows = wrap("a极", 2);
        assert_eq!(rows, ["a", "极"]);
    }

    #[test]
    fn paging_up_moves_the_window_back() {
        let mut scroll = scroll(&["0", "1", "2", "3", "4", "5"]);
        assert_eq!(shown(&mut scroll, 10, 2), ["4", "5"]);
        scroll.page_up();
        assert_eq!(shown(&mut scroll, 10, 2), ["2", "3"]);
        assert_eq!(scroll.back(), 2);
        assert!(!scroll.is_tail());
    }

    #[test]
    fn paging_up_stops_at_the_first_page() {
        let mut scroll = scroll(&["0", "1", "2", "3", "4", "5"]);
        assert_eq!(shown(&mut scroll, 10, 2), ["4", "5"]);
        for _ in 0..9 {
            scroll.page_up();
        }
        assert_eq!(shown(&mut scroll, 10, 2), ["0", "1"]);
        assert_eq!(scroll.back(), 4);
    }

    #[test]
    fn tail_returns_to_the_newest_rows() {
        let mut scroll = scroll(&["0", "1", "2", "3", "4", "5"]);
        scroll.home();
        scroll.tail();
        assert_eq!(shown(&mut scroll, 10, 2), ["4", "5"]);
        assert!(scroll.is_tail());
    }

    #[test]
    fn new_rows_do_not_move_a_scrolled_view() {
        let mut scroll = scroll(&["0", "1", "2", "3", "4", "5"]);
        assert_eq!(shown(&mut scroll, 10, 2), ["4", "5"]);
        scroll.page_up();
        assert_eq!(shown(&mut scroll, 10, 2), ["2", "3"]);
        scroll.push("6");
        assert_eq!(shown(&mut scroll, 10, 2), ["2", "3"]);
    }

    #[test]
    fn a_width_change_rewraps_the_history() {
        let mut scroll = scroll(&["极境操作系统"]);
        assert_eq!(shown(&mut scroll, 20, 6), ["极境操作系统"]);
        assert_eq!(shown(&mut scroll, 4, 6), ["极境", "操作", "系统"]);
    }

    #[test]
    fn rows_survive_an_incremental_push_after_a_resize() {
        let mut scroll = scroll(&["极境"]);
        assert_eq!(shown(&mut scroll, 2, 3), ["极", "境"]);
        scroll.push("系统");
        assert_eq!(shown(&mut scroll, 2, 3), ["境", "系", "统"]);
    }

    #[test]
    fn the_oldest_line_is_dropped_beyond_the_cap() {
        let mut scroll = Scroll::new();
        for index in 0..=CAP {
            scroll.push(index.to_string());
        }
        assert_eq!(shown(&mut scroll, 10, 4), ["1997", "1998", "1999", "2000"]);
        let all = shown(&mut scroll, 10, 2001);
        assert_eq!(all.len(), CAP);
        assert_eq!(all.first().map(String::as_str), Some("1"));
    }

    #[test]
    fn a_zero_sized_view_returns_nothing() {
        let mut scroll = scroll(&["a"]);
        assert!(scroll.view(0, 4).is_empty());
        assert!(scroll.view(10, 0).is_empty());
    }
}
