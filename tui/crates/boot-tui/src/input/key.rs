// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 键码空间
//
// 取 Linux input-event-codes 的 KEY_* 数值：终端解码、IME 与内核输入共用
// 一套键位编号，中间不再维护转译表。只列本层真正会产生的键，用到再加。
// ===========================================================================

//! 键码常量与单字节定键，数值与 Linux `KEY_*` 一致。

use super::event::{KeyAction, KeyEvent, Mods};

/// Esc。
pub const KEY_ESC: u16 = 1;
pub const KEY_TAB: u16 = 15;
/// Q；会话层的退出键。
pub const KEY_Q: u16 = 16;
pub const KEY_UP: u16 = 103;
pub const KEY_PAGEUP: u16 = 104;
pub const KEY_LEFT: u16 = 105;
pub const KEY_RIGHT: u16 = 106;
pub const KEY_END: u16 = 107;
pub const KEY_DOWN: u16 = 108;
pub const KEY_PAGEDOWN: u16 = 109;
pub const KEY_INSERT: u16 = 110;
pub const KEY_DELETE: u16 = 111;
pub const KEY_HOME: u16 = 102;
pub const KEY_F1: u16 = 59;
pub const KEY_F2: u16 = 60;
pub const KEY_F3: u16 = 61;
pub const KEY_F4: u16 = 62;
/// F5；整屏重画。
pub const KEY_F5: u16 = 63;
pub const KEY_F6: u16 = 64;
pub const KEY_F7: u16 = 65;
pub const KEY_F8: u16 = 66;
pub const KEY_F9: u16 = 67;
pub const KEY_F10: u16 = 68;
pub const KEY_F11: u16 = 87;
pub const KEY_F12: u16 = 88;
pub const KEY_SPACE: u16 = 57;
pub const KEY_ENTER: u16 = 28;
pub const KEY_BACKSPACE: u16 = 14;
pub const KEY_MINUS: u16 = 12;
pub const KEY_EQUAL: u16 = 13;
pub const KEY_LEFTBRACE: u16 = 26;
pub const KEY_RIGHTBRACE: u16 = 27;
pub const KEY_BACKSLASH: u16 = 43;
pub const KEY_SEMICOLON: u16 = 39;
/// C；与 Ctrl 组合时是终端惯例的中断键。
pub const KEY_C: u16 = 46;
pub const KEY_APOSTROPHE: u16 = 40;
pub const KEY_GRAVE: u16 = 41;
pub const KEY_COMMA: u16 = 51;
pub const KEY_DOT: u16 = 52;
pub const KEY_SLASH: u16 = 53;

/// 字母键码，按字母序排列；Linux 的 `KEY_A..KEY_Z` 是 QWERTY 顺序，不能靠加法推。
const LETTER: [u16; 26] = [
    30, 48, 46, 32, 18, 33, 34, 35, 23, 36, 37, 38, 50, 49, 24, 25, 16, 19, 31, 20, 22, 47, 17, 45,
    21, 44,
];

/// 数字键码，按 0–9 排列；Linux 的 `KEY_1..KEY_0` 同样不连续。
const DIGIT: [u16; 10] = [11, 2, 3, 4, 5, 6, 7, 8, 9, 10];

/// 可打印标点：字节、键码、是否需要 Shift。
const PUNCT: &[(u8, u16, bool)] = &[
    (b'!', 2, true),
    (b'"', KEY_APOSTROPHE, true),
    (b'#', 4, true),
    (b'$', 5, true),
    (b'%', 6, true),
    (b'&', 8, true),
    (b'\'', KEY_APOSTROPHE, false),
    (b'(', 10, true),
    (b')', 11, true),
    (b'*', 9, true),
    (b'+', KEY_EQUAL, true),
    (b',', KEY_COMMA, false),
    (b'-', KEY_MINUS, false),
    (b'.', KEY_DOT, false),
    (b'/', KEY_SLASH, false),
    (b':', KEY_SEMICOLON, true),
    (b';', KEY_SEMICOLON, false),
    (b'<', KEY_COMMA, true),
    (b'=', KEY_EQUAL, false),
    (b'>', KEY_DOT, true),
    (b'?', KEY_SLASH, true),
    (b'@', 3, true),
    (b'[', KEY_LEFTBRACE, false),
    (b'\\', KEY_BACKSLASH, false),
    (b']', KEY_RIGHTBRACE, false),
    (b'^', 7, true),
    (b'_', KEY_MINUS, true),
    (b'`', KEY_GRAVE, false),
    (b'{', KEY_LEFTBRACE, true),
    (b'|', KEY_BACKSLASH, true),
    (b'}', KEY_RIGHTBRACE, true),
    (b'~', KEY_GRAVE, true),
];

