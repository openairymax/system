// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// V-04 实机与降级取证的记录校验器
//
// 实机取证最容易出的一类错，是把「在开发机上顺手跑的夹具」当成目标机读数
// 记进账里。这里把两类记录用字段硬分开：evidence=fixture 必须带 fixture-
// 用例前缀，evidence=field 必须给出 when 与 host 且不许带前缀；发版覆盖
// 只统计 field 且 result=pass 的记录。
//
// 记录是一段以 AIRYMAX-TUI-FIELD/1 起、AIRYMAX-TUI-FIELD-END 止的纯文本，
// 键值一行一条，键集封闭。键集封闭不是洁癖：采集器与校验器一旦各认一套
// 拼写，记录看起来「有数据」，统计时却全落空。新增维度必须同时改本文件与
// 10-references/tui-field-evidence.md。
//
// 本文件只判格式与覆盖，不判画面好坏：中文显示的主观项（AC-V4）由截图
// 摘要与人工记录承担，校验器只保证截图确实附了 sha256。
//
// SSoT: 0.0.5 方案 §14.13、§15.4。
// ===========================================================================

//! 实机与降级取证记录的校验器。

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

/// 记录起始标记。
const MARK: &str = "AIRYMAX-TUI-FIELD/1";
/// 记录结束标记。
const END: &str = "AIRYMAX-TUI-FIELD-END";
/// 允许出现的键；新增维度必须两处同改。
const KEYS: [&str; 14] = [
    "case",
    "evidence",
    "arch",
    "os",
    "build",
    "when",
    "host",
    "term",
    "cols",
    "rows",
    "observed",
    "result",
    "note",
    "screenshot",
];
/// 每条记录都必须给出的键。
const REQUIRED: [&str; 9] = [
    "case", "evidence", "arch", "os", "build", "term", "cols", "rows", "result",
];
/// 只有实机记录才允许出现的键。
const FIELD_ONLY: [&str; 2] = ["when", "host"];
/// 降级矩阵七格加中文显示一项。
const CASES: [&str; 8] = [
    "ime-recover",
    "ime-no-dict",
    "asr-unavailable",
    "probe-stale",
    "render-slow",
    "inject-unknown",
    "tty-small",
    "display-zh",
];
/// 本版声明支持的三条架构。
const ARCHES: [&str; 3] = ["x86_64", "aarch64", "riscv64"];
/// 发版必须给出实机读数的两条架构；riscv64 用户态本版移出范围。
const RELEASE_ARCH: [&str; 2] = ["x86_64", "aarch64"];
/// 允许的判定词。
const RESULTS: [&str; 4] = ["pass", "fail", "unavailable", "blocked"];

/// 一条记录；`at` 是起始行号，报错时用来定位。
#[derive(Debug)]
struct Record {
    at: usize,
    fields: BTreeMap<String, String>,
}

impl Record {
    /// 取一个键；缺省为空串，是否必填由 `check` 判。
    fn word(&self, key: &str) -> &str {
        self.fields.get(key).map_or("", String::as_str)
    }
}

/// 统一的行号前缀，避免每个分支各写一遍。
fn fail(at: usize, why: &str) -> String {
    format!("第 {at} 行: {why}")
}

/// 把一段文本解析成记录序列；空行只允许出现在记录之间。
fn parse(text: &str) -> Result<Vec<Record>, String> {
    let mut out = Vec::new();
    let mut cur: Option<(usize, BTreeMap<String, String>)> = None;
    for (index, raw) in text.lines().enumerate() {
        let at = index + 1;
        let line = raw.trim_end_matches('\r');
        if cur.is_none() {
            if line.is_empty() {
                continue;
            }
            if line != MARK {
                return Err(fail(at, "记录必须以起始标记开头"));
            }
            cur = Some((at, BTreeMap::new()));
            continue;
        }
        if line == END {
            let Some((start, fields)) = cur.take() else {
                return Err(fail(at, "内部状态不一致"));
            };
            out.push(Record { at: start, fields });
            continue;
        }
        let Some((_, fields)) = cur.as_mut() else {
            return Err(fail(at, "内部状态不一致"));
        };
        if line.is_empty() {
            return Err(fail(at, "记录内不允许空行"));
        }
        let Some((key, value)) = line.split_once('=') else {
            return Err(fail(at, "缺少 = 分隔"));
        };
        if !KEYS.contains(&key) {
            return Err(fail(at, &format!("未知键 {key}")));
        }
        if fields.insert(key.to_owned(), value.to_owned()).is_some() {
            return Err(fail(at, &format!("重复键 {key}")));
        }
    }
    match cur {
        Some((start, _)) => Err(fail(start, "记录未以结束标记收尾")),
        None => Ok(out),
    }
}

