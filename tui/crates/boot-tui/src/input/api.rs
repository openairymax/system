// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 外接结构化输入（C3）
//
// 传输是 Unix domain socket 上的 JSON Lines：一行一条 airy.intent/1，单连接
// 单会话。身份不取自消息：accept 之后先用 SO_PEERCRED 取 uid/gid/pid，再按
// §4.1 定级——已认证对端是 T1，root 且消息显式声明 T2 才是 T2；非 root 声明
// T2 直接 denied，不降级成 T1 继续执行。
//
// 适配器只做 schema 校验与背压（§10.2）：通过的行变成 Structured 事件，失败
// 的行按 §7 记一条带 trace_id 的拒绝记录，同一会话继续；只有帧损坏或对端关
// 闭才断开。背压按 §3.3：同一连接最多积压 8 条尚未取走的请求，满额后不再读
// socket，对端由内核缓冲感知写入阻塞。契约没有定义应答帧，本模块不回写任何
// 响应，结果引用由执行侧按 trace_id 返回。
//
// 契约只规定传输形态而不给路径；本版统一约定为 `/run/airy/input.sock`，与
// IME 的 `/run/airy/ime.sock` 同目录，便于同一套运行时目录权限管理。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.4、§3.3、§4.1、§7、§10.2。
// ===========================================================================

//! C3 外接结构化输入：Unix socket + JSON Lines。

use std::collections::VecDeque;
use std::io::{ErrorKind, Read};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};

use intent::{Intent, Refusal, SourceKind, TraceId, Trust, SEEN};
use rustix::net::sockopt::socket_peercred;
use rustix::net::UCred;
use serde_json::Value;

use super::event::{
    bad_line, refusal_of, shape, syntax, take_events, Clock, RawBody, RawEvent, Reject,
};
use super::source::InputSource;

/// 意图入口的 socket 路径；契约未命名，本版统一约定在此。
pub const SOCKET: &str = "/run/airy/input.sock";

/// 单条请求的字节上限；超过即视为帧损坏。
pub const MAX_FRAME: usize = 64 * 1024;

/// 单连接未完成请求上限（§3.3）；满额后停止读取，形成背压。
pub const PENDING: usize = 8;

/// 单次读取的字节上限；按键量级的突发远小于此值。
const READ_MAX: usize = 4096;

/// 对端凭据：只从内核取，消息里的身份字段不参与判定。
#[derive(Clone, Copy, Debug)]
struct Peer(UCred);

impl Peer {
    fn of(stream: &UnixStream) -> Result<Self, Refusal> {
        socket_peercred(stream)
            .map(Self)
            .map_err(|_| Refusal::unavailable("peercred"))
    }

    fn uid(self) -> u32 {
        self.0.uid.as_raw()
    }
}

/// 单连接：已认证的对端、未成行的字节与半条请求。
struct Client {
    stream: UnixStream,
    peer: Peer,
    buf: Vec<u8>,
}

/// 一次取行的结果；四种状态决定是否继续读。
enum Pull {
    /// 一条完整的行（已去掉行尾）。
    Line(String),
    /// 暂时没有完整行，稍后再读。
    Idle,
    /// 对端关闭或读取失败。
    Gone,
    /// 帧本身损坏，会话不可继续。
    Bad(Refusal),
}

/// 一次读取的结果；`More` 表示可以继续取行。
enum Step {
    /// 读到了字节，或读调用被信号打断。
    More,
    /// 暂时无数据。
    Idle,
    /// 对端关闭或读取失败。
    Gone,
}

impl Client {
    fn next_line(&mut self) -> Pull {
        loop {
            if let Some(pos) = self.buf.iter().position(|byte| *byte == b'\n') {
                return self.take_line(pos);
            }
            if self.buf.len() > MAX_FRAME {
                return Pull::Bad(Refusal::invalid("frame"));
            }
            match self.read_once() {
                Step::More => continue,
                Step::Idle => return Pull::Idle,
                Step::Gone => return Pull::Gone,
            }
        }
    }

