// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// boot-ime 的 JSON Lines 协议
//
// 输入法进程与 TUI 之间只传六种消息：open、key、select、commit、reset、
// close。每条消息必带 `session` 与 `seq`：前者区分会话，后者是崩溃重放
// 的定位键。boot-tui 侧保留 256 条未提交键事件，进程重启后按 seq 续放，
// 因此 seq 必须原样穿过协议，不能在任一方向被重编号。
//
// 单条消息上限 64 KiB：这是「不让一个畸形输入把对端读空」的硬边界，而
// 不是性能调优值。超限直接判错并关闭会话，不做截断重试——截断后的半个
// 键事件无法重放，继续读只会把错误推给用户。
//
// 50 ms 是「慢」的观测阈值，200 ms 是「恢复失败」的阈值：前者只用于状态
// 栏提示（`ime=slow`），后者才建议改用 ASCII。两者都不是失败状态串，
// 不写进 §7 的失败表。
//
// SSoT: 0.0.5 方案 §14.6；本 crate 的 SPEC.md。
// ===========================================================================

//! 输入法进程的帧模型、编解码与长度边界。

use core::fmt;
use std::io::{BufRead, Read, Write};

/// 单条消息的字节上限；不含行尾换行。
pub const MAX_FRAME: usize = 64 * 1024;

/// 键事件处理超过该毫秒数即标记 `ime=slow`，不改变结果。
pub const SLOW_MS: u64 = 50;

/// 恢复超过该毫秒数即建议改用 ASCII，不再等待重放。
pub const RECOVER_MS: u64 = 200;

/// 崩溃恢复时保留并可重放的未提交键事件条数。
pub const REPLAY: usize = 256;

// 慢阈值必须先于恢复阈值，且重放窗口非空；写死在编译期，改错即构建失败。
const _: () = assert!(SLOW_MS < RECOVER_MS);
const _: () = assert!(REPLAY > 0);

/// 六种消息；`msg` 字段即这里的判别名。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Msg {
    /// 建立会话并声明词典版本。
    Open,
    /// 一个已解码的键事件。
    Key,
    /// 选中第几号候选。
    Select,
    /// 提交当前候选。
    Commit,
    /// 清空未提交组合。
    Reset,
    /// 关闭会话。
    Close,
}

impl Msg {
    /// 线格式里的稳定名字。
    pub fn name(self) -> &'static str {
        match self {
            Self::Open => "open",
            Self::Key => "key",
            Self::Select => "select",
            Self::Commit => "commit",
            Self::Reset => "reset",
            Self::Close => "close",
        }
    }
}

/// 一条消息；`key` 与 `index` 只在对应用途上出现。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Frame {
    /// 会话标识，不得为空。
    pub session: String,
    /// 单调递增的序号；重放靠它对齐。
    pub seq: u64,
    /// 消息类别。
    pub msg: Msg,
    /// `Key` 的键名；其余消息为 `None`。
    pub key: Option<String>,
    /// `Select` 的候选序号；其余消息为 `None`。
    pub index: Option<u8>,
}

impl Frame {
    /// 构造一条不带附加字段的消息。
    pub fn new(session: impl Into<String>, seq: u64, msg: Msg) -> Self {
        Self {
            session: session.into(),
            seq,
            msg,
            key: None,
            index: None,
        }
    }

    /// 构造按键消息。
    pub fn key(session: impl Into<String>, seq: u64, key: impl Into<String>) -> Self {
        Self {
            key: Some(key.into()),
            ..Self::new(session, seq, Msg::Key)
        }
    }

    /// 构造选择消息。
    pub fn select(session: impl Into<String>, seq: u64, index: u8) -> Self {
        Self {
            index: Some(index),
            ..Self::new(session, seq, Msg::Select)
        }
    }

    /// 解析一行；超长、空会话与未知消息都拒绝。
    pub fn parse(line: &str) -> Result<Self, IpcError> {
        let text = line.trim_end();
        if text.len() > MAX_FRAME {
            return Err(IpcError::Long);
        }
        let wire: Wire = serde_json::from_str(text).map_err(|_| IpcError::Json)?;
        wire.into_frame()
    }

    /// 序列化为单行 JSON；超过上限或字段不自洽时拒绝。
    pub fn encode(&self) -> Result<String, IpcError> {
        if self.session.is_empty() {
            return Err(IpcError::Empty);
        }
        require(self.msg, self.key.as_ref(), self.index)?;
        let line = serde_json::to_string(&Wire::of(self)).map_err(|_| IpcError::Json)?;
        if line.len() > MAX_FRAME {
            Err(IpcError::Long)
        } else {
            Ok(line)
        }
    }
}

/// 从字节流读一行并解析；EOF 返回 [`IpcError::Eof`]。
pub fn read<R: BufRead>(source: &mut R) -> Result<Frame, IpcError> {
    let mut line = Vec::new();
    let limit = (MAX_FRAME + 2) as u64;
    Read::take(&mut *source, limit)
        .read_until(b'\n', &mut line)
        .map_err(IpcError::Io)?;
    if line.is_empty() {
        return Err(IpcError::Eof);
    }
    if line.len() > MAX_FRAME + 1 {
        return Err(IpcError::Long);
    }
    let text = core::str::from_utf8(&line).map_err(|_| IpcError::Json)?;
    Frame::parse(text)
}

/// 写一行 JSON；写完立即刷新。
pub fn write<W: Write>(sink: &mut W, frame: &Frame) -> Result<(), IpcError> {
    let line = frame.encode()?;
    sink.write_all(line.as_bytes()).map_err(IpcError::Io)?;
    sink.write_all(b"\n").map_err(IpcError::Io)?;
    sink.flush().map_err(IpcError::Io)
}

