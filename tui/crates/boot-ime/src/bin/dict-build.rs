// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 词典构建器
//
// 把人工维护的 TSV 词表编译成 AIRYIME1。构建是离线步骤：发行时词典随
// 包分发，机器上没有编译器依赖。格式细节由 dict.rs 与 SPEC.md 共同定义，
// 这里只负责读文本、报行号、写文件。
//
// TSV 每行三列：全拼、文本、词频；空行与 `#` 开头的注释行跳过。
// ===========================================================================

//! 把 TSV 词表编译成 AIRYIME1 词典的离线工具。

use std::env;
use std::fs;
use std::process::ExitCode;

use boot_ime::dict::{build, Entry};

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let (Some(input), Some(output)) = (args.next(), args.next()) else {
        usage();
        return ExitCode::from(2);
    };
    match run(&input, &output) {
        Ok(count) => {
            eprintln!("dict-build: {count} 词条 -> {output}");
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("dict-build: {error}");
            ExitCode::from(2)
        }
    }
}

/// 读取、构建、写出；返回写入的词条数。
fn run(input: &str, output: &str) -> Result<usize, String> {
    let text = fs::read_to_string(input).map_err(|error| format!("{input}: {error}"))?;
    let entries = parse(&text)?;
    let bytes = build(&entries).map_err(|error| error.to_string())?;
    fs::write(output, bytes).map_err(|error| format!("{output}: {error}"))?;
    Ok(entries.len())
}

/// 逐行解析 TSV；任何一行不合法即整体失败，带行号。
fn parse(text: &str) -> Result<Vec<Entry>, String> {
    let mut entries = Vec::new();
    for (index, line) in text.lines().enumerate() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let mut fields = line.split('\t');
        let (Some(pinyin), Some(word), Some(freq)) = (fields.next(), fields.next(), fields.next())
        else {
            return Err(format!("第 {} 行缺少字段", index + 1));
        };
        let freq: u16 = freq
            .trim()
            .parse()
            .map_err(|_| format!("第 {} 行词频不合法", index + 1))?;
        entries.push(Entry::new(pinyin, word, freq));
    }
    Ok(entries)
}

/// 用法提示；参数不足时不猜默认路径。
fn usage() {
    eprintln!("用法: dict-build <words.tsv> <out.airyime>");
}
