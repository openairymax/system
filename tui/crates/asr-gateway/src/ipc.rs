// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// asr-gateway 的本地 socket 协议
//
// boot-tui 的 VoiceSource 只经这条 socket 与网关通信，自己绝不打开录音
// 设备（接口册 §3.2、§10.2）。framing 与输入入口一致：单连接单会话、
// JSON Lines、单条 ≤ 64 KiB。seq 单调递增；重复序号一律拒绝，不做重放——
// 语音采集不可幂等重放，把同一条 `start` 再执行一次会录出第二段音频，
// 宁可报错也不制造用户没说过的话。
//
// 会话只做状态迁移，不碰设备：`stop` 只回答「该去识别了」（[`Step::Capture`]），
// 识别结果由网关采集并识别完成后经 [`Session::submit`] 交回。因此整套状态机
// 在没有声卡、没有模型的机器上也可完整测试，不需要桩实现。
//
// 失败只报 §14.13 的三个显式状态：`unavailable` / `busy` / `empty`；
// 协议违规（未知消息、字段形状不符、序号回退）走 [`IpcError`]，不冒充降级。
//
// SSoT: 0.0.5 方案 §14.7、§14.13；30-interfaces/11-input-ingress.md
// §3.2、§3.3、§7、§10.2；本 crate 的 SPEC.md。
// ===========================================================================

//! 语音网关的帧、编解码与会话状态机。

use core::fmt;
use std::io::{BufRead, Read, Write};

use serde::{Deserialize, Serialize};

use crate::asr::{AudioRef, Availability, Transcript};
use crate::AsrError;

/// 网关监听的 socket 路径；与 IME 入口同目录、同命名规则。
pub const SOCKET: &str = "/run/airy/asr.sock";

/// 单条消息的字节上限；不含行尾换行。
pub const MAX_FRAME: usize = 64 * 1024;

/// 客户端请求。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Request {
    /// 建立或确认会话。
    Open,
    /// 开始一次采集。
    Start,
    /// 结束采集并请求识别。
    Stop,
    /// 放弃当前采集，不产生结果。
    Cancel,
    /// 关闭会话。
    Close,
}

impl Request {
    /// 线格式里的稳定名字。
    pub fn name(self) -> &'static str {
        match self {
            Self::Open => "open",
            Self::Start => "start",
            Self::Stop => "stop",
            Self::Cancel => "cancel",
            Self::Close => "close",
        }
    }
}

/// 网关应答。
#[derive(Clone, Debug, PartialEq)]
pub enum Reply {
    /// 会话就绪或采集已开始。
    Ready,
    /// 识别结果；进入可编辑缓冲区，不直接执行。
    Result(Transcript),
    /// 显式失败；`status` 即状态栏取值。
    Fail(Failure),
    /// 会话已关闭。
    Closed,
}

/// 失败应答；`status` 只允许 `unavailable` / `busy` / `empty`。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Failure {
    status: Availability,
    detail: String,
}

impl Failure {
    /// 由网关错误生成；状态与文案都来自同一处映射。
    pub fn of(error: AsrError) -> Self {
        Self {
            status: error.availability(),
            detail: error.to_string(),
        }
    }

    /// 由线上字段生成；`ready` 不是失败状态，与未知取值一样拒绝。
    fn new(status: Availability, detail: String) -> Result<Self, IpcError> {
        if status == Availability::Ready {
            return Err(IpcError::Field);
        }
        Ok(Self { status, detail })
    }

    /// 状态栏取值。
    pub fn status(&self) -> Availability {
        self.status
    }

    /// 可执行说明。
    pub fn detail(&self) -> &str {
        &self.detail
    }
}

/// 一条客户端请求帧。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Frame {
    session: String,
    seq: u64,
    request: Request,
}

impl Frame {
    /// 逐字段构造；空会话标识在编码时拒绝。
    pub fn new(session: impl Into<String>, seq: u64, request: Request) -> Self {
        Self {
            session: session.into(),
            seq,
            request,
        }
    }

    /// 会话标识。
    pub fn session(&self) -> &str {
        &self.session
    }

    /// 单调序号；同一会话内严格递增。
    pub fn seq(&self) -> u64 {
        self.seq
    }

    /// 请求类别。
    pub fn request(&self) -> Request {
        self.request
    }