/// 校验一条记录的取值域。
fn check(rec: &Record) -> Result<(), String> {
    for key in REQUIRED {
        if rec.word(key).is_empty() {
            return Err(fail(rec.at, &format!("缺少必填键 {key}")));
        }
    }
    let evidence = rec.word("evidence");
    if !matches!(evidence, "field" | "fixture") {
        return Err(fail(rec.at, "evidence 只能是 field 或 fixture"));
    }
    check_case(rec, evidence)?;
    if !ARCHES.contains(&rec.word("arch")) {
        return Err(fail(rec.at, &format!("未知架构 {}", rec.word("arch"))));
    }
    if !RESULTS.contains(&rec.word("result")) {
        return Err(fail(rec.at, &format!("未知判定 {}", rec.word("result"))));
    }
    size(rec, "cols")?;
    size(rec, "rows")?;
    if evidence == "field" {
        check_field(rec)?;
    }
    if !rec.word("screenshot").is_empty() {
        digest(rec)?;
    }
    Ok(())
}

/// 用例名与取证类别必须一致。
fn check_case(rec: &Record, evidence: &str) -> Result<(), String> {
    let case = rec.word("case");
    let bare = case.strip_prefix("fixture-").unwrap_or(case);
    if !CASES.contains(&bare) {
        return Err(fail(rec.at, &format!("未知用例 {case}")));
    }
    match (evidence, case.starts_with("fixture-")) {
        ("fixture", false) => Err(fail(rec.at, "夹具用例必须带 fixture- 前缀")),
        ("field", true) => Err(fail(rec.at, "实机用例不得带 fixture- 前缀")),
        _ => Ok(()),
    }
}

/// 实机记录必须能追到机器与时刻。
fn check_field(rec: &Record) -> Result<(), String> {
    for key in FIELD_ONLY {
        if rec.word(key).is_empty() {
            return Err(fail(rec.at, &format!("实机记录缺少 {key}")));
        }
    }
    when(rec)?;
    if rec.word("host").contains(char::is_whitespace) {
        return Err(fail(rec.at, "host 不允许空白"));
    }
    Ok(())
}

/// 尺寸必须是正整数：0 列的终端不是终端。
fn size(rec: &Record, key: &str) -> Result<(), String> {
    match rec.word(key).parse::<u16>() {
        Ok(n) if n > 0 => Ok(()),
        _ => Err(fail(rec.at, &format!("{key} 必须是正整数"))),
    }
}

/// 时刻按 UTC 的 ISO-8601 秒级写法。
fn when(rec: &Record) -> Result<(), String> {
    let text = rec.word("when");
    let bytes = text.as_bytes();
    let shaped = bytes.len() == 20
        && bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes[10] == b'T'
        && bytes[13] == b':'
        && bytes[16] == b':'
        && bytes[19] == b'Z'
        && bytes
            .iter()
            .enumerate()
            .all(|(i, b)| matches!(i, 4 | 7 | 10 | 13 | 16 | 19) || b.is_ascii_digit());
    if shaped {
        Ok(())
    } else {
        Err(fail(rec.at, &format!("when 不是 UTC 秒级时刻: {text}")))
    }
}

