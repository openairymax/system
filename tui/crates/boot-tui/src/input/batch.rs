// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 批处理输入（C4）
//
// 形态按 §3.4：stdin 或文件里每行一个 `airy.intent/1`，读到 EOF 结束。这份
// 入口只服务 CI、验收脚本与无人值守任务，因此信任级别固定 T3：消息里写别
// 的级别一律 denied，也不存在能把它提升的命令行参数。T3 的高危动作默认拒
// 绝由执行侧的规则判定（`intent::route`），接入侧不重复实现一遍。
//
// 单行失败不终止整批：语法、字段、来源、级别或重复 `trace_id` 都只记一条
// 带 `trace_id` 的拒绝记录，然后继续下一行。拒绝记录不设上限——§3.4 要求每
// 行输入都有按 `trace_id` 对应的结果，滑窗丢记录会让输出与输入对不上，调用
// 方应在每轮 `drain` 后取走。
//
// 末行汇总由 [`Summary`] 给出成功与失败计数，[`Summary::exit_code`] 把失败
// 数映射为非零退出码。结果 JSON 由执行侧按 `trace_id` 逐行产出：契约只规定
// 对应关系，没有定义结果帧格式，本模块不发明协议。
//
// 行上限与 C3 的帧上限同取 64 KiB；超长行只判该行失败并跳到下一行，不终止
// 整批，因为批处理的每一行本来就是独立请求。
//
// SSoT: 30-interfaces/11-input-ingress.md §2.4、§3.4、§4.1、§7、§10.2。
// ===========================================================================

//! C4 批处理：stdin / 文件里逐行读取意图。

use std::collections::{HashSet, VecDeque};
use std::fs::File;
use std::io::{self, BufRead, BufReader, Read};
use std::path::Path;

use intent::{Intent, Refusal, SourceKind, TraceId, Trust};
use serde_json::Value;

use super::event::{
    bad_line, refusal_of, shape, syntax, take_events, Clock, RawBody, RawEvent, Reject,
};
use super::source::InputSource;

/// 单行字节上限；与 C3 的帧上限一致，超长行按行级失败处理。
pub const MAX_LINE: usize = 64 * 1024;

/// 一次取行的结果。
enum Pull {
    /// 一整行原始字节（含行尾，待裁剪）。
    Line(Vec<u8>),
    /// 超过 [`MAX_LINE`] 的残行；余下字节已丢弃。
    Oversize,
    /// 输入结束。
    Eof,
}

/// 一批输入的汇总：总量、成功与失败，用于末行汇总与退出码。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Summary {
    total: usize,
    ok: usize,
    failed: usize,
}

impl Summary {
    /// 参与判定的行数；空行不计入。
    pub fn total(&self) -> usize {
        self.total
    }

    /// 产出事件的行数。
    pub fn ok(&self) -> usize {
        self.ok
    }

    /// 被拒的行数。
    pub fn failed(&self) -> usize {
        self.failed
    }

    /// §3.4 要求的非零退出码：有失败行即为 1，全成功为 0。
    pub fn exit_code(&self) -> i32 {
        if self.failed == 0 {
            0
        } else {
            1
        }
    }
}

/// C4 输入源：逐行读取，行级失败不终止整批。
pub struct Batch {
    reader: Option<Box<dyn BufRead + Send>>,
    eof: bool,
    pending: VecDeque<RawEvent>,
    rejects: VecDeque<Reject>,
    seen: HashSet<TraceId>,
    total: usize,
    ok: usize,
    failed: usize,
    clock: Clock,
}

impl Batch {
    /// 从标准输入读取；shell 管道与 CI 的常规用法。
    pub fn stdin() -> Self {
        Self::new(Box::new(BufReader::new(io::stdin())))
    }

    /// 从文件读取；打不开即整体不可用，不进入半批状态。
    pub fn file(path: &Path) -> Result<Self, Refusal> {
        let file = File::open(path).map_err(|_| Refusal::unavailable("batch"))?;
        Ok(Self::new(Box::new(BufReader::new(file))))
    }

    /// 从任意逐行读取器读取；测试与嵌入场景使用。
    pub fn new(reader: Box<dyn BufRead + Send>) -> Self {
        Self {
            reader: Some(reader),
            eof: false,
            pending: VecDeque::new(),
            rejects: VecDeque::new(),
            seen: HashSet::new(),
            total: 0,
            ok: 0,
            failed: 0,
            clock: Clock::new(),
        }
    }