    /// 读一次；被信号打断按可继续处理，其余错误按对端消失处理。
    fn read_once(&mut self) -> Step {
        let mut chunk = [0u8; READ_MAX];
        match self.stream.read(&mut chunk) {
            Ok(0) => Step::Gone,
            Ok(got) => {
                self.buf.extend_from_slice(&chunk[..got]);
                Step::More
            }
            Err(err) if err.kind() == ErrorKind::WouldBlock => Step::Idle,
            Err(err) if err.kind() == ErrorKind::Interrupted => Step::More,
            Err(_) => Step::Gone,
        }
    }

    /// 取走 `[0, pos]` 一行并去掉行尾；非 UTF-8 记作坏帧。
    fn take_line(&mut self, pos: usize) -> Pull {
        let rest = self.buf.split_off(pos + 1);
        let mut line = std::mem::replace(&mut self.buf, rest);
        line.pop();
        if line.last() == Some(&b'\r') {
            line.pop();
        }
        match String::from_utf8(line) {
            Ok(text) => Pull::Line(text),
            Err(_) => Pull::Bad(Refusal::invalid("utf8")),
        }
    }
}

/// C3 输入源：独占一个 Unix socket，逐行产出结构化事件。
pub struct Api {
    listener: Option<UnixListener>,
    path: PathBuf,
    client: Option<Client>,
    pending: VecDeque<RawEvent>,
    rejects: VecDeque<Reject>,
    seen: VecDeque<TraceId>,
    clock: Clock,
}

impl Api {
    /// 在约定路径上打开意图入口。
    pub fn open() -> Result<Self, Refusal> {
        Self::at(Path::new(SOCKET))
    }

    /// 在指定路径上打开意图入口；路径已存在即失败，不顶掉可能的在服实
    /// 例。残留文件由运行时目录（tmpfs）或单元清理。
    pub fn at(path: &Path) -> Result<Self, Refusal> {
        let listener = UnixListener::bind(path).map_err(|_| Refusal::unavailable("api"))?;
        listener
            .set_nonblocking(true)
            .map_err(|_| Refusal::unavailable("api"))?;
        Ok(Self {
            listener: Some(listener),
            path: path.to_path_buf(),
            client: None,
            pending: VecDeque::new(),
            rejects: VecDeque::new(),
            seen: VecDeque::new(),
            clock: Clock::new(),
        })
    }

    /// 取走一条被拒记录；没有则返回 `None`。
    pub fn next_reject(&mut self) -> Option<Reject> {
        self.rejects.pop_front()
    }

    /// 接入至多一个新连接；已有会话时新来者直接关闭（单连接单会话）。
    fn accept_peer(&mut self) {
        loop {
            let Some(listener) = self.listener.as_ref() else {
                return;
            };
            match listener.accept() {
                Ok((stream, _)) => self.welcome(stream),
                Err(err) if err.kind() == ErrorKind::WouldBlock => return,
                Err(_) => return,
            }
        }
    }

    fn welcome(&mut self, stream: UnixStream) {
        if self.client.is_some() || stream.set_nonblocking(true).is_err() {
            return;
        }
        let Ok(peer) = Peer::of(&stream) else {
            return;
        };
        self.client = Some(Client {
            stream,
            peer,
            buf: Vec::new(),
        });
    }

    /// 读入并校验，直到积压达到上限或没有完整行。
    fn fill(&mut self) {
        while self.pending.len() < PENDING {
            let Some(peer) = self.client.as_ref().map(|client| client.peer) else {
                break;
            };
            let pull = match self.client.as_mut() {
                Some(client) => client.next_line(),
                None => break,
            };
            match pull {
                Pull::Line(text) => self.offer(peer, text),
                Pull::Idle => break,
                Pull::Gone => {
                    self.client = None;
                    break;
                }
                Pull::Bad(refusal) => {
                    self.drop_client(refusal);
                    break;
                }
            }
        }
    }

    /// 处理一行：空行忽略，其余按 §2.4 校验后入队或记拒绝。
    fn offer(&mut self, peer: Peer, text: String) {
        if text.is_empty() {
            return;
        }
        match self.admit(peer, &text) {
            Ok(Some(event)) => self.pending.push_back(event),
            Ok(None) => {}
            Err(reject) => self.keep(reject),
        }
    }