    /// 解析一行；超长、空会话与未知消息都拒绝。
    pub fn parse(line: &str) -> Result<Self, IpcError> {
        let text = line.trim_end();
        if text.len() > MAX_FRAME {
            return Err(IpcError::Long);
        }
        let wire: Req = serde_json::from_str(text).map_err(|_| IpcError::Json)?;
        let frame = wire.into_frame();
        frame.check()?;
        Ok(frame)
    }

    /// 序列化为单行 JSON。
    pub fn encode(&self) -> Result<String, IpcError> {
        self.check()?;
        let line = serde_json::to_string(&self.wire()).map_err(|_| IpcError::Json)?;
        if line.len() > MAX_FRAME {
            Err(IpcError::Long)
        } else {
            Ok(line)
        }
    }

    /// 空会话标识任一方向都不成立。
    fn check(&self) -> Result<(), IpcError> {
        if self.session.is_empty() {
            Err(IpcError::Field)
        } else {
            Ok(())
        }
    }

    /// 转成线格式。
    fn wire(&self) -> Req {
        let session = self.session.clone();
        let seq = self.seq;
        match self.request {
            Request::Open => Req::Open { session, seq },
            Request::Start => Req::Start { session, seq },
            Request::Stop => Req::Stop { session, seq },
            Request::Cancel => Req::Cancel { session, seq },
            Request::Close => Req::Close { session, seq },
        }
    }
}

/// 一条网关应答帧。
#[derive(Clone, Debug, PartialEq)]
pub struct Answer {
    session: String,
    seq: u64,
    reply: Reply,
}

impl Answer {
    /// 应答的 `seq` 必须回显它所回答的请求序号。
    pub fn new(session: impl Into<String>, seq: u64, reply: Reply) -> Self {
        Self {
            session: session.into(),
            seq,
            reply,
        }
    }

    /// 会话标识。
    pub fn session(&self) -> &str {
        &self.session
    }

    /// 被回答的请求序号。
    pub fn seq(&self) -> u64 {
        self.seq
    }

    /// 应答内容。
    pub fn reply(&self) -> &Reply {
        &self.reply
    }

    /// 解析一行应答。
    pub fn parse(line: &str) -> Result<Self, IpcError> {
        let text = line.trim_end();
        if text.len() > MAX_FRAME {
            return Err(IpcError::Long);
        }
        let wire: Rep = serde_json::from_str(text).map_err(|_| IpcError::Json)?;
        let answer = wire.into_answer()?;
        if answer.session.is_empty() {
            return Err(IpcError::Field);
        }
        Ok(answer)
    }

    /// 序列化为单行 JSON。
    pub fn encode(&self) -> Result<String, IpcError> {
        if self.session.is_empty() {
            return Err(IpcError::Field);
        }
        let line = serde_json::to_string(&self.wire()).map_err(|_| IpcError::Json)?;
        if line.len() > MAX_FRAME {
            Err(IpcError::Long)
        } else {
            Ok(line)
        }
    }

    /// 转成线格式。
    fn wire(&self) -> Rep {
        let session = self.session.clone();
        let seq = self.seq;
        match &self.reply {
            Reply::Ready => Rep::Ready { session, seq },
            Reply::Result(item) => Rep::Result {
                session,
                seq,
                text: item.text().to_string(),
                confidence: item.confidence(),
                audio: AudioWire::of(item.audio()),
            },
            Reply::Fail(fail) => Rep::Fail {
                session,
                seq,
                status: fail.status().status().to_string(),
                detail: fail.detail().to_string(),
            },
            Reply::Closed => Rep::Closed { session, seq },
        }
    }
}

/// 读一行请求；EOF 返回 [`IpcError::Eof`]。
pub fn read_frame<R: BufRead>(source: &mut R) -> Result<Frame, IpcError> {
    let mut bytes = Vec::new();
    Read::take(source, MAX_FRAME as u64 + 2)
        .read_until(b'\n', &mut bytes)
        .map_err(IpcError::Io)?;
    match bytes.len() {
        0 => Err(IpcError::Eof),
        n if n > MAX_FRAME + 1 => Err(IpcError::Long),
        _ => Frame::parse(core::str::from_utf8(&bytes).map_err(|_| IpcError::Json)?),
    }
}

