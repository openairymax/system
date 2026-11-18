// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// L1 输入事件的载荷与元数据
//
// 事件带来源、信任级别、单调时刻与追踪号；载荷保持各来源的原貌，
// 归一化只发生在组合层。字段语义以接入契约为准。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.1、§4.1、§10.2。
// ===========================================================================

/// 来源种类，与 `airy.intent/1` 的 `source.kind` 一一对应。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SourceKind {
    /// 本地键盘：适配器只产键码，不产文本。
    Key,
    /// 语音：识别结果先进可编辑缓冲区，确认后才成为意图。
    Voice,
    /// 外接结构化 API：身份由 `SO_PEERCRED` 决定。
    Api,
    /// 批处理：逐行一个意图，信任级别固定 T3。
    Batch,
}

/// 信任级别；只由适配器判定，组合层不得改写。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Trust {
    /// 本地控制台用户。
    T0,
    /// 已认证会话用户。
    T1,
    /// 服务主体。
    T2,
    /// 自动化。
    T3,
}

impl Trust {
    /// 契约 §4.2 的写法，用于日志与错误附加字段。
    pub fn label(self) -> &'static str {
        match self {
            Self::T0 => "T0",
            Self::T1 => "T1",
            Self::T2 => "T2",
            Self::T3 => "T3",
        }
    }
}

/// 32 位十六进制的追踪号；重试必须复用，新请求不得复用。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TraceId(pub [u8; 16]);

impl TraceId {
    /// 十六进制字符数；等于底层字节数的两倍。
    pub const HEX_LEN: usize = 32;

    /// 从 32 位十六进制文本解析；长度与字符集不合法即拒绝。
    pub fn parse(text: &str) -> Result<Self, super::SourceError> {
        let bytes = text.as_bytes();
        let bad = || super::SourceError::Invalid { field: "trace_id" };
        if bytes.len() != Self::HEX_LEN {
            return Err(bad());
        }
        let mut raw = [0u8; 16];
        for (slot, pair) in raw.iter_mut().zip(bytes.chunks_exact(2)) {
            let hi = hex_val(pair[0]).ok_or_else(bad)?;
            let lo = hex_val(pair[1]).ok_or_else(bad)?;
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

/// 单调时钟读数，单位纳秒，用于乱序与超时判定。
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct MonoTime(pub u64);

/// 按键动作。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum KeyAction {
    /// 按下。
    Press,
    /// 长按重复。
    Repeat,
    /// 抬起。
    Release,
}

/// 修饰键；只表示物理状态，不决定字符大小写。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Mods {
    /// Shift。
    pub shift: bool,
    /// Control。
    pub ctrl: bool,
    /// Alt。
    pub alt: bool,
}

/// 键码与修饰键；`code` 用 Linux `KEY_*` 码，文本由组合层决定。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct KeyEvent {
    /// Linux `KEY_*` 键码。
    pub code: u16,
    /// 同时处于按下状态的修饰键。
    pub mods: Mods,
    /// 本次动作。
    pub action: KeyAction,
}

/// 语音回放引用；`digest` 与实物不符即拒绝。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AudioRef {
    /// 受管目录内的相对路径。
    pub path: String,
    /// `sha256:` 前缀的摘要。
    pub digest: String,
    /// 时长，单位毫秒。
    pub duration_ms: u32,
}

/// L1 载荷：保持来源原貌，不在此处做归一化。
#[derive(Clone, Debug)]
pub enum RawBody {
    /// 键码事件，不含文本。
    Key(KeyEvent),
    /// 语音识别结果与置信度。
    Transcript {
        /// 识别文本。
        text: String,
        /// 置信度，取值 0.0–1.0。
        confidence: f32,
        /// 或可回放的音频引用。
        audio_ref: AudioRef,
    },
    /// 已结构化的载荷，待 schema 校验。
    Structured(serde_json::Value),
    /// 批处理的一行。
    Line(String),
}

/// L1 事件：四个适配器的统一产出。
#[derive(Clone, Debug)]
pub struct RawEvent {
    /// 追踪号，随执行链路透传。
    pub trace_id: TraceId,
    /// 来源种类。
    pub source: SourceKind,
    /// 该来源允许的信任级别。
    pub trust: Trust,
    /// 产生时刻。
    pub at: MonoTime,
    /// 来源载荷。
    pub body: RawBody,
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEXT: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";

    #[test]
    fn trace_id_roundtrip() {
        let id = TraceId::parse(TEXT).expect("合法十六进制");
        assert_eq!(id.to_hex(), TEXT);
        assert_eq!(id.to_hex().len(), TraceId::HEX_LEN);
    }

    #[test]
    fn trace_id_accepts_uppercase() {
        let upper = TEXT.to_uppercase();
        let id = TraceId::parse(&upper).expect("大写同样合法");
        assert_eq!(id.to_hex(), TEXT, "输出统一为小写");
    }

    #[test]
    fn trace_id_rejects_malformed() {
        let cases = [
            "",
            "9f2c",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b4",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b400",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b4g",
            "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b4 ",
        ];
        for text in cases {
            let err = TraceId::parse(text).expect_err("必须拒绝");
            assert_eq!(err.status(), "invalid", "{text:?}");
            assert_eq!(err.detail(), "trace_id", "{text:?}");
        }
    }
}
