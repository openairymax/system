// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// `airy.intent/1` 的标识符与文档模型
//
// `trace_id` 是全链路唯一的关联键：接入、执行与审计三处日志靠它串起来。
// 它的形式属于契约本身，所以解析与规范化只在这里实现一份，其余层只引用
// 这个类型，不各自校验。整份意图同样只在此处建模：字段、必填与条件的
// 判定都对着 §2.1 的表格写，避免每个入口各抄一份比较式。
//
// 兼容策略按 §6：消费方忽略未知字段，故不设 `deny_unknown_fields`；新增
// 可选字段不会让旧消费方拒绝整条意图。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.1、§2.3、§2.4、§3.2、§4.3、§6。
// ===========================================================================

//! `airy.intent/1` 的标识符、文档模型与接入侧预检。

use serde::Deserialize;
use serde_json::Value;

use crate::trust::{SourceKind, Trust};

/// 契约 §2.1 的固定 schema 值；MAJOR 变更才改此值。
pub const SCHEMA: &str = "airy.intent/1";

/// `trace_id` 不合法；调用方据此报 `invalid` 并把字段名写进附加字段。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TraceIdError;

impl core::fmt::Display for TraceIdError {
    fn fmt(&self, out: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        out.write_str("trace_id 必须是 32 位十六进制")
    }
}

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

impl<'de> Deserialize<'de> for TraceId {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let text = String::deserialize(deserializer)?;
        Self::parse(&text).map_err(serde::de::Error::custom)
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

/// 会话所处通路。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum SessionMode {
    /// 引导期 TUI。
    Tui,
    /// 桌面 dock。
    Gui,
    /// 无界面批处理。
    Headless,
}

/// 载荷种类。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum PayloadKind {
    /// 文本，已含 IME 提交结果。
    Text,
    /// 已注册动作的结构化调用。
    Structured,
    /// 需要回放时保留的音频引用。
    AudioRef,
}

/// 来源标识。
#[derive(Clone, Debug, Deserialize)]
pub struct Source {
    /// 来源种类；与信任级别的配对见 §4.1。
    pub kind: SourceKind,
    /// 设备标识；无设备时为 `local`。
    #[serde(default = "local")]
    pub device: String,
}

/// 信任凭据。
#[derive(Clone, Debug, Deserialize)]
pub struct TrustTag {
    /// 级别；由适配器判定，组合层不得改写。
    pub level: Trust,
    /// 发起者标识：`console` / `uid:1000` / `svc:<name>`。
    pub principal: String,
}

/// 会话标识。
#[derive(Clone, Debug, Deserialize)]
pub struct Session {
    /// 所处通路。
    pub mode: SessionMode,
    /// 会话标识，用于审计关联。
    pub id: String,
}

/// 语音回放引用；`digest` 与实物不符由执行侧拒绝。
#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct AudioRef {
    /// 受管目录内的相对路径。
    pub path: String,
    /// `sha256:` 前缀的摘要。
    pub digest: String,
    /// 时长，单位毫秒。
    pub duration_ms: u32,
}

/// 已注册动作的结构化调用；未注册的动作由执行侧拒绝。
#[derive(Clone, Debug, Deserialize)]
pub struct Structured {
    /// 动作名，如 `disk.status`。
    pub action: String,
    /// 动作参数；形状由动作自身定义。
    #[serde(default)]
    pub args: Option<Value>,
    /// 期望的结果 schema。
    #[serde(default)]
    pub expect: Option<Expect>,
}

/// 结构化调用期望的结果 schema。
#[derive(Clone, Debug, Deserialize)]
pub struct Expect {
    /// 结果遵循的 schema 标识。
    pub schema: String,
}

/// 意图载荷；`kind` 决定哪个字段必填。
#[derive(Clone, Debug, Deserialize)]
pub struct Payload {
    /// 载荷种类。
    pub kind: PayloadKind,
    /// 文本，UTF-8。
    #[serde(default)]
    pub text: Option<String>,
    /// BCP 47 语言标记；缺省 `zh-CN`。
    #[serde(default = "zh_cn")]
    pub lang: String,
    /// 结构化调用。
    #[serde(default)]
    pub structured: Option<Structured>,
    /// 音频引用。
    #[serde(default)]
    pub audio_ref: Option<AudioRef>,
}

impl Payload {
    fn check(&self) -> Result<(), CheckError> {
        match self.kind {
            PayloadKind::Text => self.need_text(),
            PayloadKind::Structured => self.need_struct(),
            PayloadKind::AudioRef => self.need_audio(),
        }
    }