/// 写一行应答并立即刷新。
pub fn write_answer<W: Write>(sink: &mut W, answer: &Answer) -> Result<(), IpcError> {
    let mut line = answer.encode()?;
    line.push('\n');
    sink.write_all(line.as_bytes()).map_err(IpcError::Io)?;
    sink.flush().map_err(IpcError::Io)
}

/// 协议错误；只有帧级错误会终止连接。
#[derive(Debug)]
pub enum IpcError {
    /// 不是合法 JSON、缺字段或未知消息。
    Json,
    /// 消息超过 64 KiB。
    Long,
    /// 字段存在但取值不合法；空会话标识也归此类。
    Field,
    /// 对端已关闭。
    Eof,
    /// 会话标识不匹配。
    Foreign,
    /// 序号未递增，疑似重复投递。
    Stale,
    /// 请求与当前阶段不符。
    Phase,
    /// 会话已关闭。
    Closed,
    /// 底层读写失败。
    Io(std::io::Error),
}

impl fmt::Display for IpcError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::Json => "消息不是合法 JSON",
            Self::Long => "消息超过 64 KiB",
            Self::Field => "消息字段取值非法",
            Self::Eof => "对端已关闭",
            Self::Foreign => "会话标识不匹配",
            Self::Stale => "序号未递增",
            Self::Phase => "请求与当前阶段不符",
            Self::Closed => "会话已关闭",
            Self::Io(error) => return write!(f, "读写失败: {error}"),
        };
        f.write_str(text)
    }
}

/// 会话阶段。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Phase {
    /// 可开始采集。
    Idle,
    /// 采集中；除 `stop` / `cancel` / `close` 外的请求不再受理。
    Recording,
    /// 已关闭；任何请求都拒绝。
    Closed,
}

/// 一帧请求的处置。
#[derive(Clone, Debug, PartialEq)]
pub enum Step {
    /// 需要采集并识别；完成后用 [`Session::submit`] 交回结果。
    Capture,
    /// 无需采集，直接回送这条应答。
    Reply(Reply),
}

/// 一个语音会话；模型可用性在建立时固定。
#[derive(Debug)]
pub struct Session {
    id: String,
    last: u64,
    phase: Phase,
    gate: Availability,
    pending: Option<u64>,
}

impl Session {
    /// 建立会话；空标识或不可用模型不会让会话进入可采集状态。
    pub fn open(id: impl Into<String>, gate: Availability) -> Result<Self, IpcError> {
        let id = id.into();
        if id.is_empty() {
            return Err(IpcError::Field);
        }
        Ok(Self {
            id,
            last: 0,
            phase: Phase::Idle,
            gate,
            pending: None,
        })
    }

    /// 会话标识。
    pub fn id(&self) -> &str {
        &self.id
    }

    /// 当前阶段。
    pub fn phase(&self) -> Phase {
        self.phase
    }

    /// 应用一条请求；序号在成功处理后才前移，失败可原样重试。
    pub fn apply(&mut self, frame: &Frame) -> Result<Step, IpcError> {
        self.guard(frame)?;
        let step = match frame.request {
            Request::Open => Step::Reply(Reply::Ready),
            Request::Start => Step::Reply(self.start()),
            Request::Stop => self.stop(frame.seq)?,
            Request::Cancel => Step::Reply(self.cancel()),
            Request::Close => Step::Reply(self.close()),
        };
        self.last = frame.seq;
        Ok(step)
    }

    /// 交回采集与识别的结果；`seq` 必须是触发采集的那条 `stop`。
    pub fn submit(
        &mut self,
        seq: u64,
        heard: Result<Transcript, AsrError>,
    ) -> Result<Reply, IpcError> {
        if self.phase != Phase::Recording || self.pending != Some(seq) {
            return Err(IpcError::Phase);
        }
        self.phase = Phase::Idle;
        self.pending = None;
        Ok(match heard {
            Ok(item) => Reply::Result(item),
            Err(error) => Reply::Fail(Failure::of(error)),
        })
    }

    /// 前置条件：会话未关闭、帧属于本会话、序号严格递增。
    fn guard(&self, frame: &Frame) -> Result<(), IpcError> {
        if self.phase == Phase::Closed {
            return Err(IpcError::Closed);
        }
        if frame.session != self.id {
            return Err(IpcError::Foreign);
        }
        if frame.seq <= self.last {
            Err(IpcError::Stale)
        } else {
            Ok(())
        }
    }

