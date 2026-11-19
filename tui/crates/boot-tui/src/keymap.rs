// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 键到动作的唯一映射
//
// 帧循环只认动作，不认键码：退出到底绑 q、Esc 还是 Ctrl-C，只在 lookup
// 一处回答。面板里选中的命令也归到同一组动作上执行，因此「按 q」与
// 「在面板里选退出」不可能走出两种行为。
//
// 松键一律不产生动作：终端会把同一次按下报成按下与松开两条，若都处理，
// 中文输入法的确认键会让会话退两次。
//
// SSoT: 0.0.5 方案 §14.2、§14.5。
// ===========================================================================

//! 键序列到会话动作的映射。

use crate::input::{
    KeyAction, KeyEvent, KEY_C, KEY_DOWN, KEY_END, KEY_ENTER, KEY_ESC, KEY_F1, KEY_F5, KEY_HOME,
    KEY_PAGEDOWN, KEY_PAGEUP, KEY_Q, KEY_UP,
};

/// 会话层能执行的动作。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Action {
    /// 退出会话。
    Quit,
    /// 整屏重画。
    Redraw,
    /// 上翻一页。
    PageUp,
    /// 下翻一页。
    PageDown,
    /// 回到最早。
    Top,
    /// 回到最新。
    Bottom,
    /// 打开或关闭命令面板。
    Menu,
    /// 上移选中项。
    Up,
    /// 下移选中项。
    Down,
    /// 执行选中项。
    Enter,
    /// 取消当前层：面板打开时关面板，否则退出。
    Cancel,
}

/// 把一次按键翻译成动作；未绑定或松键返回 `None`。
pub fn lookup(key: &KeyEvent) -> Option<Action> {
    if key.action == KeyAction::Release {
        return None;
    }
    quit_chord(key)
        .or_else(|| paging(key.code))
        .or_else(|| menu_key(key.code))
}

/// 退出组合：q 单按或 Ctrl-C。
fn quit_chord(key: &KeyEvent) -> Option<Action> {
    match (key.code, key.mods.ctrl, key.mods.alt) {
        (KEY_Q, false, false) => Some(Action::Quit),
        (KEY_C, true, _) => Some(Action::Quit),
        _ => None,
    }
}

/// 翻页与列表移动。
fn paging(code: u16) -> Option<Action> {
    let action = match code {
        KEY_PAGEUP => Action::PageUp,
        KEY_PAGEDOWN => Action::PageDown,
        KEY_HOME => Action::Top,
        KEY_END => Action::Bottom,
        KEY_UP => Action::Up,
        KEY_DOWN => Action::Down,
        _ => return None,
    };
    Some(action)
}

/// 面板与取消。
fn menu_key(code: u16) -> Option<Action> {
    let action = match code {
        KEY_ESC => Action::Cancel,
        KEY_F1 => Action::Menu,
        KEY_F5 => Action::Redraw,
        KEY_ENTER => Action::Enter,
        _ => return None,
    };
    Some(action)
}

#[cfg(test)]
mod tests {
    use super::{lookup, Action};
    use crate::input::{
        KeyAction, KeyEvent, Mods, KEY_C, KEY_DOWN, KEY_END, KEY_ENTER, KEY_ESC, KEY_F1, KEY_F5,
        KEY_HOME, KEY_PAGEDOWN, KEY_PAGEUP, KEY_Q, KEY_UP,
    };

    fn press(code: u16, mods: Mods) -> Option<Action> {
        lookup(&KeyEvent {
            code,
            mods,
            action: KeyAction::Press,
        })
    }

    #[test]
    fn the_documented_keys_map_to_actions() {
        assert_eq!(press(KEY_Q, Mods::default()), Some(Action::Quit));
        assert_eq!(press(KEY_ESC, Mods::default()), Some(Action::Cancel));
        assert_eq!(
            press(
                KEY_C,
                Mods {
                    ctrl: true,
                    ..Mods::default()
                }
            ),
            Some(Action::Quit)
        );
        assert_eq!(press(KEY_F1, Mods::default()), Some(Action::Menu));
        assert_eq!(press(KEY_F5, Mods::default()), Some(Action::Redraw));
        assert_eq!(press(KEY_PAGEUP, Mods::default()), Some(Action::PageUp));
        assert_eq!(press(KEY_PAGEDOWN, Mods::default()), Some(Action::PageDown));
        assert_eq!(press(KEY_HOME, Mods::default()), Some(Action::Top));
        assert_eq!(press(KEY_END, Mods::default()), Some(Action::Bottom));
        assert_eq!(press(KEY_UP, Mods::default()), Some(Action::Up));
        assert_eq!(press(KEY_DOWN, Mods::default()), Some(Action::Down));
        assert_eq!(press(KEY_ENTER, Mods::default()), Some(Action::Enter));
    }

    #[test]
    fn a_plain_c_is_not_a_command() {
        assert_eq!(press(KEY_C, Mods::default()), None);
    }

    #[test]
    fn a_release_never_fires() {
        assert_eq!(
            lookup(&KeyEvent {
                code: KEY_Q,
                mods: Mods::default(),
                action: KeyAction::Release,
            }),
            None
        );
    }

    #[test]
    fn an_unbound_key_is_ignored() {
        assert_eq!(press(0xffff, Mods::default()), None);
    }
}
