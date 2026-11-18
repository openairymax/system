// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 识别结果与可用性
//
// 语音通道只承诺四件事（§14.7）：有模型可用、无模型显式不可用、结果可
// 编辑可放弃、不绕过审批。本文件负责前两件与「结果」这一层的数据约束：
// 空识别不等于空字符串，而是 `asr=empty`；没有模型不等于键盘兜底，而是
// `asr=unavailable`。任何一条被静默改写都会让状态栏说谎。
//
// audio-ref 的摘要引用按接口册 §3.2 固定为 `sha256:<64 位十六进制>`。
// ===========================================================================

use crate::capture::Model;
use crate::AsrError;

/// 一次语音输入的可观测状态；字符串即状态栏取值。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Availability {
    /// 模型已校验，可以开始采集。
    Ready,
    /// 模型缺失、清单损坏或摘要不符。
    Unavailable,
    /// 录音设备被占用。
    Busy,
    /// 识别结果为空。
    Empty,
}

impl Availability {
    /// 状态栏取值：`asr=<status>`。
    pub fn status(&self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::Unavailable => "unavailable",
            Self::Busy => "busy",
            Self::Empty => "empty",
        }
    }

    /// 由已校验的模型推出可用性；`None` 即显式不可用。
    pub fn for_model(model: Option<&Model>) -> Self {
        match model {
            Some(_) => Self::Ready,
            None => Self::Unavailable,
        }
    }

    /// 由状态栏取值还原；未知取值返回 `None`，不做近似匹配。
    pub fn from_status(text: &str) -> Option<Self> {
        Some(match text {
            "ready" => Self::Ready,
            "unavailable" => Self::Unavailable,
            "busy" => Self::Busy,
            "empty" => Self::Empty,
            _ => return None,
        })
    }

    /// 是否允许开始一次采集。
    pub fn usable(&self) -> bool {
        matches!(self, Self::Ready)
    }
}

/// 音频引用；只描述本地受管文件，接入侧不解析路径。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AudioRef {
    path: String,
    digest: String,
    duration_ms: u32,
}

impl AudioRef {
    /// 逐字段校验；路径为空、摘要格式错或时长为零都拒绝。
    pub fn new(
        path: impl Into<String>,
        digest: impl Into<String>,
        duration_ms: u32,
    ) -> Result<Self, AsrError> {
        let path = path.into();
        let digest = digest.into();
        if path.is_empty() || duration_ms == 0 {
            return Err(AsrError::Field);
        }
        if !digest_ok(&digest) {
            return Err(AsrError::Field);
        }
        Ok(Self {
            path,
            digest,
            duration_ms,
        })
    }

    /// 本地受管路径。
    pub fn path(&self) -> &str {
        &self.path
    }

    /// `sha256:<64 位十六进制>`。
    pub fn digest(&self) -> &str {
        &self.digest
    }

    /// 时长，毫秒。
    pub fn duration_ms(&self) -> u32 {
        self.duration_ms
    }
}

/// 识别结果；进入可编辑缓冲区，用户确认后才成为 Intent。
#[derive(Clone, Debug, PartialEq)]
pub struct Transcript {
    text: String,
    confidence: f32,
    audio: AudioRef,
}

impl Transcript {
    /// 校验并构造；空文本映射 `asr=empty`，置信度必须落在 0–1。
    pub fn new(
        text: impl Into<String>,
        confidence: f32,
        audio: AudioRef,
    ) -> Result<Self, AsrError> {
        let text = text.into();
        if text.is_empty() {
            return Err(AsrError::Empty);
        }
        if !confidence.is_finite() || !(0.0..=1.0).contains(&confidence) {
            return Err(AsrError::Field);
        }
        Ok(Self {
            text,
            confidence,
            audio,
        })
    }

    /// 识别文本。
    pub fn text(&self) -> &str {
        &self.text
    }

    /// 置信度，0–1。
    pub fn confidence(&self) -> f32 {
        self.confidence
    }

    /// 音频引用。
    pub fn audio(&self) -> &AudioRef {
        &self.audio
    }
}

/// 摘要必须是 `sha256:` 加 64 位十六进制；大小写不敏感。
fn digest_ok(text: &str) -> bool {
    let Some(hex) = text.strip_prefix("sha256:") else {
        return false;
    };
    hex.len() == 64 && hex.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn audio() -> AudioRef {
        AudioRef::new(
            "/var/lib/airy/asr/1.wav",
            format!("sha256:{}", "a".repeat(64)),
            3120,
        )
        .expect("可构造")
    }

    #[test]
    fn audio_ref_accepts_valid_fields() {
        let audio = audio();
        assert_eq!(audio.path(), "/var/lib/airy/asr/1.wav");
        assert_eq!(audio.duration_ms(), 3120);
    }

    #[test]
    fn audio_ref_rejects_bad_digest() {
        for digest in ["deadbeef".to_string(), format!("sha256:{}", "g".repeat(64))] {
            assert_eq!(AudioRef::new("/tmp/a.wav", digest, 1), Err(AsrError::Field));
        }
        assert_eq!(
            AudioRef::new("", format!("sha256:{}", "a".repeat(64)), 1),
            Err(AsrError::Field)
        );
        assert_eq!(
            AudioRef::new("/tmp/a.wav", format!("sha256:{}", "a".repeat(64)), 0),
            Err(AsrError::Field)
        );
    }

    #[test]
    fn transcript_accepts_non_empty_text() {
        let item = Transcript::new("打开终端", 0.82, audio()).expect("可构造");
        assert_eq!(item.text(), "打开终端");
        assert_eq!(item.confidence(), 0.82);
    }

    #[test]
    fn transcript_rejects_empty_text() {
        assert_eq!(Transcript::new("", 0.5, audio()), Err(AsrError::Empty));
    }

    #[test]
    fn transcript_rejects_bad_confidence() {
        for value in [-0.1, 1.1, f32::NAN, f32::INFINITY] {
            assert_eq!(
                Transcript::new("打开终端", value, audio()),
                Err(AsrError::Field)
            );
        }
    }

    #[test]
    fn availability_tokens_match_the_matrix() {
        assert_eq!(Availability::Ready.status(), "ready");
        assert_eq!(Availability::Unavailable.status(), "unavailable");
        assert_eq!(Availability::Busy.status(), "busy");
        assert_eq!(Availability::Empty.status(), "empty");
        assert!(Availability::Ready.usable());
        assert!(!Availability::Unavailable.usable());
    }

    #[test]
    fn missing_model_is_explicitly_unavailable() {
        assert_eq!(Availability::for_model(None), Availability::Unavailable);
    }

    #[test]
    fn status_tokens_roundtrip() {
        for state in [
            Availability::Ready,
            Availability::Unavailable,
            Availability::Busy,
            Availability::Empty,
        ] {
            assert_eq!(Availability::from_status(state.status()), Some(state));
        }
        assert_eq!(Availability::from_status("unknown"), None);
        assert_eq!(Availability::from_status(""), None);
    }
}
