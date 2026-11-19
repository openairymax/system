// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// V-03 中文输入语料回归（QG-09）
//
// 语料同源冻结：sentences.tsv 由 words.tsv 的 3 个时间前缀、20 个主语与
// 50 个谓语（动词 + 宾语）交叉生成，共 3,000 句、12,000 个词。生成规则
// 固定，冻结之后只允许追加行、不允许修改既有行；生成器不入库，规则就是
// 唯一来源，审计时按规则重算应逐字节一致。
//
// 判据（QG-09 / AC-B6）：
//   · 语料 3,000 句，每句 4 词，词表能完整构建并加载成 AIRYIME1 词典；
//   · 逐词只按默认首选提交，首选字准确率 ≥ 95%，会话拒绝 0；
//   · 单次按键的候选刷新 p99 ≤ 50 ms（SPEC §5 的 slow 阈值），崩溃 0。
//
// 「首选字准确率」按词计：每个词是一次完整的拼音输入，用户按默认首选
// 提交。这是键盘输入唯一可判定的口径；按字拆分需要分词，切分噪声会盖住
// 候选排序的真实缺陷。
//
// SSoT: 0.0.5 方案 QG-09、F-06、AC-B6；boot-ime/SPEC.md §4、§5。
// ===========================================================================

//! 3,000 句冻结语料的首选字准确率与按键延迟回归。

use std::collections::HashSet;
use std::time::{Duration, Instant};

use boot_ime::{build, Dictionary, Effect, Entry, Frame, Msg, Session};

const WORDS: &str = include_str!("words.tsv");
const SENTENCES: &str = include_str!("sentences.tsv");

/// 会话标识；整轮语料共用一个会话，与真实连续输入一致。
const SESSION: &str = "v-03";

/// 冻结口径的句数；语料增删必须同时改这里并说明原因。
const EXPECTED: usize = 3000;

/// 首选字准确率下限（QG-09）。
const FLOOR: f64 = 0.95;

/// 单次按键的候选刷新预算（SPEC §5：超过即 `ime=slow`）。
const BUDGET: Duration = Duration::from_millis(50);

/// 一句语料：全拼序列、期望词序列、期望整句。
struct Case {
    pinyin: Vec<&'static str>,
    words: Vec<&'static str>,
    text: &'static str,
}

/// 冻结语料必须完整、不重复，且每个期望词都能在词表里找到同拼音同文本。
#[test]
fn corpus_is_frozen_at_three_thousand_sentences() {
    let cases = cases();
    assert_eq!(cases.len(), EXPECTED, "语料句数与冻结口径不符");

    let dict = dictionary();
    let mut seen = HashSet::new();
    for case in &cases {
        assert_eq!(case.pinyin.len(), 4, "句子按「前缀 主语 动词 宾语」组织");
        assert_eq!(case.words.len(), case.pinyin.len(), "全拼与词数不符");
        assert!(seen.insert(case.text), "语料不得重复：{}", case.text);
        for (pinyin, word) in case.pinyin.iter().zip(&case.words) {
            assert!(
                dict.entries()
                    .iter()
                    .any(|entry| entry.pinyin() == *pinyin && entry.text() == *word),
                "词表缺少 {pinyin}\t{word}"
            );
        }
    }
}