    /// 取走一条被拒记录；没有则返回 `None`。
    pub fn next_reject(&mut self) -> Option<Reject> {
        self.rejects.pop_front()
    }

    /// 当前汇总；未读到 EOF 前是部分结果。
    pub fn summary(&self) -> Summary {
        Summary {
            total: self.total,
            ok: self.ok,
            failed: self.failed,
        }
    }

    /// 输入已结束且事件已全部取走。
    pub fn is_done(&self) -> bool {
        self.eof && self.pending.is_empty()
    }

    /// 读入并校验，直到积压达到 `max` 或 EOF。
    fn pump(&mut self, max: usize) -> Result<(), Refusal> {
        while !self.eof && self.pending.len() < max {
            match self.pull()? {
                Pull::Line(raw) => self.offer(raw),
                Pull::Oversize => self.fail(Refusal::invalid("frame")),
                Pull::Eof => self.eof = true,
            }
        }
        Ok(())
    }

    /// 取一行原始字节；长度上限由 `take` 截住，超出部分单独丢弃。
    fn pull(&mut self) -> Result<Pull, Refusal> {
        let mut raw = Vec::new();
        let Some(reader) = self.reader.as_mut() else {
            return Ok(Pull::Eof);
        };
        let got = (&mut **reader)
            .take((MAX_LINE + 1) as u64)
            .read_until(b'\n', &mut raw)
            .map_err(|_| Refusal::internal("batch.read"))?;
        if got == 0 {
            return Ok(Pull::Eof);
        }
        if raw.last() != Some(&b'\n') && raw.len() > MAX_LINE {
            self.skip()?;
            return Ok(Pull::Oversize);
        }
        Ok(Pull::Line(raw))
    }

    /// 丢弃当前行的剩余字节，保证下一行仍从行首开始。
    fn skip(&mut self) -> Result<(), Refusal> {
        let Some(reader) = self.reader.as_mut() else {
            return Ok(());
        };
        loop {
            let (len, newline) = {
                let buf = reader
                    .fill_buf()
                    .map_err(|_| Refusal::internal("batch.read"))?;
                if buf.is_empty() {
                    return Ok(());
                }
                (buf.len(), buf.iter().position(|byte| *byte == b'\n'))
            };
            match newline {
                Some(at) => {
                    reader.consume(at + 1);
                    return Ok(());
                }
                None => reader.consume(len),
            }
        }
    }

    /// 处理一行：空行忽略，其余按 §2.4 校验后入队或记拒绝。
    fn offer(&mut self, mut raw: Vec<u8>) {
        if raw.last() == Some(&b'\n') {
            raw.pop();
        }
        if raw.last() == Some(&b'\r') {
            raw.pop();
        }
        let Ok(text) = String::from_utf8(raw) else {
            self.fail(Refusal::invalid("utf8"));
            return;
        };
        if text.is_empty() {
            return;
        }
        self.total += 1;
        match self.admit(&text) {
            Ok(event) => {
                self.ok += 1;
                self.pending.push_back(event);
            }
            Err(reject) => {
                self.failed += 1;
                self.rejects.push_back(reject);
            }
        }
    }

    /// 校验一行并产出事件；失败只影响这一行。
    fn admit(&mut self, text: &str) -> Result<RawEvent, Reject> {
        let value =
            serde_json::from_str::<Value>(text).map_err(|err| self.bad(None, syntax(&err)))?;
        let intent = serde_json::from_value::<Intent>(value.clone())
            .map_err(|err| self.bad(Some(&value), shape(&err)))?;
        if intent.source.kind != SourceKind::Batch {
            return Err(self.reject(intent.trace_id, Refusal::invalid("source.kind")));
        }
        if intent.trust.level != Trust::T3 {
            return Err(self.reject(intent.trace_id, Refusal::denied(Trust::T3.label())));
        }
        if let Err(err) = intent.check() {
            return Err(self.reject(intent.trace_id, refusal_of(err, SourceKind::Batch)));
        }
        if !self.seen.insert(intent.trace_id) {
            return Err(self.reject(intent.trace_id, Refusal::invalid("trace_id")));
        }
        Ok(RawEvent {
            trace_id: intent.trace_id,
            source: SourceKind::Batch,
            trust: Trust::T3,
            at: self.clock.now(),
            body: RawBody::Line(text.to_string()),
        })
    }

    /// 无法解码成文本的行：计数、登记并继续。
    fn fail(&mut self, refusal: Refusal) {
        self.total += 1;
        self.failed += 1;
        let trace = self.clock.next();
        self.rejects.push_back(Reject::new(trace, refusal));
    }

