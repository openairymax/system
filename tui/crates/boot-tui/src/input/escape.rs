// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 终端转义序列解码
//
// 只解以 ESC 开头、语义无歧义的序列（xterm 与 ECMA-48 的交集）：方向键、
// Home/End、翻页、插入删除、F1–F12 与反 Tab。可打印字节不在此处解释——
// 它们属于布局相关的字符映射，本层不做猜测。序列未结束时返回 NeedMore，
// 由调用方在下一次读取后继续喂字节；孤立 ESC 只有在超时后由 lone() 定键。
// ===========================================================================

//! ESC 序列解码。

use super::event::{KeyAction, KeyEvent, Mods};
use super::key;

/// 一次解码的结果。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Step {
    /// 得到一个完整的键，`used` 是本次消费的字节数。
    Key {
        /// 解出的按键。
        event: KeyEvent,
        /// 消费的字节数。
        used: usize,
    },
    /// 序列尚未结束，等更多字节再解。
    NeedMore,
    /// 不是本层认识的序列，`used` 是跳过的字节数。
    Unknown {
        /// 跳过的字节数。
        used: usize,
    },
}

/// `CSI <n> ~` 的数字到键码。
const TILDE: &[(u16, u16)] = &[
    (1, key::KEY_HOME),
    (2, key::KEY_INSERT),
    (3, key::KEY_DELETE),
    (4, key::KEY_END),
    (5, key::KEY_PAGEUP),
    (6, key::KEY_PAGEDOWN),
    (7, key::KEY_HOME),
    (8, key::KEY_END),
    (11, key::KEY_F1),
    (12, key::KEY_F2),
    (13, key::KEY_F3),
    (14, key::KEY_F4),
    (15, key::KEY_F5),
    (17, key::KEY_F6),
    (18, key::KEY_F7),
    (19, key::KEY_F8),
    (20, key::KEY_F9),
    (21, key::KEY_F10),
    (23, key::KEY_F11),
    (24, key::KEY_F12),
];

/// `CSI <final>` 的终结字节到键码。
const CSI_FINAL: &[(u8, u16)] = &[
    (b'A', key::KEY_UP),
    (b'B', key::KEY_DOWN),
    (b'C', key::KEY_RIGHT),
    (b'D', key::KEY_LEFT),
    (b'H', key::KEY_HOME),
    (b'F', key::KEY_END),
];

/// `SS3 <final>` 中方向键与 Home/End 复用 `CSI_FINAL`，这里只列功能键。
const SS3_FUNC: &[(u8, u16)] = &[
    (b'P', key::KEY_F1),
    (b'Q', key::KEY_F2),
    (b'R', key::KEY_F3),
    (b'S', key::KEY_F4),
];

/// 解码一个以 ESC 开头的序列。
pub fn decode(bytes: &[u8]) -> Step {
    match bytes.first() {
        None => Step::NeedMore,
        Some(0x1b) => after_esc(bytes),
        Some(_) => Step::Unknown { used: 1 },
    }
}

/// 超时后把孤立 ESC 定为 Esc 键。
pub fn lone() -> KeyEvent {
    KeyEvent {
        code: key::KEY_ESC,
        mods: Mods::default(),
        action: KeyAction::Press,
    }
}

/// 未完成序列的保留上限；超过即清空，避免没有终结字节的输入撑大缓冲。
pub const MAX_PENDING: usize = 64;

/// 字节流的增量解码器：跨读取保留未完成序列，把完整的键按顺序交出。
#[derive(Default)]
pub struct Decoder {
    buf: Vec<u8>,
}

/// `Decoder::take` 的三种结局；`Wait` 表示要等更多字节。
enum Taken {
    Event(KeyEvent),
    Skip,
    Wait,
}

impl Decoder {
    /// 新建空解码器。
    pub fn new() -> Self {
        Self::default()
    }

    /// 喂入一段字节，把已能定下来的键按到达顺序追加到 `out`。
    pub fn feed(&mut self, bytes: &[u8], out: &mut Vec<KeyEvent>) {
        self.buf.extend_from_slice(bytes);
        loop {
            match self.take() {
                Taken::Event(event) => out.push(event),
                Taken::Skip => {}
                Taken::Wait => break,
            }
        }
        if self.buf.len() > MAX_PENDING {
            self.buf.clear();
        }
    }

    /// 等待窗口结束后调用；缓冲只剩孤立 ESC 时把它定为 Esc 键。
    pub fn flush(&mut self, out: &mut Vec<KeyEvent>) {
        if self.buf.as_slice() == [0x1b] {
            self.buf.clear();
            out.push(lone());
        }
    }

    /// 仍未定键的字节数；用于判断等待窗口是否该结束。
    pub fn pending(&self) -> usize {
        self.buf.len()
    }