/// 首选字准确率与按键延迟；两者在同一遍语料上取样，避免结论来自不同的运行。
#[test]
fn first_choice_accuracy_and_latency_hold() {
    let dict = dictionary();
    let mut ime = Session::open(&dict, SESSION).expect("可开会话");
    let cases = cases();
    let (mut seq, mut total, mut correct, mut whole, mut errors) =
        (0u64, 0usize, 0usize, 0usize, 0usize);
    let mut samples: Vec<Duration> = Vec::new();
    let mut misses: Vec<String> = Vec::new();

    for case in &cases {
        let mut text = String::new();
        for (pinyin, want) in case.pinyin.iter().zip(&case.words) {
            let got = type_word(&mut ime, &mut seq, pinyin, &mut samples, &mut errors);
            total += 1;
            if got == *want {
                correct += 1;
            } else if misses.len() < 10 {
                misses.push(format!("{pinyin} 期望「{want}」实得「{got}」"));
            }
            text.push_str(&got);
        }
        if text == case.text {
            whole += 1;
        }
    }

    let accuracy = correct as f64 / total as f64;
    samples.sort_unstable();
    let p99 = samples[(samples.len() * 99) / 100];
    println!(
        "语料 {} 句 / {} 词：首选 {:.2}%（{}/{}），整句 {:.2}%（{}/{}），拒绝 {}，p99 {:.3} ms，最大 {:.3} ms",
        cases.len(),
        total,
        accuracy * 100.0,
        correct,
        total,
        whole as f64 / cases.len() as f64 * 100.0,
        whole,
        cases.len(),
        errors,
        p99.as_secs_f64() * 1e3,
        samples.last().map_or(0.0, |max| max.as_secs_f64() * 1e3),
    );

    assert!(
        accuracy >= FLOOR,
        "首选字准确率 {:.2}% 低于 {:.0}%：{:?}",
        accuracy * 100.0,
        FLOOR * 100.0,
        misses
    );
    assert_eq!(errors, 0, "会话拒绝了合法按键 {} 次", errors);
    assert!(
        p99 <= BUDGET,
        "按键 p99 {:.3} ms 超过 {} ms",
        p99.as_secs_f64() * 1e3,
        BUDGET.as_millis()
    );
}

/// 把一行全拼逐键送进会话，再按默认首选提交；返回提交文本。
///
/// 按键耗时逐次采样；会话拒绝（`apply` 返回错误）只计数不中断，让整轮
/// 语料能跑完并给出完整读数，而不是在第一个坏样本上 panic。
fn type_word(
    ime: &mut Session<'_>,
    seq: &mut u64,
    pinyin: &str,
    samples: &mut Vec<Duration>,
    errors: &mut usize,
) -> String {
    for ch in pinyin.chars() {
        *seq += 1;
        let start = Instant::now();
        let effect = ime.apply(&Frame::key(SESSION, *seq, ch.to_string()));
        samples.push(start.elapsed());
        if !matches!(effect, Ok(Effect::Composing { .. })) {
            *errors += 1;
        }
    }
    *seq += 1;
    match ime.apply(&Frame::new(SESSION, *seq, Msg::Commit)) {
        Ok(Effect::Commit(text)) => text,
        _ => {
            *errors += 1;
            String::new()
        }
    }
}

/// 解析冻结语料；三列以制表符分隔，空行与 `#` 注释跳过。
fn cases() -> Vec<Case> {
    let mut out = Vec::new();
    for (index, line) in SENTENCES.lines().enumerate() {
        let line = line.trim_end();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split('\t');
        let (Some(pinyin), Some(words), Some(text), None) =
            (fields.next(), fields.next(), fields.next(), fields.next())
        else {
            panic!("sentences.tsv 第 {} 行不是三列", index + 1);
        };
        out.push(Case {
            pinyin: pinyin.split(' ').collect(),
            words: words.split(' ').collect(),
            text,
        });
    }
    out
}

/// 由词表构建词典；任何一行不合法都让整份语料不可测。
fn dictionary() -> Dictionary {
    let mut entries = Vec::new();
    for (index, line) in WORDS.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split('\t');
        let (Some(pinyin), Some(text), Some(freq), None) =
            (fields.next(), fields.next(), fields.next(), fields.next())
        else {
            panic!("words.tsv 第 {} 行不是三列", index + 1);
        };
        let freq = freq
            .parse::<u16>()
            .unwrap_or_else(|_| panic!("words.tsv 第 {} 行词频不合法", index + 1));
        entries.push(Entry::new(pinyin, text, freq));
    }
    Dictionary::load(&build(&entries).expect("词表可构建")).expect("词表可加载")
}