    /// 校验一行并产出事件；`Ok(None)` 表示同一 `trace_id` 的重试。
    fn admit(&mut self, peer: Peer, text: &str) -> Result<Option<RawEvent>, Reject> {
        let value = match serde_json::from_str::<Value>(text) {
            Ok(value) => value,
            Err(err) => return Err(self.bad(None, syntax(&err))),
        };
        let intent = match serde_json::from_value::<Intent>(value.clone()) {
            Ok(intent) => intent,
            Err(err) => return Err(self.bad(Some(&value), shape(&err))),
        };
        if intent.source.kind != SourceKind::Api {
            return Err(self.reject(intent.trace_id, Refusal::invalid("source.kind")));
        }
        let level = match level_of(peer.uid(), intent.trust.level) {
            Ok(level) => level,
            Err(refusal) => return Err(self.reject(intent.trace_id, refusal)),
        };
        if let Err(err) = intent.check() {
            return Err(self.reject(intent.trace_id, refusal_of(err, SourceKind::Api)));
        }
        if !self.mark(intent.trace_id) {
            return Ok(None);
        }
        Ok(Some(RawEvent {
            trace_id: intent.trace_id,
            source: SourceKind::Api,
            trust: level,
            at: self.clock.now(),
            body: RawBody::Structured(value),
        }))
    }

    /// 记录一次受理；同一窗口内重复的 `trace_id` 返回 false。
    fn mark(&mut self, trace: TraceId) -> bool {
        if self.seen.contains(&trace) {
            return false;
        }
        if self.seen.len() == SEEN {
            self.seen.pop_front();
        }
        self.seen.push_back(trace);
        true
    }

    /// 坏行（语法或字段）：能从原文取到 trace_id 就复用，否则新配一个。
    fn bad(&mut self, value: Option<&Value>, refusal: Refusal) -> Reject {
        bad_line(&mut self.clock, value, refusal)
    }

    fn reject(&self, trace: TraceId, refusal: Refusal) -> Reject {
        Reject::new(trace, refusal)
    }

    /// 拒绝记录同样有界：最早的记录让位给新记录，不排队到无限。
    fn keep(&mut self, reject: Reject) {
        if self.rejects.len() == PENDING {
            self.rejects.pop_front();
        }
        self.rejects.push_back(reject);
    }

    /// 帧损坏即断开：同一会话不再继续读。
    fn drop_client(&mut self, refusal: Refusal) {
        self.client = None;
        let trace = self.clock.next();
        self.keep(Reject::new(trace, refusal));
    }