/// 截图摘要必须是完整的小写 sha256。
fn digest(rec: &Record) -> Result<(), String> {
    let text = rec.word("screenshot");
    let shaped = text.len() == 64 && text.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'));
    if shaped {
        Ok(())
    } else {
        Err(fail(rec.at, "screenshot 必须是 64 位小写十六进制"))
    }
}

/// 读一个取证包目录；目录里每个 `*.field` 都必须是合法记录。
fn bundle(dir: &Path) -> Result<Vec<Record>, String> {
    let mut paths: Vec<PathBuf> = fs::read_dir(dir)
        .map_err(|err| format!("{}: {err}", dir.display()))?
        .filter_map(|entry| entry.ok().map(|entry| entry.path()))
        .filter(|path| path.extension().is_some_and(|ext| ext == "field"))
        .collect();
    paths.sort();
    if paths.is_empty() {
        return Err(format!("{}: 没有 *.field 记录", dir.display()));
    }
    let mut out = Vec::new();
    for path in paths {
        let text = fs::read_to_string(&path).map_err(|err| format!("{}: {err}", path.display()))?;
        out.extend(parse(&text).map_err(|why| format!("{}: {why}", path.display()))?);
    }
    Ok(out)
}

/// 发版覆盖还差哪些格；只认实机且判定的记录。
fn missing(records: &[Record]) -> Vec<String> {
    let mut gaps = Vec::new();
    for arch in RELEASE_ARCH {
        for case in CASES {
            let hit = records.iter().any(|rec| {
                rec.word("evidence") == "field"
                    && rec.word("arch") == arch
                    && rec.word("case") == case
                    && rec.word("result") == "pass"
            });
            if !hit {
                gaps.push(format!("{arch}/{case}"));
            }
        }
    }
    gaps
}

/// 一个取证包的完整结论：格式、失败读数与覆盖。
fn judge(records: &[Record]) -> Vec<String> {
    let mut issues = Vec::new();
    for rec in records {
        if let Err(why) = check(rec) {
            issues.push(why);
        }
        if rec.word("result") == "fail" {
            issues.push(format!("{}: 实机判定为 fail", rec.word("case")));
        }
    }
    for gap in missing(records) {
        issues.push(format!("缺少实机用例 {gap}"));
    }
    issues
}

/// 夹具目录。
fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/field/fixtures")
}

/// 一条合法的实机记录；负例在它上面改一处。
fn sample() -> String {
    [
        MARK,
        "case=ime-recover",
        "evidence=field",
        "arch=x86_64",
        "os=AirymaxOS 0.0.5",
        "build=2028.01.31",
        "when=2028-01-31T09:00:00Z",
        "host=lab-01",
        "term=linux",
        "cols=85",
        "rows=24",
        "observed=ime=recovering",
        "result=pass",
        END,
    ]
    .join("\n")
}

/// 合成一条覆盖用例；只用于覆盖统计，不冒充实机记录。
fn forged(case: &str, arch: &str) -> Record {
    let mut fields = BTreeMap::new();
    for (key, value) in [
        ("case", case),
        ("evidence", "field"),
        ("arch", arch),
        ("os", "AirymaxOS 0.0.5"),
        ("build", "test"),
        ("when", "2028-01-31T09:00:00Z"),
        ("host", "lab-01"),
        ("term", "linux"),
        ("cols", "85"),
        ("rows", "24"),
        ("observed", "synthetic"),
        ("result", "pass"),
    ] {
        fields.insert(key.to_owned(), value.to_owned());
    }
    Record { at: 1, fields }
}

#[test]
fn a_valid_field_record_passes() {
    let records = parse(&sample()).expect("解析");
    assert_eq!(records.len(), 1);
    check(&records[0]).expect("合法记录");
}

#[test]
fn the_shipped_fixtures_are_all_fixture_labelled() {
    let records = bundle(&fixtures()).expect("读夹具");
    assert_eq!(records.len(), 3, "夹具条数");
    for rec in &records {
        check(rec).expect("夹具必须是合法记录");
        assert_eq!(rec.word("evidence"), "fixture");
        assert!(rec.word("case").starts_with("fixture-"));
        assert_ne!(rec.word("result"), "fail", "夹具不得携带失败读数");
    }
}