    fn take(&mut self) -> Taken {
        let Some(&first) = self.buf.first() else {
            return Taken::Wait;
        };
        if first != 0x1b {
            self.buf.remove(0);
            return key::key_byte(first).map_or(Taken::Skip, Taken::Event);
        }
        match decode(&self.buf) {
            Step::Key { event, used } => {
                self.buf.drain(..used);
                Taken::Event(event)
            }
            Step::Unknown { used } => {
                self.buf.drain(..used);
                Taken::Skip
            }
            Step::NeedMore => Taken::Wait,
        }
    }
}

fn after_esc(bytes: &[u8]) -> Step {
    match bytes.get(1) {
        None => Step::NeedMore,
        Some(b'[') => csi(bytes),
        Some(b'O') => ss3(bytes),
        Some(_) => Step::Unknown { used: 2 },
    }
}

fn csi(bytes: &[u8]) -> Step {
    let mut skip = false;
    for (pos, byte) in bytes.iter().enumerate().skip(2) {
        if (0x40..=0x7e).contains(byte) {
            return if skip {
                Step::Unknown { used: pos + 1 }
            } else {
                final_key(bytes, pos)
            };
        }
        if !byte.is_ascii_digit() && *byte != b';' {
            skip = true;
        }
    }
    Step::NeedMore
}

fn final_key(bytes: &[u8], end: usize) -> Step {
    let args = params(&bytes[2..end]);
    let fin = bytes[end];
    let code = match fin {
        b'Z' => Some(key::KEY_TAB),
        b'~' => lookup16(TILDE, args.first().copied().unwrap_or(0)),
        _ => lookup8(CSI_FINAL, fin),
    };
    let mods = if fin == b'Z' {
        Mods {
            shift: true,
            ctrl: false,
            alt: false,
        }
    } else {
        mods_of(&args)
    };
    match code {
        Some(code) => key_of(code, mods, end + 1),
        None => Step::Unknown { used: end + 1 },
    }
}

fn ss3(bytes: &[u8]) -> Step {
    let Some(&fin) = bytes.get(2) else {
        return Step::NeedMore;
    };
    let code = lookup8(CSI_FINAL, fin).or_else(|| lookup8(SS3_FUNC, fin));
    match code {
        Some(code) => key_of(code, Mods::default(), 3),
        None => Step::Unknown { used: 3 },
    }
}

fn key_of(code: u16, mods: Mods, used: usize) -> Step {
    Step::Key {
        event: KeyEvent {
            code,
            mods,
            action: KeyAction::Press,
        },
        used,
    }
}

fn params(area: &[u8]) -> Vec<u16> {
    area.split(|byte| *byte == b';')
        .map(|part| {
            part.iter().fold(0u16, |acc, byte| {
                acc.saturating_mul(10)
                    .saturating_add(u16::from(byte - b'0'))
            })
        })
        .collect()
}

fn mods_of(args: &[u16]) -> Mods {
    let bits = args.get(1).copied().unwrap_or(1).saturating_sub(1);
    Mods {
        shift: bits & 1 != 0,
        alt: bits & 2 != 0,
        ctrl: bits & 4 != 0,
    }
}

fn lookup8(table: &[(u8, u16)], key: u8) -> Option<u16> {
    table.iter().find(|pair| pair.0 == key).map(|pair| pair.1)
}