    fn shutdown(&mut self) {
        self.client = None;
        self.pending.clear();
        if self.listener.take().is_some() {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

impl InputSource for Api {
    fn kind(&self) -> SourceKind {
        SourceKind::Api
    }

    fn drain(&mut self, max: usize) -> Result<Vec<RawEvent>, Refusal> {
        self.accept_peer();
        self.fill();
        Ok(take_events(&mut self.pending, max))
    }

    fn close(&mut self) -> Result<(), Refusal> {
        self.shutdown();
        Ok(())
    }
}

impl Drop for Api {
    fn drop(&mut self) {
        self.shutdown();
    }
}

/// 内核凭据与显式声明共同定级（§3.3）：非 root 不得取 T2。
fn level_of(uid: u32, declared: Trust) -> Result<Trust, Refusal> {
    if declared == Trust::T2 && uid != 0 {
        return Err(Refusal::denied(Trust::T2.label()));
    }
    Ok(declared)
}

#[cfg(test)]
mod tests {
    use std::io::Write;
    use std::path::PathBuf;
    use std::thread;
    use std::time::Duration;

    use rustix::process::getuid;

    use super::*;

    const TRACE: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";

    fn socket(tag: &str) -> PathBuf {
        let mut path = std::env::temp_dir();
        path.push(format!("airy-api-{}-{tag}.sock", std::process::id()));
        let _ = std::fs::remove_file(&path);
        path
    }

    fn line(trace: &str, level: &str, kind: &str) -> String {
        format!(
            r#"{{"schema":"airy.intent/1","trace_id":"{trace}","ts":"2026-11-17T10:02:11+08:00","source":{{"kind":"{kind}","device":"sock"}},"trust":{{"level":"{level}","principal":"uid:1000"}},"session":{{"mode":"tui","id":"s-1"}},"payload":{{"kind":"text","text":"查看磁盘","lang":"zh-CN"}}}}"#
        )
    }

    fn connect(path: &Path) -> UnixStream {
        UnixStream::connect(path).expect("监听已就绪")
    }

    fn send(path: &Path, text: &str) -> UnixStream {
        let mut client = connect(path);
        client.write_all(text.as_bytes()).expect("写入");
        client.write_all(b"\n").expect("换行");
        client
    }

    /// 反复取事件与拒绝记录，直到数量满足或超时。
    fn collect(
        api: &mut Api,
        events_want: usize,
        rejects_want: usize,
    ) -> (Vec<RawEvent>, Vec<Reject>) {
        let mut events = Vec::new();
        let mut rejects = Vec::new();
        for _ in 0..400 {
            events.extend(api.drain(PENDING).expect("读取不得失败"));
            while let Some(reject) = api.next_reject() {
                rejects.push(reject);
            }
            if events.len() >= events_want && rejects.len() >= rejects_want {
                return (events, rejects);
            }
            thread::sleep(Duration::from_millis(1));
        }
        (events, rejects)
    }

    #[test]
    fn a_t1_line_becomes_a_structured_event() {
        let path = socket("t1");
        let mut api = Api::at(&path).expect("创建入口");
        let _client = send(&path, &line(TRACE, "T1", "api"));
        let (events, rejects) = collect(&mut api, 1, 0);
        assert!(rejects.is_empty(), "合法请求不得被拒");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].source, SourceKind::Api);
        assert_eq!(events[0].trust, Trust::T1);
        assert_eq!(events[0].trace_id.to_hex(), TRACE);
        match &events[0].body {
            RawBody::Structured(value) => {
                assert_eq!(value["payload"]["text"], "查看磁盘");
            }
            _ => panic!("API 源只产结构化载荷"),
        }
    }

    #[test]
    fn t2_needs_root_and_an_explicit_level() {
        assert_eq!(level_of(0, Trust::T2), Ok(Trust::T2));
        assert_eq!(level_of(1000, Trust::T1), Ok(Trust::T1));
        let denied = level_of(1000, Trust::T2).expect_err("非 root 不得声明 T2");
        assert_eq!(denied.status(), "denied");
        assert_eq!(denied.detail(), "T2");
    }

    #[test]
    fn a_non_root_t2_line_is_rejected() {
        if getuid().as_raw() == 0 {
            return;
        }
        let path = socket("t2");
        let mut api = Api::at(&path).expect("创建入口");
        let _client = send(&path, &line(TRACE, "T2", "api"));
        let (events, rejects) = collect(&mut api, 0, 1);
        assert!(events.is_empty(), "被拒的请求不得产出事件");
        assert_eq!(rejects[0].refusal().status(), "denied");
        assert_eq!(rejects[0].trace().to_hex(), TRACE);
    }

    #[test]
    fn a_bad_line_is_reported_and_the_session_survives() {
        let path = socket("bad");
        let mut api = Api::at(&path).expect("创建入口");
        let mut client = connect(&path);
        client.write_all(b"not json\n").expect("写入");
        client
            .write_all(format!("{}\n", line(TRACE, "T1", "api")).as_bytes())
            .expect("写入");
        let (events, rejects) = collect(&mut api, 1, 1);
        assert_eq!(rejects.len(), 1);
        assert_eq!(rejects[0].refusal().status(), "invalid");
        assert_eq!(rejects[0].trace().to_hex().len(), TraceId::HEX_LEN);
        assert_eq!(events.len(), 1, "坏行不得终止会话");
    }

    #[test]
    fn a_partial_line_waits_for_the_rest() {
        let path = socket("part");
        let mut api = Api::at(&path).expect("创建入口");
        let mut client = connect(&path);
        let text = line(TRACE, "T1", "api");
        let (head, tail) = text.split_at(text.len() / 2);
        client.write_all(head.as_bytes()).expect("写入前半");
        thread::sleep(Duration::from_millis(10));
        assert!(
            api.drain(PENDING).expect("读取不得失败").is_empty(),
            "半条请求不得提前成行"
        );
        client
            .write_all(format!("{tail}\n").as_bytes())
            .expect("写入后半");
        let (events, _) = collect(&mut api, 1, 0);
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn two_lines_in_one_write_keep_their_order() {
        let path = socket("two");
        let mut api = Api::at(&path).expect("创建入口");
        let first = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";
        let second = "0a1b2c3d4e5f60718293a4b5c6d7e8f9";
        let body = format!(
            "{}\n{}\n",
            line(first, "T1", "api"),
            line(second, "T1", "api")
        );
        let mut client = connect(&path);
        client.write_all(body.as_bytes()).expect("写入");
        let (events, _) = collect(&mut api, 2, 0);
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].trace_id.to_hex(), first);
        assert_eq!(events[1].trace_id.to_hex(), second);
    }

