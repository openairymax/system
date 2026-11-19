// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 命令面板
//
// 引导期不能靠说明书：用户可能是在救援现场第一次见到这块屏。面板把会话
// 层能做的事列成一张可上下选择的清单，每个动作同时标出直接键，用户从
// 面板里学会快捷键之后就不必再打开它。
//
// 面板本身不执行动作，只回答「当前选中哪条命令」；执行仍由帧循环按
// [`Command::action`] 走与直接按键完全相同的路径，避免两套行为分叉。
//
// 每次打开都回到第一项：上次停在哪是临时状态，保留它会让同一次按键在
// 不同会话里落到不同命令上。
//
// SSoT: 0.0.5 方案 §14.2「键盘导航」。
// ===========================================================================

//! 命令面板的命令清单与游标。

use crate::keymap::Action;
use crate::nav::Nav;

/// 面板里的一条命令；声明顺序即显示顺序。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Command {
    /// 整屏重画。
    Redraw,
    /// 回到最早一页。
    Top,
    /// 回到最新一页。
    Tail,
    /// 退出会话。
    Quit,
}

impl Command {
    /// 全部命令。
    pub const ALL: [Self; 4] = [Self::Redraw, Self::Top, Self::Tail, Self::Quit];

    /// 面向用户的中文名称。
    pub fn label(self) -> &'static str {
        match self {
            Self::Redraw => "重绘整屏",
            Self::Top => "回到最早",
            Self::Tail => "回到最新",
            Self::Quit => "退出会话",
        }
    }

    /// 直接触发同一动作的键，显示在名称右侧。
    pub fn hint(self) -> &'static str {
        match self {
            Self::Redraw => "F5",
            Self::Top => "Home",
            Self::Tail => "End",
            Self::Quit => "q",
        }
    }

    /// 命令对应的动作；与直接按键共用同一条执行路径。
    pub fn action(self) -> Action {
        match self {
            Self::Redraw => Action::Redraw,
            Self::Top => Action::Top,
            Self::Tail => Action::Bottom,
            Self::Quit => Action::Quit,
        }
    }
}

/// 面板状态：是否打开与当前选中项。
#[derive(Clone, Debug, Default)]
pub struct Palette {
    nav: Nav,
    open: bool,
}

impl Palette {
    /// 关闭状态的面板，游标停在第一项。
    pub fn new() -> Self {
        Self::default()
    }

    /// 面板是否打开。
    pub fn is_open(&self) -> bool {
        self.open
    }

    /// 打开或关闭；每次打开都回到第一项。
    pub fn toggle(&mut self) {
        if self.open {
            self.close();
            return;
        }
        self.nav = Nav::of(Command::ALL.len());
        self.open = true;
    }

    /// 关闭面板；游标留待下次打开时重置。
    pub fn close(&mut self) {
        self.open = false;
    }

    /// 上移一项。
    pub fn up(&mut self) {
        self.nav.up();
    }

    /// 下移一项。
    pub fn down(&mut self) {
        self.nav.down();
    }

    /// 当前选中的命令下标。
    pub fn at(&self) -> usize {
        self.nav.at()
    }

    /// 当前选中的命令；清单为空时为 `None`。
    pub fn chosen(&self) -> Option<Command> {
        Command::ALL.get(self.nav.at()).copied()
    }
}

#[cfg(test)]
mod tests {
    use super::{Command, Palette};

    #[test]
    fn a_new_palette_is_closed() {
        assert!(!Palette::new().is_open());
    }

    #[test]
    fn every_command_has_a_label_and_a_hint() {
        for command in Command::ALL {
            assert!(!command.label().is_empty());
            assert!(!command.hint().is_empty());
        }
    }

    #[test]
    fn toggling_opens_and_closes() {
        let mut palette = Palette::new();
        palette.toggle();
        assert!(palette.is_open());
        palette.toggle();
        assert!(!palette.is_open());
    }

    #[test]
    fn the_cursor_follows_the_keys() {
        let mut palette = Palette::new();
        palette.toggle();
        assert_eq!(palette.chosen(), Some(Command::Redraw));
        palette.down();
        assert_eq!(palette.chosen(), Some(Command::Top));
        palette.up();
        assert_eq!(palette.chosen(), Some(Command::Redraw));
    }

    #[test]
    fn reopening_starts_at_the_first_command() {
        let mut palette = Palette::new();
        palette.toggle();
        palette.down();
        palette.close();
        palette.toggle();
        assert_eq!(palette.at(), 0);
    }
}