#[test]
fn a_missing_marker_is_rejected() {
    let text = sample().replace(MARK, "AIRYMAX-TUI-FIELD/2");
    assert!(parse(&text).is_err());
}

#[test]
fn an_unterminated_record_is_rejected() {
    let text = sample().replace(END, "");
    assert!(parse(&text).is_err());
}

#[test]
fn an_unknown_key_is_rejected() {
    let text = sample().replace("observed=", "observer=");
    assert!(parse(&text).is_err());
}

#[test]
fn a_duplicate_key_is_rejected() {
    let text = sample().replace("result=pass", "arch=x86_64\nresult=pass");
    assert!(parse(&text).is_err());
}

#[test]
fn a_missing_required_key_is_rejected() {
    let text = sample().replace("term=linux\n", "");
    let records = parse(&text).expect("解析");
    assert!(check(&records[0]).is_err());
}

#[test]
fn a_field_record_needs_time_and_host() {
    for key in ["when=2028-01-31T09:00:00Z\n", "host=lab-01\n"] {
        let text = sample().replace(key, "");
        let records = parse(&text).expect("解析");
        assert!(check(&records[0]).is_err(), "缺少 {key} 应判负");
    }
}

#[test]
fn a_fixture_case_must_carry_the_prefix() {
    let text = sample()
        .replace("case=ime-recover", "case=fixture-ime-recover")
        .replace("evidence=field", "evidence=fixture")
        .replace("when=2028-01-31T09:00:00Z\n", "")
        .replace("host=lab-01\n", "");
    let records = parse(&text).expect("解析");
    check(&records[0]).expect("带前缀的夹具合法");

    let bare = text.replace("case=fixture-", "case=");
    let records = parse(&bare).expect("解析");
    assert!(check(&records[0]).is_err(), "夹具缺前缀应判负");
}

#[test]
fn an_unknown_arch_is_rejected() {
    let text = sample().replace("arch=x86_64", "arch=mips64");
    let records = parse(&text).expect("解析");
    assert!(check(&records[0]).is_err());
}

#[test]
fn a_zero_sized_terminal_is_rejected() {
    let text = sample().replace("cols=85", "cols=0");
    let records = parse(&text).expect("解析");
    assert!(check(&records[0]).is_err());
}

#[test]
fn a_truncated_digest_is_rejected() {
    let text = sample().replace("result=pass", "note=shot\nscreenshot=abc123\nresult=pass");
    let records = parse(&text).expect("解析");
    assert!(check(&records[0]).is_err());
}

#[test]
fn a_failing_result_fails_the_bundle() {
    let text = sample().replace("result=pass", "result=fail");
    let records = parse(&text).expect("解析");
    let issues = judge(&records);
    assert!(
        issues.iter().any(|line| line.contains("fail")),
        "{issues:?}"
    );
}

#[test]
fn coverage_needs_every_case_on_both_arches() {
    assert_eq!(missing(&[]).len(), RELEASE_ARCH.len() * CASES.len());
    let mut records = Vec::new();
    for arch in RELEASE_ARCH {
        for case in CASES {
            records.push(forged(case, arch));
        }
    }
    assert!(judge(&records).is_empty(), "{:?}", judge(&records));
}

#[test]
fn a_target_bundle_is_required_when_asked() {
    let Some(dir) = env::var_os("AIRYMAX_FIELD_DIR").map(PathBuf::from) else {
        assert!(
            env::var_os("AIRYMAX_FIELD_REQUIRE").is_none(),
            "要求实机取证，但 AIRYMAX_FIELD_DIR 未设置"
        );
        return;
    };
    let records = bundle(&dir).expect("读实机记录");
    let issues = judge(&records);
    assert!(issues.is_empty(), "实机取证不完整: {}", issues.join("; "));
}