    fn need_text(&self) -> Result<(), CheckError> {
        match self.text.as_deref() {
            Some(text) if !text.is_empty() => Ok(()),
            _ => Err(CheckError::Invalid("payload.text")),
        }
    }

    fn need_struct(&self) -> Result<(), CheckError> {
        let action = self.structured.as_ref().map(|item| item.action.as_str());
        match action {
            Some(action) if !action.is_empty() => Ok(()),
            _ => Err(CheckError::Invalid("payload.structured")),
        }
    }

    fn need_audio(&self) -> Result<(), CheckError> {
        match self.audio_ref.as_ref() {
            Some(reference) if reference.well_formed() => Ok(()),
            _ => Err(CheckError::Invalid("payload.audio_ref")),
        }
    }
}

impl AudioRef {
    fn well_formed(&self) -> bool {
        !self.path.is_empty() && self.digest.starts_with("sha256:")
    }
}

/// 执行约束。
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Constraints {
    /// 端到端预算，单位毫秒；超时即取消。
    #[serde(default)]
    pub deadline_ms: Option<u32>,
    /// 允许的动作类别白名单；缺省表示不设白名单。
    #[serde(default)]
    pub allow: Option<Vec<String>>,
    /// 禁止的动作类别黑名单，优先级高于白名单。
    #[serde(default)]
    pub deny: Vec<String>,
}

/// 高危动作的审批凭据；不是布尔值，而是一条可审计记录。
#[derive(Clone, Debug, Deserialize)]
pub struct Approval {
    /// 审批标识。
    pub id: String,
    /// 审批人标识。
    pub by: String,
    /// 审批时刻，RFC 3339。
    pub ts: String,
    /// 覆盖的动作类别；必须包含实际动作。
    pub scope: Vec<String>,
    /// 只对该追踪号有效。
    pub trace_id: TraceId,
    /// 有效期，单位毫秒；过期即作废。
    #[serde(default)]
    pub expires_ms: Option<u64>,
}

/// 一条完整的意图，对应契约 §2.1 的字段表。
#[derive(Clone, Debug, Deserialize)]
pub struct Intent {
    /// 固定 `airy.intent/1`。
    pub schema: String,
    /// 追踪号。
    pub trace_id: TraceId,
    /// 产生时刻，RFC 3339。
    pub ts: String,
    /// 来源。
    pub source: Source,
    /// 信任凭据。
    pub trust: TrustTag,
    /// 会话。
    pub session: Session,
    /// 载荷。
    pub payload: Payload,
    /// 执行约束。
    #[serde(default)]
    pub constraints: Constraints,
    /// 审批凭据。
    #[serde(default)]
    pub approval: Option<Approval>,
}

/// 接入侧预检的失败语义；与契约 §7 的稳定状态串对应。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CheckError {
    /// 语法或必填字段不通过，对应 `-EINVAL`。
    Invalid(&'static str),
    /// 来源与信任级别不匹配，对应 `-EACCES`。
    Denied(&'static str),
}

impl CheckError {
    /// 首个不通过的字段名。
    pub fn field(self) -> &'static str {
        match self {
            Self::Invalid(field) | Self::Denied(field) => field,
        }
    }

    /// 契约 §7 的稳定状态串。
    pub fn status(self) -> &'static str {
        match self {
            Self::Invalid(_) => "invalid",
            Self::Denied(_) => "denied",
        }
    }
}

impl Intent {
    /// 按 §2.4 的顺序做接入侧预检的第 1、3、4 步。
    ///
    /// 第 2 步的唯一性、第 5 步的白黑名单与第 6 步的动作注册分别属于
    /// 会话层、路由与执行侧，不在文档模型里判定。
    pub fn check(&self) -> Result<(), CheckError> {
        if self.schema != SCHEMA {
            return Err(CheckError::Invalid("schema"));
        }
        self.check_meta()?;
        if !self.source.kind.allows(self.trust.level) {
            return Err(CheckError::Denied("trust.level"));
        }
        self.payload.check()
    }

    fn check_meta(&self) -> Result<(), CheckError> {
        let required = [
            (self.ts.as_str(), "ts"),
            (self.trust.principal.as_str(), "trust.principal"),
            (self.session.id.as_str(), "session.id"),
        ];
        for (value, field) in required {
            if value.is_empty() {
                return Err(CheckError::Invalid(field));
            }
        }
        Ok(())
    }
}

fn local() -> String {
    "local".to_string()
}

