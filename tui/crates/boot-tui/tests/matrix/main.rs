// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// V-01 四通道端到端矩阵
//
// 契约 §9 的 V-1 要求键盘（中文）、语音、API、批处理各有可判定的成功路径，
// V-2 要求每类失败至少一条。本文件按通道成对组织：每对一条成功、一条失败，
// 断言一律落在适配器公开接口上，不在测试里另造输入协议或结果文案。
//
// 键盘的成功路径止于输入法提交的文本：可打印 ASCII 的字节与键名一一对应
// （`key_byte` 的 US 布局口径），同一组字节既送进按键解码器，也作为全拼送进
// 输入法会话，中间没有第二份转译表。语音的成功路径止于识别结果，失败路径
// 断言「无模型即 unavailable」而不是键盘兜底。API 与批处理直接驱动真实
// 适配器：Unix socket 与逐行读取器，失败记录沿用适配器产出的 `Reject`。
//
// SSoT: 30-interfaces/11-input-ingress.md §3、§4.1、§7、§9 V-1/V-2。
// ===========================================================================

//! 四通道成功与失败路径的端到端矩阵。

use std::io::{Cursor, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use asr_gateway::{
    AsrError, AudioRef, Availability, Frame as VoiceFrame, IpcError, Phase, Reply, Request,
    Session as Voice, Step as VoiceStep, Transcript,
};
use boot_ime::{
    build, Dictionary, Effect, Entry, Frame as ImeFrame, Msg, SessError, Session as Ime,
};
use boot_tui::input::{
    key_byte, Api, Batch, Decoder, InputSource, KeyAction, RawBody, RawEvent, Reject, SourceKind,
    TraceId, Trust,
};

const A: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";
const B: &str = "0a1b2c3d4e5f60718293a4b5c6d7e8f9";

/// C1 成功：终端字节解成按键，同一组字母由输入法提交成中文。
#[test]
fn keyboard_bytes_reach_chinese_text() {
    let pinyin = "zhong";
    let keys = decoded(pinyin);
    assert_eq!(keys.len(), pinyin.len(), "每个字母都要定键");
    assert!(
        keys.iter().all(|key| key.action == KeyAction::Press),
        "普通字节只产生按下动作"
    );

    let dict = dictionary();
    let mut ime = Ime::open(&dict, "s-1").expect("可开会话");
    assert_eq!(type_pinyin(&mut ime, pinyin), "中");
}

/// C1 失败：不构成键的字节被丢弃，非字母键被输入法拒绝，二者都不产文本。
#[test]
fn non_letters_never_become_text() {
    for byte in [0x80u8, 0xc3, 0xff] {
        assert!(key_byte(byte).is_none(), "{byte:#04x} 不得定键");
    }
    let dict = dictionary();
    let mut ime = Ime::open(&dict, "s-1").expect("可开会话");
    let err = ime
        .apply(&ImeFrame::key("s-1", 1, "1"))
        .expect_err("数字不是全拼字母");
    assert_eq!(err, SessError::Key);
    assert!(ime.composing().is_empty(), "拒绝之后不得留下组合");
}

/// C2 成功：就绪的模型走完采集与识别，交回可编辑的文本。
#[test]
fn voice_capture_yields_a_transcript() {
    let mut voice = Voice::open("v-1", Availability::Ready).expect("可开会话");
    assert_eq!(asked(&mut voice, 1, Request::Open), Reply::Ready);
    assert_eq!(asked(&mut voice, 2, Request::Start), Reply::Ready);
    assert_eq!(
        voice
            .apply(&VoiceFrame::new("v-1", 3, Request::Stop))
            .expect("可停止"),
        VoiceStep::Capture
    );
    let heard = Transcript::new("查看磁盘", 0.92, audio()).expect("可构造");
    match voice.submit(3, Ok(heard)).expect("可交回") {
        Reply::Result(item) => assert_eq!(item.text(), "查看磁盘"),
        other => panic!("期望识别结果，实得 {other:?}"),
    }
    assert_eq!(voice.phase(), Phase::Idle, "交回后回到空闲");
}

/// C2 失败：没有模型就是显式 unavailable，不退化成键盘输入。
#[test]
fn voice_without_a_model_fails_instead_of_falling_back() {
    let mut voice = Voice::open("v-1", Availability::Unavailable).expect("可开会话");
    match voice
        .apply(&VoiceFrame::new("v-1", 1, Request::Start))
        .expect("可受理")
    {
        VoiceStep::Reply(Reply::Fail(fail)) => {
            assert_eq!(fail.status(), Availability::Unavailable);
            assert_eq!(fail.status().status(), "unavailable");
            assert!(!fail.detail().is_empty(), "失败必须带可执行说明");
        }
        other => panic!("期望显式失败，实得 {other:?}"),
    }
    assert_eq!(voice.phase(), Phase::Idle, "没有进入采集");
    assert!(
        matches!(voice.submit(1, Err(AsrError::Empty)), Err(IpcError::Phase)),
        "没开始采集就没有可交回的结果"
    );
}

/// C3 成功：socket 上的一行结构化意图变成 API 事件。
#[test]
fn api_socket_accepts_a_structured_intent() {
    let endpoint = Endpoint::new("ok");
    let mut api = Api::at(endpoint.path()).expect("可打开入口");
    let mut client = UnixStream::connect(endpoint.path()).expect("可连接");
    write_line(&mut client, &intent_line(A, "api", "T1", "tui"));

    let (events, rejects) = wait(&mut api, 1);
    assert!(rejects.is_empty(), "合法请求不得被拒");
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].trace_id, parsed(A));
    assert_eq!(events[0].source, SourceKind::Api);
    assert_eq!(events[0].trust, Trust::T1);
    assert!(
        matches!(events[0].body, RawBody::Structured(_)),
        "结构化来源不得被改写成文本"
    );
    api.close().expect("可关闭");
}

