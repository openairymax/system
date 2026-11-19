// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 列表游标
//
// 命令面板、以后的设备表与进程表都要「在一个定长清单里上下移动」。游标
// 只记位置与长度，不持有清单本身：清单是静态数据，位置是会话状态，两者
// 分开才能让选择在重排后依然成立。
//
// 上下移动在两端夹住而不环绕：命令行不是游戏菜单，按到头还弹回第一项
// 会让人分不清「已经到底」还是「刚打开」。游标越界由 [`Nav::of`] 夹回，
// 清单变短时不会指向不存在的行。
//
// SSoT: 0.0.5 方案 §14.2「键盘导航」。
// ===========================================================================

//! 定长清单里的游标。

/// 一个清单游标：当前下标与清单长度。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Nav {
    at: usize,
    len: usize,
}

impl Nav {
    /// 长度为 `len` 的清单，游标停在第一项。
    pub const fn of(len: usize) -> Self {
        Self { at: 0, len }
    }

    /// 清单长度。
    pub const fn len(&self) -> usize {
        self.len
    }

    /// 清单是否为空。
    pub const fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// 当前下标；空清单恒为 0。
    pub const fn at(&self) -> usize {
        self.at
    }

    /// 回到第一项。
    pub fn reset(&mut self) {
        self.at = 0;
    }

    /// 上移一项；已在顶部则不动。
    pub fn up(&mut self) {
        self.at = self.at.saturating_sub(1);
    }

    /// 下移一项；已在底部则不动。
    pub fn down(&mut self) {
        if self.at + 1 < self.len {
            self.at += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Nav;

    #[test]
    fn a_new_cursor_starts_at_the_top() {
        let nav = Nav::of(4);
        assert_eq!(nav.at(), 0);
        assert_eq!(nav.len(), 4);
        assert!(!nav.is_empty());
    }

    #[test]
    fn up_and_down_stop_at_the_ends() {
        let mut nav = Nav::of(2);
        nav.up();
        assert_eq!(nav.at(), 0);
        nav.down();
        nav.down();
        assert_eq!(nav.at(), 1);
    }

    #[test]
    fn an_empty_list_never_moves() {
        let mut nav = Nav::of(0);
        nav.down();
        nav.up();
        assert_eq!(nav.at(), 0);
        assert!(nav.is_empty());
    }

    #[test]
    fn reset_returns_to_the_first_item() {
        let mut nav = Nav::of(3);
        nav.down();
        nav.down();
        nav.reset();
        assert_eq!(nav.at(), 0);
    }
}