const NONE: Mods = Mods {
    shift: false,
    ctrl: false,
    alt: false,
};
const CTRL: Mods = Mods {
    shift: false,
    ctrl: true,
    alt: false,
};

/// 单字节定键：控制键按终端惯例，可打印字符按 US 布局。
///
/// `/dev/tty` 的 raw 模式只给字节；ESC 序列先交给 [`super::escape`]，
/// 这里处理其余字节。非 ASCII 字节不定键——中文只能经 IME 提交或显式
/// ASCII 直通成为文本（SSoT §10.2）。US 布局是当前版本的键位口径，非 US
/// 布局要等键位资产引入后再重映射。
pub fn key_byte(byte: u8) -> Option<KeyEvent> {
    match byte {
        0x00 => Some(key_of(KEY_SPACE, CTRL)),
        0x08 | 0x7f => Some(key_of(KEY_BACKSPACE, NONE)),
        0x09 => Some(key_of(KEY_TAB, NONE)),
        0x0a | 0x0d => Some(key_of(KEY_ENTER, NONE)),
        0x1b => Some(key_of(KEY_ESC, NONE)),
        0x01..=0x1a => Some(key_of(LETTER[usize::from(byte) - 1], CTRL)),
        _ => printed(byte),
    }
}

fn printed(byte: u8) -> Option<KeyEvent> {
    let (code, shift) = match byte {
        b'a'..=b'z' => (LETTER[usize::from(byte - b'a')], false),
        b'A'..=b'Z' => (LETTER[usize::from(byte - b'A')], true),
        b'0'..=b'9' => (DIGIT[usize::from(byte - b'0')], false),
        b' ' => (KEY_SPACE, false),
        _ => {
            let (_, code, shift) = *PUNCT.iter().find(|row| row.0 == byte)?;
            (code, shift)
        }
    };
    Some(key_of(code, shift_mods(shift)))
}

fn key_of(code: u16, mods: Mods) -> KeyEvent {
    KeyEvent {
        code,
        mods,
        action: KeyAction::Press,
    }
}

fn shift_mods(shift: bool) -> Mods {
    Mods {
        shift,
        ctrl: false,
        alt: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(byte: u8) -> KeyEvent {
        key_byte(byte).unwrap_or_else(|| panic!("{byte:#04x} 应当定键"))
    }

    #[test]
    fn letters_follow_qwerty_codes() {
        assert_eq!(ev(b'a').code, 30);
        assert_eq!(ev(b'q').code, 16);
        assert_eq!(ev(b'z').code, 44);
        assert_eq!(ev(b'm').code, 50);
        assert_eq!(ev(b'Z').code, 44);
        assert!(ev(b'Z').mods.shift);
        assert!(!ev(b'z').mods.shift);
    }

    #[test]
    fn digits_and_punctuation() {
        assert_eq!(ev(b'0').code, 11);
        assert_eq!(ev(b'1').code, 2);
        assert_eq!(ev(b'9').code, 10);
        assert_eq!(ev(b' ').code, KEY_SPACE);
        assert_eq!(ev(b'-').code, KEY_MINUS);
        assert_eq!(ev(b'_').code, KEY_MINUS);
        assert!(ev(b'_').mods.shift);
        assert_eq!(ev(b'?').code, KEY_SLASH);
        assert_eq!(ev(b'/').code, KEY_SLASH);
        assert_eq!(ev(b'@').code, 3);
        assert_eq!(ev(b'!').code, 2);
        assert_eq!(ev(b'~').code, KEY_GRAVE);
        assert_eq!(ev(b'`').code, KEY_GRAVE);
    }

    #[test]
    fn control_bytes_follow_terminal_usage() {
        assert_eq!(ev(0x01).code, 30);
        assert!(ev(0x01).mods.ctrl);
        assert_eq!(ev(0x1a).code, 44);
        assert_eq!(ev(0x09).code, KEY_TAB);
        assert_eq!(ev(0x0a).code, KEY_ENTER);
        assert_eq!(ev(0x0d).code, KEY_ENTER);
        assert_eq!(ev(0x08).code, KEY_BACKSPACE);
        assert_eq!(ev(0x7f).code, KEY_BACKSPACE);
        assert_eq!(ev(0x00).code, KEY_SPACE);
        assert!(ev(0x00).mods.ctrl);
        assert_eq!(ev(0x1b).code, KEY_ESC);
    }

    #[test]
    fn non_ascii_is_not_a_key() {
        for byte in [0x80, 0xc3, 0xe4, 0xff] {
            assert!(key_byte(byte).is_none(), "{byte:#04x}");
        }
    }
}