    fn bad(&mut self, value: Option<&Value>, refusal: Refusal) -> Reject {
        bad_line(&mut self.clock, value, refusal)
    }

    fn reject(&self, trace: TraceId, refusal: Refusal) -> Reject {
        Reject::new(trace, refusal)
    }
}

impl InputSource for Batch {
    fn kind(&self) -> SourceKind {
        SourceKind::Batch
    }

    fn drain(&mut self, max: usize) -> Result<Vec<RawEvent>, Refusal> {
        self.pump(max)?;
        Ok(take_events(&mut self.pending, max))
    }

    fn close(&mut self) -> Result<(), Refusal> {
        self.reader = None;
        self.eof = true;
        self.pending.clear();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;
    use std::path::PathBuf;

    use super::*;

    const A: &str = "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40";
    const B: &str = "0a1b2c3d4e5f60718293a4b5c6d7e8f9";

    fn reader(text: &str) -> Box<dyn BufRead + Send> {
        Box::new(Cursor::new(text.as_bytes().to_vec()))
    }

    fn bytes(raw: Vec<u8>) -> Box<dyn BufRead + Send> {
        Box::new(Cursor::new(raw))
    }

    fn line(trace: &str, level: &str, kind: &str) -> String {
        format!(
            r#"{{"schema":"airy.intent/1","trace_id":"{trace}","ts":"2026-11-17T10:02:11+08:00","source":{{"kind":"{kind}","device":"local"}},"trust":{{"level":"{level}","principal":"svc:ci"}},"session":{{"mode":"headless","id":"s-1"}},"payload":{{"kind":"text","text":"查看磁盘","lang":"zh-CN"}}}}"#
        )
    }

    /// 读完整批，顺带收走全部拒绝记录。
    fn run(batch: &mut Batch) -> (Vec<RawEvent>, Vec<Reject>) {
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

    #[test]
    fn a_t3_line_becomes_a_batch_event() {
        let mut batch = Batch::new(reader(&(line(A, "T3", "batch") + "\n")));
        let (events, rejects) = run(&mut batch);
        assert!(rejects.is_empty(), "合法行不得被拒");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].trace_id, TraceId::parse(A).expect("追踪号"));
        assert_eq!(events[0].source, SourceKind::Batch);
        assert_eq!(events[0].trust, Trust::T3);
        match &events[0].body {
            RawBody::Line(text) => assert_eq!(text, &line(A, "T3", "batch")),
            other => panic!("批处理必须保留原始行，实得 {other:?}"),
        }
        let summary = batch.summary();
        assert_eq!((summary.total(), summary.ok(), summary.failed()), (1, 1, 0));
        assert_eq!(summary.exit_code(), 0);
    }

    #[test]
    fn a_batch_cannot_lift_trust() {
        for level in ["T0", "T1", "T2"] {
            let mut batch = Batch::new(reader(&(line(A, level, "batch") + "\n")));
            let (events, rejects) = run(&mut batch);
            assert!(events.is_empty(), "{level} 不得进入批处理");
            assert_eq!(rejects.len(), 1);
            assert_eq!(rejects[0].refusal().status(), "denied");
            assert_eq!(rejects[0].refusal().detail(), "T3");
            assert_eq!(batch.summary().exit_code(), 1);
        }
    }

    #[test]
    fn another_channel_cannot_enter_the_batch() {
        let mut batch = Batch::new(reader(&(line(A, "T3", "keyboard") + "\n")));
        let (events, rejects) = run(&mut batch);
        assert!(events.is_empty());
        assert_eq!(rejects.len(), 1);
        assert_eq!(rejects[0].refusal().status(), "invalid");
        assert_eq!(rejects[0].refusal().detail(), "source.kind");
    }

    #[test]
    fn a_bad_line_does_not_stop_the_batch() {
        let text = format!(
            "{{not json}}\n{}\n{{\"schema\":\"airy.intent/1\"}}\n{}\n",
            line(A, "T3", "batch"),
            line(B, "T3", "batch")
        );
        let mut batch = Batch::new(reader(&text));
        let (events, rejects) = run(&mut batch);
        assert_eq!(events.len(), 2, "坏行前后的合法行都要产出");
        assert_eq!(rejects.len(), 2);
        assert_eq!(rejects[1].refusal().detail(), "trace_id");
        let summary = batch.summary();
        assert_eq!((summary.total(), summary.ok(), summary.failed()), (4, 2, 2));
        assert_eq!(summary.exit_code(), 1);
    }