/// C3 失败：来源种类不符的行被拒，`trace_id` 与失败三列都可核对。
#[test]
fn api_socket_rejects_a_foreign_source() {
    let endpoint = Endpoint::new("bad");
    let mut api = Api::at(endpoint.path()).expect("可打开入口");
    let mut client = UnixStream::connect(endpoint.path()).expect("可连接");
    write_line(&mut client, &intent_line(B, "keyboard", "T0", "tui"));

    let (events, rejects) = wait(&mut api, 1);
    assert!(events.is_empty(), "错误来源不得产出事件");
    assert_eq!(rejects.len(), 1);
    assert_eq!(rejects[0].trace(), parsed(B));
    assert_eq!(rejects[0].refusal().status(), "invalid");
    assert_eq!(rejects[0].refusal().detail(), "source.kind");
    assert!(!rejects[0].refusal().advice().is_empty());
    api.close().expect("可关闭");
}

/// C4 成功：整批读完后汇总与退出码都为成功。
#[test]
fn a_batch_runs_to_the_summary() {
    let mut batch = batch(&[
        intent_line(A, "batch", "T3", "headless"),
        intent_line(B, "batch", "T3", "headless"),
    ]);
    let (events, rejects) = read_batch(&mut batch);
    assert!(rejects.is_empty(), "合法行不得被拒");
    assert_eq!(events.len(), 2);
    assert_eq!(events[0].source, SourceKind::Batch);
    assert_eq!(events[0].trust, Trust::T3);
    assert!(
        matches!(&events[0].body, RawBody::Line(text) if text == &intent_line(A, "batch", "T3", "headless")),
        "批处理要保留原始行"
    );
    let summary = batch.summary();
    assert_eq!((summary.total(), summary.ok(), summary.failed()), (2, 2, 0));
    assert_eq!(summary.exit_code(), 0);
}

/// C4 失败：坏行只记一条拒绝，后续行照常处理，末行汇总给出非零退出码。
#[test]
fn one_bad_batch_line_does_not_stop_the_rest() {
    let mut batch = batch(&[
        intent_line(A, "batch", "T3", "headless"),
        intent_line(B, "batch", "T2", "headless"),
    ]);
    let (events, rejects) = read_batch(&mut batch);
    assert_eq!(events.len(), 1, "失败行之后的合法行仍要产出事件");
    assert_eq!(rejects.len(), 1);
    assert_eq!(rejects[0].trace(), parsed(B));
    assert_eq!(rejects[0].refusal().status(), "denied");
    assert_eq!(rejects[0].refusal().detail(), "T3");
    let summary = batch.summary();
    assert_eq!((summary.total(), summary.ok(), summary.failed()), (2, 1, 1));
    assert_eq!(summary.exit_code(), 1);
}

/// 一条输入法的词典：全拼 `zhong` 的首选是「中」。
fn dictionary() -> Dictionary {
    let entries = [
        Entry::new("zhong", "中", 900),
        Entry::new("zhong", "钟", 400),
    ];
    Dictionary::load(&build(&entries).expect("可构建")).expect("可加载")
}

