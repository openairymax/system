// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 契约标识符
//
// `trace_id` 是全链路唯一的关联键：接入、执行与审计三处日志靠它串起来。
// 它的形式属于契约本身，所以解析与规范化只在这里实现一份，其余层只引用
// 这个类型，不各自校验。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.1（`trace_id`）、§2.4。
// ===========================================================================

//! `airy.intent/1` 的标识符类型。

/// `trace_id` 不合法；调用方据此报 `invalid` 并把字段名写进附加字段。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TraceIdError;

/// 32 位十六进制的追踪号；重试必须复用，新请求不得复用。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TraceId(pub [u8; 16]);

impl TraceId {
    /// 十六进制字符数；等于底层字节数的两倍。
    pub const HEX_LEN: usize = 32;

    /// 从 32 位十六进制文本解析；长度与字符集不合法即拒绝。
    pub fn parse(text: &str) -> Result<Self, TraceIdError> {
        let bytes = text.as_bytes();
        if bytes.len() != Self::HEX_LEN {
            return Err(TraceIdError);
        }
        let mut raw = [0u8; 16];
        for (slot, pair) in raw.iter_mut().zip(bytes.chunks_exact(2)) {
            let hi = hex_val(pair[0]).ok_or(TraceIdError)?;
            let lo = hex_val(pair[1]).ok_or(TraceIdError)?;
            *slot = (hi << 4) | lo;
        }
        Ok(Self(raw))
    }

    /// 输出小写十六进制文本，长度恒为 32。
    pub fn to_hex(self) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut out = String::with_capacity(Self::HEX_LEN);
        for byte in self.0 {
            out.push(HEX[usize::from(byte >> 4)] as char);
            out.push(HEX[usize::from(byte & 0x0f)] as char);
        }
        out
    }
}

/// 单个十六进制字符的数值；非十六进制返回 `None`。
fn hex_val(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEXT: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";

    #[test]
    fn roundtrip_is_lowercase_hex() {
        let id = TraceId::parse(TEXT).expect("合法十六进制");
        assert_eq!(id.to_hex(), TEXT);
        assert_eq!(id.to_hex().len(), TraceId::HEX_LEN);
    }

    #[test]
    fn uppercase_is_accepted() {
        let id = TraceId::parse(&TEXT.to_uppercase()).expect("大写同样合法");
        assert_eq!(id.to_hex(), TEXT, "输出统一为小写");
    }

    #[test]
    fn malformed_is_rejected() {
        let cases = [
            "",
            "9f2c",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b4",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b400",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b4g",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b4 ",
        ];
        for text in cases {
            assert_eq!(TraceId::parse(text), Err(TraceIdError), "{text:?}");
        }
    }
}