    #[test]
    fn a_retried_trace_is_not_admitted_twice() {
        let path = socket("retry");
        let mut api = Api::at(&path).expect("创建入口");
        let body = format!(
            "{}\n{}\n",
            line(TRACE, "T1", "api"),
            line(TRACE, "T1", "api")
        );
        let mut client = connect(&path);
        client.write_all(body.as_bytes()).expect("写入");
        let (mut events, rejects) = collect(&mut api, 1, 0);
        for _ in 0..20 {
            events.extend(api.drain(PENDING).expect("读取不得失败"));
            thread::sleep(Duration::from_millis(1));
        }
        assert_eq!(events.len(), 1, "同一 trace_id 不得重复产出");
        assert!(rejects.is_empty(), "重试不是错误");
    }

    #[test]
    fn the_backlog_is_capped_at_eight() {
        let path = socket("cap");
        let mut api = Api::at(&path).expect("创建入口");
        let mut body = String::new();
        for seq in 0..12u8 {
            let trace = format!("{:032x}", seq + 1);
            body.push_str(&line(&trace, "T1", "api"));
            body.push('\n');
        }
        let mut client = connect(&path);
        client.write_all(body.as_bytes()).expect("写入");
        thread::sleep(Duration::from_millis(20));
        let first = api.drain(64).expect("读取不得失败");
        assert_eq!(first.len(), PENDING, "一次不得取走超过积压上限的请求");
        let (rest, _) = collect(&mut api, 12 - PENDING, 0);
        assert_eq!(first.len() + rest.len(), 12, "余下请求随后逐条送达");
    }

    #[test]
    fn another_channel_cannot_enter_through_the_socket() {
        let path = socket("kind");
        let mut api = Api::at(&path).expect("创建入口");
        let _client = send(&path, &line(TRACE, "T0", "keyboard"));
        let (events, rejects) = collect(&mut api, 0, 1);
        assert!(events.is_empty(), "键盘意图不得从 API 口进入");
        assert_eq!(rejects[0].refusal().status(), "invalid");
        assert_eq!(rejects[0].refusal().detail(), "source.kind");
    }

    #[test]
    fn a_second_connection_is_closed() {
        let path = socket("single");
        let mut api = Api::at(&path).expect("创建入口");
        let _first = send(&path, &line(TRACE, "T1", "api"));
        let (events, _) = collect(&mut api, 1, 0);
        assert_eq!(events.len(), 1);
        let other = "0a1b2c3d4e5f60718293a4b5c6d7e8f9";
        let mut second = connect(&path);
        let _ = second.write_all(format!("{}\n", line(other, "T1", "api")).as_bytes());
        thread::sleep(Duration::from_millis(20));
        assert!(
            api.drain(PENDING).expect("读取不得失败").is_empty(),
            "单连接单会话：第二个连接不得被服务"
        );
    }

    #[test]
    fn close_removes_the_socket_and_is_repeatable() {
        let path = socket("close");
        let mut api = Api::at(&path).expect("创建入口");
        assert!(path.exists());
        api.close().expect("首次关闭");
        assert!(!path.exists(), "关闭后不得留下 socket 文件");
        api.close().expect("重复关闭不报错");
        assert!(api.drain(PENDING).expect("关闭后仍可调用").is_empty());
    }
}
