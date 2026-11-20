// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 执行审计行
//
// 一次执行要能被事后归因：谁（principal）、做了什么（动作名与 argv 摘要）、
// 过了哪条批准（approval）、结果如何（状态串、返回码、建议）、输出有没有
// 被截断。契约 §3 定了字段，§14.9 补上 principal、动作与摘要；本模块只把
// 它们压成一条 journal 行——字段顺序固定，一次执行一行，缺字段的行不能
// 用于归因。
//
// 空格、换行与控制字节一律折成百分号转义，行内除字段分隔外不出现空白；
// 输出内容最多保留 512 字节转义文本，`out`/`err` 记捕获到的字节数，
// `out_cut`/`err_cut` 说明行里是否漏了字节——截断事实单独成列，不与内容
// 混淆。
//
// 追踪号格式错或被新请求复用时，接入侧另起审计行并带上被复用的旧号；本
// 模块用 `prev` 留出这一列，执行侧自身只写本请求的追踪号。
//
// SSoT: system/contracts/audit.md §3、APPROVAL.md §4、0.0.5 §14.9。
// ===========================================================================

//! 一次执行的可归因记录与单行渲染。

use intent::TraceId;

use crate::exec::{digest, Request};
use crate::normalize::{Captured, Outcome};

/// 行首标记；每一行审计都以它开头。
pub const PREFIX: &str = "AIRYMAX-AUDIT";

/// 输出内容在行内保留的最大字节数。
pub const TEXT: usize = 512;

/// 空值记号；与诊断行保持同一写法。
const DASH: &str = "-";

/// 必须转义的五个字节；其余可打印字符原样保留。
const ESC: [(char, &str); 5] = [
    ('%', "%25"),
    (' ', "%20"),
    ('\n', "%0A"),
    ('\r', "%0D"),
    ('\t', "%09"),
];

/// 一条审计记录；字段名与契约 §3 一致。
#[derive(Clone, Debug)]
pub struct Entry {
    /// 本请求的追踪号。
    pub trace: TraceId,
    /// 被复用的旧追踪号；没有复用时为 `None`。
    pub prev: Option<TraceId>,
    /// 发起方标识，形如 `console`、`uid:1000`、`svc:<name>`。
    pub principal: String,
    /// 已注册动作名。
    pub action: String,
    /// argv 摘要，与审批链用同一条算法。
    pub digest: String,
    /// 契约 §7 的状态串。
    pub status: &'static str,
    /// 失败返回码；成功为 0。
    pub errno: i32,
    /// 子进程退出码；没跑起来时为 `None`。
    pub code: Option<i32>,
    /// 审批被拒时的原因；其余为 `None`。
    pub reason: Option<&'static str>,
    /// 命中的批准文件名；未命中时为 `None`。
    pub approval: Option<String>,
    /// 失败建议；成功为空串。
    pub advice: String,
    /// 标准输出。
    pub stdout: Captured,
    /// 标准错误。
    pub stderr: Captured,
}

impl Entry {
    /// 从请求与结果生成一条记录；摘要按审批时的同一条算法重算。
    pub fn of(req: &Request, principal: &str, outcome: Outcome) -> Self {
        Self {
            trace: outcome.trace,
            prev: None,
            principal: principal.to_owned(),
            action: req.action.to_owned(),
            digest: digest(req),
            status: outcome.status,
            errno: outcome.errno,
            code: outcome.code,
            reason: outcome.reason,
            approval: outcome.approval,
            advice: outcome.advice.to_owned(),
            stdout: outcome.stdout,
            stderr: outcome.stderr,
        }
    }

    /// 渲染成一行；字段顺序即契约 §3 的顺序。
    pub fn line(&self) -> String {
        let (out, out_cut) = fold(&self.stdout.text, TEXT);
        let (err, err_cut) = fold(&self.stderr.text, TEXT);
        [
            PREFIX.to_owned(),
            pair("trace", &self.trace.to_hex()),
            pair("prev", &hexed(self.prev)),
            pair("principal", &flat(&self.principal)),
            pair("action", &flat(&self.action)),
            pair("digest", &self.digest),
            pair("status", self.status),
            pair("errno", &self.errno.to_string()),
            pair("code", &code_of(self.code)),
            pair("reason", &dash(self.reason.unwrap_or(""))),
            pair("approval", &dash(self.approval.as_deref().unwrap_or(""))),
            pair("advice", &flat(&self.advice)),
            pair("out", &self.stdout.text.len().to_string()),
            pair("out_cut", flag(out_cut)),
            pair("err", &self.stderr.text.len().to_string()),
            pair("err_cut", flag(err_cut)),
            pair("stdout", &out),
            pair("stderr", &err),
        ]
        .join(" ")
    }
}

/// 一个 `键=值` 片段。
fn pair(key: &str, value: &str) -> String {
    format!("{key}={value}")
}

/// 旧追踪号；没有时写 `-`。
fn hexed(trace: Option<TraceId>) -> String {
    trace.map_or_else(|| DASH.to_owned(), TraceId::to_hex)
}

/// 空值折成 `-`。
fn dash(value: &str) -> String {
    if value.is_empty() {
        DASH.to_owned()
    } else {
        value.to_owned()
    }
}

/// 退出码；没有时写 `-`。
fn code_of(code: Option<i32>) -> String {
    code.map_or_else(|| DASH.to_owned(), |value| value.to_string())
}

/// 布尔字段的写法。
fn flag(cut: bool) -> &'static str {
    if cut {
        "1"
    } else {
        "0"
    }
}

/// 自由文本折成单行并去空。
fn flat(text: &str) -> String {
    let (folded, _) = fold(text, TEXT);
    dash(&folded)
}

/// 按字节上限折叠；返回文本与是否因上限漏掉字节。
fn fold(text: &str, limit: usize) -> (String, bool) {
    let mut out = String::with_capacity(text.len().min(limit));
    for ch in text.chars() {
        let piece = esc(ch);
        if out.len() + piece.len() > limit {
            return (out, true);
        }
        out.push_str(&piece);
    }
    (out, false)
}

/// 单个字符的转义写法。
fn esc(ch: char) -> String {
    ESC.iter()
        .find(|(raw, _)| *raw == ch)
        .map_or_else(|| ctl(ch), |(_, code)| (*code).to_owned())
}

/// 控制字符写 `%XX`，其余原样。
fn ctl(ch: char) -> String {
    if ch.is_control() {
        format!("%{:02X}", ch as u32)
    } else {
        ch.to_string()
    }
}