fn zh_cn() -> String {
    "zh-CN".to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEXT: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";

    const SAMPLE: &str = r#"{
        "schema": "airy.intent/1",
        "trace_id": "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40",
        "ts": "2026-11-17T10:02:11+08:00",
        "source": {"kind": "keyboard", "device": "tty1"},
        "trust": {"level": "T0", "principal": "console"},
        "session": {"mode": "tui", "id": "s-0f31"},
        "payload": {"kind": "text", "text": "查看磁盘使用情况", "lang": "zh-CN"},
        "constraints": {"deadline_ms": 5000, "deny": ["disk.format"]}
    }"#;

    fn parse(json: &str) -> Intent {
        serde_json::from_str(json).expect("示例必须可解析")
    }

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

    #[test]
    fn sample_matches_the_contract() {
        let intent = parse(SAMPLE);
        assert_eq!(intent.schema, SCHEMA);
        assert_eq!(intent.trace_id.to_hex(), TEXT);
        assert_eq!(intent.source.kind, SourceKind::Key);
        assert_eq!(intent.source.device, "tty1");
        assert_eq!(intent.trust.level, Trust::T0);
        assert_eq!(intent.session.mode, SessionMode::Tui);
        assert_eq!(intent.payload.kind, PayloadKind::Text);
        assert_eq!(intent.payload.lang, "zh-CN");
        assert_eq!(intent.constraints.deadline_ms, Some(5000));
        assert_eq!(intent.constraints.deny, ["disk.format"]);
        assert!(intent.constraints.allow.is_none(), "缺省不设白名单");
        assert!(intent.check().is_ok());
    }

    #[test]
    fn unknown_fields_are_ignored() {
        let json = SAMPLE.replace("\"ts\":", "\"future\": 1, \"ts\":");
        let intent = parse(&json);
        assert!(intent.check().is_ok(), "旧消费方不得因新字段拒绝");
    }

    #[test]
    fn schema_and_required_fields_are_checked() {
        let wrong = SAMPLE.replace("airy.intent/1", "airy.intent/2");
        assert_eq!(parse(&wrong).check(), Err(CheckError::Invalid("schema")));
        let empty = SAMPLE.replace("\"console\"", "\"\"");
        assert_eq!(
            parse(&empty).check(),
            Err(CheckError::Invalid("trust.principal"))
        );
    }

    #[test]
    fn trust_must_match_the_source() {
        let lifted = SAMPLE.replace("\"level\": \"T0\"", "\"level\": \"T1\"");
        let err = parse(&lifted).check().expect_err("键盘不得升到 T1");
        assert_eq!(err.status(), "denied");
        assert_eq!(err.field(), "trust.level");
    }

    #[test]
    fn payload_must_match_its_kind() {
        let missing = SAMPLE.replace(", \"text\": \"查看磁盘使用情况\"", "");
        assert_eq!(
            parse(&missing).check(),
            Err(CheckError::Invalid("payload.text"))
        );
        let structured = SAMPLE.replace(
            r#""kind": "text", "text": "查看磁盘使用情况""#,
            r#""kind": "structured", "structured": {"action": "disk.status"}"#,
        );
        let intent = parse(&structured);
        assert_eq!(intent.payload.kind, PayloadKind::Structured);
        assert!(intent.check().is_ok());
    }

    #[test]
    fn audio_ref_needs_a_sha256_digest() {
        let json = SAMPLE.replace(
            r#""kind": "text", "text": "查看磁盘使用情况""#,
            r#""kind": "audio-ref", "audio_ref":
               {"path": "asr/7d1e.wav", "digest": "sha256:00ff", "duration_ms": 3120}"#,
        );
        assert!(parse(&json).check().is_ok());
        let bad = json.replace("sha256:00ff", "00ff");
        assert_eq!(
            parse(&bad).check(),
            Err(CheckError::Invalid("payload.audio_ref"))
        );
    }

    #[test]
    fn approval_is_bound_to_one_trace() {
        let json = SAMPLE.replace(
            "\"constraints\"",
            r#""approval": {"id": "ap-7d1e", "by": "console",
               "ts": "2026-11-17T10:02:14+08:00", "scope": ["disk.format"],
               "trace_id": "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40",
               "expires_ms": 60000},
               "constraints""#,
        );
        let intent = parse(&json);
        let approval = intent.approval.expect("审批记录必须解析");
        assert_eq!(approval.trace_id, intent.trace_id, "只对同一追踪号有效");
        assert_eq!(approval.scope, ["disk.format"]);
    }
}