    /// 开始采集；模型不可用与设备占用都回显式失败。
    fn start(&mut self) -> Reply {
        if !self.gate.usable() {
            return Reply::Fail(Failure::of(AsrError::NoModel));
        }
        if self.phase == Phase::Recording {
            return Reply::Fail(Failure::of(AsrError::Busy));
        }
        self.phase = Phase::Recording;
        Reply::Ready
    }

    /// 结束采集；把 `seq` 记成待交回的识别请求。
    fn stop(&mut self, seq: u64) -> Result<Step, IpcError> {
        if self.phase != Phase::Recording {
            return Err(IpcError::Phase);
        }
        self.pending = Some(seq);
        Ok(Step::Capture)
    }

    /// 放弃采集；非采集阶段视为空操作。
    fn cancel(&mut self) -> Reply {
        if self.phase == Phase::Recording {
            self.phase = Phase::Idle;
            self.pending = None;
        }
        Reply::Ready
    }

    /// 关闭会话；之后的请求一律拒绝。
    fn close(&mut self) -> Reply {
        self.phase = Phase::Closed;
        self.pending = None;
        Reply::Closed
    }
}

/// 请求线格式；`msg` 是判别名。
#[derive(Deserialize, Serialize)]
#[serde(tag = "msg", rename_all = "lowercase")]
enum Req {
    /// 建立会话。
    Open { session: String, seq: u64 },
    /// 开始采集。
    Start { session: String, seq: u64 },
    /// 结束采集。
    Stop { session: String, seq: u64 },
    /// 放弃采集。
    Cancel { session: String, seq: u64 },
    /// 关闭会话。
    Close { session: String, seq: u64 },
}

impl Req {
    /// 线格式到帧；未知字段由 serde 忽略，未知判别名已在上一步拒绝。
    fn into_frame(self) -> Frame {
        let (session, seq, request) = match self {
            Self::Open { session, seq } => (session, seq, Request::Open),
            Self::Start { session, seq } => (session, seq, Request::Start),
            Self::Stop { session, seq } => (session, seq, Request::Stop),
            Self::Cancel { session, seq } => (session, seq, Request::Cancel),
            Self::Close { session, seq } => (session, seq, Request::Close),
        };
        Frame::new(session, seq, request)
    }
}

/// 应答线格式；`msg` 是判别名。
#[derive(Deserialize, Serialize)]
#[serde(tag = "msg", rename_all = "lowercase")]
enum Rep {
    /// 就绪。
    Ready { session: String, seq: u64 },
    /// 识别结果。
    Result {
        session: String,
        seq: u64,
        text: String,
        confidence: f32,
        audio: AudioWire,
    },
    /// 显式失败。
    Fail {
        session: String,
        seq: u64,
        status: String,
        detail: String,
    },
    /// 已关闭。
    Closed { session: String, seq: u64 },
}

impl Rep {
    /// 线格式到应答；结果字段重新走一遍构造校验，不信任对端。
    fn into_answer(self) -> Result<Answer, IpcError> {
        let (session, seq, reply) = match self {
            Self::Ready { session, seq } => (session, seq, Reply::Ready),
            Self::Result {
                session,
                seq,
                text,
                confidence,
                audio,
            } => (session, seq, Reply::Result(item(text, confidence, audio)?)),
            Self::Fail {
                session,
                seq,
                status,
                detail,
            } => {
                let status = Availability::from_status(&status).ok_or(IpcError::Field)?;
                let fail = Failure::new(status, detail)?;
                (session, seq, Reply::Fail(fail))
            }
            Self::Closed { session, seq } => (session, seq, Reply::Closed),
        };
        Ok(Answer::new(session, seq, reply))
    }
}

/// 音频引用的线格式；与接口册 §3.2 的三个字段一一对应。
#[derive(Deserialize, Serialize)]
struct AudioWire {
    path: String,
    digest: String,
    duration_ms: u32,
}

impl AudioWire {
    /// 由已校验的引用生成。
    fn of(audio: &AudioRef) -> Self {
        Self {
            path: audio.path().to_string(),
            digest: audio.digest().to_string(),
            duration_ms: audio.duration_ms(),
        }
    }
}