    #[test]
    fn each_line_needs_a_fresh_trace() {
        let text = format!("{}\n{}\n", line(A, "T3", "batch"), line(A, "T3", "batch"));
        let mut batch = Batch::new(reader(&text));
        let (events, rejects) = run(&mut batch);
        assert_eq!(events.len(), 1);
        assert_eq!(rejects.len(), 1);
        assert_eq!(rejects[0].refusal().status(), "invalid");
        assert_eq!(rejects[0].refusal().detail(), "trace_id");
        assert_eq!(batch.summary().failed(), 1);
    }

    #[test]
    fn an_overlong_line_does_not_eat_its_neighbour() {
        let text = format!(
            "{}\n{}\n",
            "x".repeat(MAX_LINE + 10),
            line(A, "T3", "batch")
        );
        let mut batch = Batch::new(reader(&text));
        let (events, rejects) = run(&mut batch);
        assert_eq!(events.len(), 1, "超长行之后的合法行仍要读到");
        assert_eq!(events[0].trace_id, TraceId::parse(A).expect("追踪号"));
        assert_eq!(rejects.len(), 1);
        assert_eq!(rejects[0].refusal().status(), "invalid");
        assert_eq!(rejects[0].refusal().detail(), "frame");
    }

    #[test]
    fn crlf_and_an_unterminated_last_line_are_accepted() {
        let text = format!("{}\r\n{}", line(A, "T3", "batch"), line(B, "T3", "batch"));
        let mut batch = Batch::new(reader(&text));
        let (events, rejects) = run(&mut batch);
        assert!(rejects.is_empty());
        assert_eq!(events.len(), 2);
        match (&events[0].body, &events[1].body) {
            (RawBody::Line(first), RawBody::Line(last)) => {
                assert_eq!(first, &line(A, "T3", "batch"));
                assert_eq!(last, &line(B, "T3", "batch"));
            }
            other => panic!("批处理必须保留原始行，实得 {other:?}"),
        }
    }

    #[test]
    fn blank_lines_are_not_counted() {
        let text = format!("\n\n{}\n\n", line(A, "T3", "batch"));
        let mut batch = Batch::new(reader(&text));
        let (events, rejects) = run(&mut batch);
        assert!(rejects.is_empty());
        assert_eq!(events.len(), 1);
        assert_eq!(batch.summary().total(), 1);
    }

    #[test]
    fn invalid_utf8_is_a_line_failure() {
        let mut data = vec![0xff, 0xfe, b'\n'];
        data.extend_from_slice(line(A, "T3", "batch").as_bytes());
        data.push(b'\n');
        let mut batch = Batch::new(bytes(data));
        let (events, rejects) = run(&mut batch);
        assert_eq!(events.len(), 1);
        assert_eq!(rejects.len(), 1);
        assert_eq!(rejects[0].refusal().status(), "invalid");
        assert_eq!(rejects[0].refusal().detail(), "utf8");
    }

    #[test]
    fn close_is_repeatable_and_keeps_the_tally() {
        let mut batch = Batch::new(reader(&(line(A, "T3", "batch") + "\n")));
        let _ = run(&mut batch);
        let before = batch.summary();
        assert!(batch.close().is_ok());
        assert!(batch.close().is_ok());
        assert!(batch.drain(8).expect("关闭后读取").is_empty());
        assert_eq!(batch.summary(), before);
    }

    #[test]
    fn a_missing_file_reports_unavailable() {
        let path = PathBuf::from("/nonexistent/airy-batch-input.jsonl");
        let refusal = Batch::file(&path).err().expect("文件不存在必须失败");
        assert_eq!(refusal.status(), "unavailable");
        assert_eq!(refusal.detail(), "batch");
    }

    #[test]
    fn a_file_batch_reads_to_eof() {
        let mut path = std::env::temp_dir();
        path.push(format!("airy-batch-{}.jsonl", std::process::id()));
        let text = format!("{}\n{}\n", line(A, "T3", "batch"), line(B, "T3", "batch"));
        std::fs::write(&path, text).expect("写入临时文件");
        let mut batch = Batch::file(&path).expect("打开临时文件");
        let (events, rejects) = run(&mut batch);
        let _ = std::fs::remove_file(&path);
        assert!(rejects.is_empty());
        assert_eq!(events.len(), 2);
        assert_eq!(batch.summary().ok(), 2);
    }
}