fn lookup16(table: &[(u16, u16)], key: u16) -> Option<u16> {
    table.iter().find(|pair| pair.0 == key).map(|pair| pair.1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn press(code: u16, mods: Mods) -> KeyEvent {
        KeyEvent {
            code,
            mods,
            action: KeyAction::Press,
        }
    }

    fn arrow(seq: &[u8]) -> KeyEvent {
        match decode(seq) {
            Step::Key { event, used } => {
                assert_eq!(used, seq.len(), "{seq:?}");
                event
            }
            other => panic!("{seq:?} => {other:?}"),
        }
    }

    #[test]
    fn arrows_and_home_end() {
        assert_eq!(arrow(b"\x1b[A"), press(key::KEY_UP, Mods::default()));
        assert_eq!(arrow(b"\x1b[B"), press(key::KEY_DOWN, Mods::default()));
        assert_eq!(arrow(b"\x1b[C"), press(key::KEY_RIGHT, Mods::default()));
        assert_eq!(arrow(b"\x1b[D"), press(key::KEY_LEFT, Mods::default()));
        assert_eq!(arrow(b"\x1b[H"), press(key::KEY_HOME, Mods::default()));
        assert_eq!(arrow(b"\x1b[F"), press(key::KEY_END, Mods::default()));
        assert_eq!(arrow(b"\x1bOA"), press(key::KEY_UP, Mods::default()));
        assert_eq!(arrow(b"\x1bOD"), press(key::KEY_LEFT, Mods::default()));
    }

    #[test]
    fn tilde_keys() {
        assert_eq!(arrow(b"\x1b[2~"), press(key::KEY_INSERT, Mods::default()));
        assert_eq!(arrow(b"\x1b[3~"), press(key::KEY_DELETE, Mods::default()));
        assert_eq!(arrow(b"\x1b[5~"), press(key::KEY_PAGEUP, Mods::default()));
        assert_eq!(arrow(b"\x1b[6~"), press(key::KEY_PAGEDOWN, Mods::default()));
        assert_eq!(arrow(b"\x1b[1~"), press(key::KEY_HOME, Mods::default()));
        assert_eq!(arrow(b"\x1b[4~"), press(key::KEY_END, Mods::default()));
        assert_eq!(arrow(b"\x1b[11~"), press(key::KEY_F1, Mods::default()));
        assert_eq!(arrow(b"\x1b[15~"), press(key::KEY_F5, Mods::default()));
        assert_eq!(arrow(b"\x1b[24~"), press(key::KEY_F12, Mods::default()));
        assert_eq!(arrow(b"\x1bOP"), press(key::KEY_F1, Mods::default()));
        assert_eq!(arrow(b"\x1bOS"), press(key::KEY_F4, Mods::default()));
    }

    #[test]
    fn modifiers_and_backtab() {
        let ctrl = Mods {
            shift: false,
            ctrl: true,
            alt: false,
        };
        assert_eq!(arrow(b"\x1b[1;5A"), press(key::KEY_UP, ctrl));
        let shift = Mods {
            shift: true,
            ctrl: false,
            alt: false,
        };
        assert_eq!(arrow(b"\x1b[1;2A"), press(key::KEY_UP, shift));
        assert_eq!(arrow(b"\x1b[Z"), press(key::KEY_TAB, shift));
        assert_eq!(
            arrow(b"\x1b[2A"),
            press(key::KEY_UP, Mods::default()),
            "CSI 参数是重复次数，不是修饰键"
        );
    }

    #[test]
    fn incomplete_and_unknown() {
        assert_eq!(decode(b""), Step::NeedMore);
        assert_eq!(decode(b"\x1b"), Step::NeedMore);
        assert_eq!(decode(b"\x1b["), Step::NeedMore);
        assert_eq!(decode(b"\x1b[1;"), Step::NeedMore);
        assert_eq!(decode(b"\x1bx"), Step::Unknown { used: 2 });
        assert_eq!(decode(b"\x1b[99~"), Step::Unknown { used: 5 });
        assert_eq!(
            decode(b"\x1b[?25h"),
            Step::Unknown { used: 6 },
            "私有模式整段跳过，避免残留的 25h 变成按键"
        );
        assert_eq!(decode(b"a"), Step::Unknown { used: 1 });
    }

    #[test]
    fn decoder_joins_split_sequences() {
        let mut dec = Decoder::new();
        let mut out = Vec::new();
        dec.feed(b"\x1b[", &mut out);
        assert!(out.is_empty(), "半条序列不得提前定键");
        assert_eq!(dec.pending(), 2);
        dec.feed(b"A", &mut out);
        assert_eq!(out, [press(key::KEY_UP, Mods::default())]);
        assert_eq!(dec.pending(), 0);
    }

    #[test]
    fn decoder_keeps_arrival_order() {
        let mut dec = Decoder::new();
        let mut out = Vec::new();
        dec.feed(b"a\x1b[1;5Cb", &mut out);
        let ctrl = Mods {
            shift: false,
            ctrl: true,
            alt: false,
        };
        assert_eq!(
            out,
            [
                press(30, Mods::default()),
                press(key::KEY_RIGHT, ctrl),
                press(48, Mods::default())
            ]
        );
    }

    #[test]
    fn decoder_drops_what_it_cannot_read() {
        let mut dec = Decoder::new();
        let mut out = Vec::new();
        dec.feed(b"\x1b[?25h\xe4", &mut out);
        assert!(out.is_empty(), "私有模式与非 ASCII 字节都不得产键");
        assert_eq!(dec.pending(), 0);
    }

    #[test]
    fn flush_only_settles_a_lone_escape() {
        let mut dec = Decoder::new();
        let mut out = Vec::new();
        dec.feed(b"\x1b", &mut out);
        dec.flush(&mut out);
        assert_eq!(out, [lone()]);

        dec.feed(b"\x1b[", &mut out);
        dec.flush(&mut out);
        assert_eq!(out, [lone()], "未完成的序列不因超时变成 Esc");
        assert_eq!(dec.pending(), 2, "未完成的序列留待后续字节");
    }

    #[test]
    fn lone_esc_is_its_own_key() {
        assert_eq!(lone().code, key::KEY_ESC);
        assert_eq!(lone().action, KeyAction::Press);
    }
}