/// 把一串字母逐键送进输入法并提交。
fn type_pinyin(ime: &mut Ime<'_>, pinyin: &str) -> String {
    let mut seq = 0;
    for key in pinyin.chars() {
        seq += 1;
        ime.apply(&ImeFrame::key("s-1", seq, key.to_string()))
            .expect("字母可按键");
    }
    seq += 1;
    match ime
        .apply(&ImeFrame::new("s-1", seq, Msg::Commit))
        .expect("可提交")
    {
        Effect::Commit(text) => text,
        other => panic!("期望提交文本，实得 {other:?}"),
    }
}

/// 用按键解码器解出字节对应的键事件。
fn decoded(bytes: &str) -> Vec<boot_tui::input::KeyEvent> {
    let mut decoder = Decoder::new();
    let mut out = Vec::new();
    decoder.feed(bytes.as_bytes(), &mut out);
    out
}

/// 音频引用：路径与摘要都按 §3.2 的格式给出。
fn audio() -> AudioRef {
    AudioRef::new(
        "/var/lib/airy/asr/1.wav",
        format!("sha256:{}", "a".repeat(64)),
        3120,
    )
    .expect("可构造")
}

/// 向语音会话发一条请求并取出应答。
fn asked(voice: &mut Voice, seq: u64, request: Request) -> Reply {
    match voice
        .apply(&VoiceFrame::new("v-1", seq, request))
        .expect("可受理")
    {
        VoiceStep::Reply(reply) => reply,
        other => panic!("期望直接应答，实得 {other:?}"),
    }
}

/// 构造一行 `airy.intent/1`；字段按契约 §2.1。
fn intent_line(trace: &str, kind: &str, level: &str, mode: &str) -> String {
    format!(
        r#"{{"schema":"airy.intent/1","trace_id":"{trace}","ts":"2026-11-19T10:00:00+08:00","source":{{"kind":"{kind}","device":"local"}},"trust":{{"level":"{level}","principal":"uid:1000"}},"session":{{"mode":"{mode}","id":"s-1"}},"payload":{{"kind":"text","text":"查看磁盘","lang":"zh-CN"}}}}"#
    )
}

fn parsed(trace: &str) -> TraceId {
    TraceId::parse(trace).expect("追踪号")
}

/// 写一行并立刻刷新，模拟对端按行投递。
fn write_line(client: &mut UnixStream, line: &str) {
    client.write_all(line.as_bytes()).expect("可写入");
    client.write_all(b"\n").expect("可写入行尾");
    client.flush().expect("可刷新");
}

/// 轮询入口直到攒够 `want` 条结果；socket 是非阻塞的。
fn wait(api: &mut Api, want: usize) -> (Vec<RawEvent>, Vec<Reject>) {
    let mut events = Vec::new();
    let mut rejects = Vec::new();
    for _ in 0..200 {
        events.extend(api.drain(8).expect("读取不得失败"));
        while let Some(reject) = api.next_reject() {
            rejects.push(reject);
        }
        if events.len() + rejects.len() >= want {
            return (events, rejects);
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    panic!("入口在预算内没有攒够 {want} 条结果");
}

/// 从逐行文本构造批处理源。
fn batch(lines: &[String]) -> Batch {
    let mut text = String::new();
    for line in lines {
        text.push_str(line);
        text.push('\n');
    }
    Batch::new(Box::new(Cursor::new(text)))
}

/// 读完整批并收走全部拒绝记录。
fn read_batch(batch: &mut Batch) -> (Vec<RawEvent>, Vec<Reject>) {
    let mut events = Vec::new();
    let mut rejects = Vec::new();
    while !batch.is_done() {
        events.extend(batch.drain(8).expect("批处理读取不得失败"));
        while let Some(reject) = batch.next_reject() {
            rejects.push(reject);
        }
    }
    (events, rejects)
}

/// 每个用例独占一个 socket 路径，退出时连同文件一起清掉。
struct Endpoint(PathBuf);

impl Endpoint {
    fn new(tag: &str) -> Self {
        let seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|since| since.as_nanos())
            .unwrap_or_default();
        let name = format!("airy-matrix-{tag}-{}-{seed}.sock", std::process::id());
        Self(std::env::temp_dir().join(name))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for Endpoint {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