/// 重建识别结果；对端字段不合法即判协议错误。
fn item(text: String, confidence: f32, audio: AudioWire) -> Result<Transcript, IpcError> {
    let audio =
        AudioRef::new(audio.path, audio.digest, audio.duration_ms).map_err(|_| IpcError::Field)?;
    Transcript::new(text, confidence, audio).map_err(|_| IpcError::Field)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    /// 一条合法音频引用。
    fn audio() -> AudioRef {
        AudioRef::new(
            "/var/lib/airy/asr/1.wav",
            format!("sha256:{}", "a".repeat(64)),
            3120,
        )
        .expect("可构造")
    }

    /// 一条合法识别结果。
    fn heard() -> Transcript {
        Transcript::new("打开终端", 0.9, audio()).expect("可构造")
    }

    /// 以 `s-1` 会话、`seq=1` 构造请求。
    fn frame(request: Request) -> Frame {
        Frame::new("s-1", 1, request)
    }

    /// 以 `s-1` 会话、`seq=2` 构造应答。
    fn answer(reply: Reply) -> Answer {
        Answer::new("s-1", 2, reply)
    }

    #[test]
    fn every_request_roundtrips() {
        for request in [
            Request::Open,
            Request::Start,
            Request::Stop,
            Request::Cancel,
            Request::Close,
        ] {
            let frame = frame(request);
            let line = frame.encode().expect("可编码");
            assert_eq!(Frame::parse(&line).expect("可解析"), frame);
        }
    }

    #[test]
    fn every_reply_roundtrips() {
        let replies = [
            Reply::Ready,
            Reply::Result(heard()),
            Reply::Fail(Failure::of(AsrError::Busy)),
            Reply::Closed,
        ];
        for reply in replies {
            let answer = answer(reply);
            let line = answer.encode().expect("可编码");
            assert_eq!(Answer::parse(&line).expect("可解析"), answer);
        }
    }

    #[test]
    fn result_frame_names_its_fields() {
        let line = answer(Reply::Result(heard())).encode().expect("可编码");
        assert!(line.contains("\"msg\":\"result\""));
        assert!(line.contains("\"duration_ms\":3120"));
    }

    #[test]
    fn unknown_message_is_rejected() {
        let line = r#"{"session":"s-1","seq":1,"msg":"paste"}"#;
        assert!(matches!(Frame::parse(line), Err(IpcError::Json)));
        assert!(matches!(Answer::parse(line), Err(IpcError::Json)));
    }

    #[test]
    fn missing_fields_are_rejected() {
        let line = r#"{"session":"s-1","msg":"start"}"#;
        assert!(matches!(Frame::parse(line), Err(IpcError::Json)));
        let line = r#"{"session":"s-1","seq":1,"msg":"fail","status":"busy"}"#;
        assert!(matches!(Answer::parse(line), Err(IpcError::Json)));
    }

    #[test]
    fn empty_session_is_rejected_both_ways() {
        let line = r#"{"session":"","seq":1,"msg":"open"}"#;
        assert!(matches!(Frame::parse(line), Err(IpcError::Field)));
        assert!(matches!(
            Frame::new("", 1, Request::Open).encode(),
            Err(IpcError::Field)
        ));
        assert!(matches!(
            Answer::new("", 2, Reply::Ready).encode(),
            Err(IpcError::Field)
        ));
    }

    #[test]
    fn overlong_frame_is_rejected() {
        let line = "x".repeat(MAX_FRAME + 1);
        assert!(matches!(Frame::parse(&line), Err(IpcError::Long)));
        assert!(matches!(Answer::parse(&line), Err(IpcError::Long)));
    }

    #[test]
    fn stream_reads_frames_and_stops_at_eof() {
        let frames = [
            Frame::new("s-1", 1, Request::Open),
            Frame::new("s-1", 2, Request::Start),
        ];
        let mut buffer = Vec::new();
        for frame in &frames {
            buffer.extend_from_slice(frame.encode().expect("可编码").as_bytes());
            buffer.push(b'\n');
        }
        let mut cursor = Cursor::new(buffer);
        assert_eq!(read_frame(&mut cursor).expect("可读"), frames[0]);
        assert_eq!(read_frame(&mut cursor).expect("可读"), frames[1]);
        assert!(matches!(read_frame(&mut cursor), Err(IpcError::Eof)));
    }

    #[test]
    fn answers_are_written_as_single_lines() {
        let mut buffer = Vec::new();
        write_answer(&mut buffer, &answer(Reply::Ready)).expect("可写");
        let text = String::from_utf8(buffer).expect("可读");
        assert_eq!(
            text.trim_end(),
            answer(Reply::Ready).encode().expect("可编码")
        );
        assert_eq!(text.matches('\n').count(), 1, "一行一条");
    }

    #[test]
    fn unavailable_model_fails_start() {
        let mut session = Session::open("s-1", Availability::Unavailable).expect("可建立");
        let step = session.apply(&frame(Request::Start)).expect("可处理");
        let Step::Reply(Reply::Fail(fail)) = step else {
            panic!("应回显式失败: {step:?}");
        };
        assert_eq!(fail.status(), Availability::Unavailable);
        assert_eq!(session.phase(), Phase::Idle);
    }

    #[test]
    fn second_start_reports_busy() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        assert_eq!(
            session.apply(&frame(Request::Start)).expect("可处理"),
            Step::Reply(Reply::Ready)
        );
        let step = session
            .apply(&Frame::new("s-1", 2, Request::Start))
            .expect("可处理");
        let Step::Reply(Reply::Fail(fail)) = step else {
            panic!("应回显式失败: {step:?}");
        };
        assert_eq!(fail.status(), Availability::Busy);
    }

    #[test]
    fn stop_without_start_is_phase_error() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        assert!(matches!(
            session.apply(&frame(Request::Stop)),
            Err(IpcError::Phase)
        ));
    }

    #[test]
    fn stop_then_submit_yields_result() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        session.apply(&frame(Request::Start)).expect("可开始");
        let stop = Frame::new("s-1", 2, Request::Stop);
        assert_eq!(
            session.apply(&stop).expect("可处理"),
            Step::Capture,
            "stop 只要求采集"
        );
        let reply = session.submit(2, Ok(heard())).expect("可交回");
        assert_eq!(reply, Reply::Result(heard()));
        assert_eq!(session.phase(), Phase::Idle);
    }

    #[test]
    fn empty_result_reports_empty() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        session.apply(&frame(Request::Start)).expect("可开始");
        session
            .apply(&Frame::new("s-1", 2, Request::Stop))
            .expect("可停止");
        let reply = session.submit(2, Err(AsrError::Empty)).expect("可交回");
        let Reply::Fail(fail) = reply else {
            panic!("应回显式失败: {reply:?}");
        };
        assert_eq!(fail.status(), Availability::Empty);
    }

    #[test]
    fn submit_without_stop_is_phase_error() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        session.apply(&frame(Request::Start)).expect("可开始");
        assert!(matches!(
            session.submit(1, Ok(heard())),
            Err(IpcError::Phase)
        ));
    }

    #[test]
    fn cancel_returns_to_idle() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        session.apply(&frame(Request::Start)).expect("可开始");
        session
            .apply(&Frame::new("s-1", 2, Request::Cancel))
            .expect("可取消");
        assert_eq!(session.phase(), Phase::Idle);
        assert_eq!(
            session
                .apply(&Frame::new("s-1", 3, Request::Start))
                .expect("可重启"),
            Step::Reply(Reply::Ready)
        );
    }

    #[test]
    fn foreign_session_is_rejected() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        assert!(matches!(
            session.apply(&Frame::new("s-2", 1, Request::Open)),
            Err(IpcError::Foreign)
        ));
    }

    #[test]
    fn stale_seq_is_rejected() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        session.apply(&frame(Request::Open)).expect("可处理");
        assert!(matches!(
            session.apply(&frame(Request::Open)),
            Err(IpcError::Stale)
        ));
    }

    #[test]
    fn closed_session_is_rejected() {
        let mut session = Session::open("s-1", Availability::Ready).expect("可建立");
        assert_eq!(
            session
                .apply(&Frame::new("s-1", 1, Request::Close))
                .expect("可关闭"),
            Step::Reply(Reply::Closed)
        );
        assert!(matches!(
            session.apply(&Frame::new("s-1", 2, Request::Open)),
            Err(IpcError::Closed)
        ));
    }

    #[test]
    fn failure_status_tokens_are_validated() {
        let line = r#"{"session":"s-1","seq":2,"msg":"fail","status":"ready","detail":"x"}"#;
        assert!(matches!(Answer::parse(line), Err(IpcError::Field)));
        let line = r#"{"session":"s-1","seq":2,"msg":"fail","status":"jammed","detail":"x"}"#;
        assert!(matches!(Answer::parse(line), Err(IpcError::Field)));
    }
}