/// 协议错误；只有 `Long` 与 `Json` 会终止会话。
#[derive(Debug)]
pub enum IpcError {
    /// 消息超过 64 KiB。
    Long,
    /// 会话标识为空。
    Empty,
    /// 不是合法 JSON 或缺少必需字段。
    Json,
    /// 读端已到文件尾。
    Eof,
    /// 底层读写失败。
    Io(std::io::Error),
}

impl fmt::Display for IpcError {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Long => out.write_str("消息超过 64 KiB"),
            Self::Empty => out.write_str("会话标识为空"),
            Self::Json => out.write_str("消息不是合法 JSON"),
            Self::Eof => out.write_str("对端已关闭"),
            Self::Io(error) => write!(out, "读写失败: {error}"),
        }
    }
}

/// 线格式；`msg` 是字符串判别名，`key` 与 `index` 按需出现。
#[derive(serde::Deserialize, serde::Serialize)]
struct Wire {
    session: String,
    seq: u64,
    msg: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    index: Option<u8>,
}

impl Wire {
    /// 由帧生成线格式，并保证附加字段只在对应用途上出现。
    fn of(frame: &Frame) -> Self {
        let key = matches!(frame.msg, Msg::Key)
            .then(|| frame.key.clone())
            .flatten();
        let index = matches!(frame.msg, Msg::Select)
            .then_some(frame.index)
            .flatten();
        Self {
            session: frame.session.clone(),
            seq: frame.seq,
            msg: frame.msg.name().to_string(),
            key,
            index,
        }
    }

    /// 校验判别名与附加字段后转成帧。
    fn into_frame(self) -> Result<Frame, IpcError> {
        if self.session.is_empty() {
            return Err(IpcError::Empty);
        }
        let msg = decode(&self.msg)?;
        require(msg, self.key.as_ref(), self.index)?;
        Ok(Frame {
            session: self.session,
            seq: self.seq,
            msg,
            key: self.key,
            index: self.index,
        })
    }
}

/// 判别名到消息类别；未知名字一律拒绝。
fn decode(name: &str) -> Result<Msg, IpcError> {
    let msg = match name {
        "open" => Msg::Open,
        "key" => Msg::Key,
        "select" => Msg::Select,
        "commit" => Msg::Commit,
        "reset" => Msg::Reset,
        "close" => Msg::Close,
        _ => return Err(IpcError::Json),
    };
    Ok(msg)
}

/// 附加字段必须与消息类别一致，缺失或多余都判错。
fn require(msg: Msg, key: Option<&String>, index: Option<u8>) -> Result<(), IpcError> {
    let keyed = (msg == Msg::Key) == key.is_some();
    let indexed = (msg == Msg::Select) == index.is_some();
    if keyed && indexed {
        Ok(())
    } else {
        Err(IpcError::Json)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn plain_frames_roundtrip() {
        for msg in [Msg::Open, Msg::Commit, Msg::Reset, Msg::Close] {
            let frame = Frame::new("s-1", 3, msg);
            let line = frame.encode().expect("可编码");
            assert_eq!(Frame::parse(&line).expect("可解析"), frame);
        }
    }

    #[test]
    fn key_frame_carries_the_key() {
        let frame = Frame::key("s-1", 4, "zhong");
        let line = frame.encode().expect("可编码");
        assert!(line.contains("\"msg\":\"key\""));
        assert_eq!(Frame::parse(&line).expect("可解析"), frame);
    }

    #[test]
    fn select_frame_carries_the_index() {
        let frame = Frame::select("s-1", 5, 2);
        let line = frame.encode().expect("可编码");
        assert_eq!(Frame::parse(&line).expect("可解析"), frame);
    }

    #[test]
    fn inconsistent_frame_is_not_encodable() {
        let frame = Frame::new("s-1", 1, Msg::Key);
        assert!(matches!(frame.encode(), Err(IpcError::Json)));
    }

    #[test]
    fn missing_required_field_is_rejected() {
        let line = r#"{"session":"s-1","seq":1,"msg":"key"}"#;
        assert!(matches!(Frame::parse(line), Err(IpcError::Json)));
    }

    #[test]
    fn empty_session_is_rejected() {
        let line = r#"{"session":"","seq":1,"msg":"open"}"#;
        assert!(matches!(Frame::parse(line), Err(IpcError::Empty)));
    }

    #[test]
    fn unknown_message_is_rejected() {
        let line = r#"{"session":"s-1","seq":1,"msg":"paste"}"#;
        assert!(matches!(Frame::parse(line), Err(IpcError::Json)));
    }

    #[test]
    fn overlong_line_is_rejected() {
        let line = "x".repeat(MAX_FRAME + 1);
        assert!(matches!(Frame::parse(&line), Err(IpcError::Long)));
    }

    #[test]
    fn overlong_frame_cannot_be_encoded() {
        let frame = Frame::key("s-1", 1, "x".repeat(MAX_FRAME));
        assert!(matches!(frame.encode(), Err(IpcError::Long)));
    }

    #[test]
    fn stream_roundtrip_stops_at_eof() {
        let frames = [Frame::key("s-1", 1, "z"), Frame::select("s-1", 2, 0)];
        let mut buffer = Vec::new();
        for frame in &frames {
            write(&mut buffer, frame).expect("可写");
        }
        let mut cursor = Cursor::new(buffer);
        assert_eq!(read(&mut cursor).expect("可读"), frames[0]);
        assert_eq!(read(&mut cursor).expect("可读"), frames[1]);
        assert!(matches!(read(&mut cursor), Err(IpcError::Eof)));
    }
}
