// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 状态面
//
// 状态面只回答两个问题：取证面能不能读、实时能力到哪一级。两条都来自
// 已冻结的 `--json` 契约，本模块只做「起进程、收 stdout、交给适配层」，
// 不重算任何判据——重算就会与生产侧口径分叉。
//
// 失败必须写在脸上。命令不存在、退出码非零、输出不合契约，都是状态面
// 的一行文字，而不是被吞掉后照样显示绿色：开机时看不到原因，用户只能
// 看到「一切正常」的假象，这比直接报错更难查。
//
// 起进程在主线程之外：探针在异常机器上可能长时间不回，帧循环不能跟着
// 停摆。结果通过通道回传，读到之前状态面显示「读取中」。
//
// SSoT: 0.0.5 方案 §2.3 四个冻结契约、§14 状态面。
// ===========================================================================

//! 状态面：取证面与实时能力的只读摘要。

use std::io;
use std::process::Command;

use airy_adapters::probe::{Report, State};
use airy_adapters::rtcap::{Flag, Grade, Matrix};
use intent::Refusal;

/// 一个状态项。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Item {
    /// 项名。
    pub name: &'static str,
    /// 结论或失败原因。
    pub text: String,
}

impl Item {
    /// 渲染成一行。
    pub fn line(&self) -> String {
        format!("{}  {}", self.name, self.text)
    }
}

/// 一帧状态面的内容。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Face {
    /// 取证面。
    pub probe: Item,
    /// 实时能力。
    pub rt: Item,
}

/// 读取两条状态；各自失败互不影响。
pub fn read() -> Face {
    Face {
        probe: item("探针", probe_text()),
        rt: item("实时", rt_text()),
    }
}

/// 整面都无法读取时的兜底。
pub fn failed(why: &str) -> Face {
    let text = why.to_owned();
    Face {
        probe: Item {
            name: "探针",
            text: text.clone(),
        },
        rt: Item {
            name: "实时", text
        },
    }
}

/// 把一次读取折成一个状态项。
fn item(name: &'static str, outcome: Result<String, Refusal>) -> Item {
    Item {
        name,
        text: match outcome {
            Ok(text) => text,
            Err(err) => err.detail().to_owned(),
        },
    }
}

/// 取证面摘要。
fn probe_text() -> Result<String, Refusal> {
    let report = Report::parse(&run("airy-probe", &["status", "--json"])?)?;
    let open = report.channels.iter().filter(|chan| chan.writable).count();
    Ok(format!(
        "{} · 事件 {}/{} · 通道 {}/{} · 见证{}",
        probe_word(report.state),
        report.readable,
        report.total,
        open,
        report.channels.len(),
        yes_no(report.witnessed),
    ))
}

/// 实时能力摘要。
fn rt_text() -> Result<String, Refusal> {
    let matrix = Matrix::parse(&run("airy-rt-cap", &["matrix", "--json"])?)?;
    let ready = matrix
        .caps
        .iter()
        .filter(|cap| cap.flag == Flag::Yes)
        .count();
    Ok(format!(
        "{} · 就绪 {}/{} · 缺 {}",
        grade_word(matrix.grade),
        ready,
        matrix.caps.len(),
        matrix.pending.len(),
    ))
}

/// 取证面结论的词。
fn probe_word(state: State) -> &'static str {
    match state {
        State::Ok => "正常",
        State::Partial => "部分",
        State::Blocked => "不可用",
    }
}

/// 实时等级的词。
fn grade_word(grade: Grade) -> &'static str {
    match grade {
        Grade::Ready => "完整",
        Grade::Soft => "软实时",
        Grade::None => "不可用",
    }
}

/// 布尔值的词。
fn yes_no(value: bool) -> &'static str {
    if value {
        "是"
    } else {
        "否"
    }
}

/// 执行一条只读契约命令并收下 stdout。
fn run(bin: &str, args: &[&str]) -> Result<Vec<u8>, Refusal> {
    let out = Command::new(bin).args(args).output().map_err(|err| {
        if err.kind() == io::ErrorKind::NotFound {
            Refusal::invalid(format!("{bin} 未安装"))
        } else {
            Refusal::invalid(format!("{bin} 无法执行：{err}"))
        }
    })?;
    if !out.status.success() {
        return Err(Refusal::invalid(format!(
            "{bin} 退出码 {}",
            out.status.code().unwrap_or(-1)
        )));
    }
    Ok(out.stdout)
}

#[cfg(test)]
mod tests {
    use super::{grade_word, item, probe_word, run, yes_no};
    use airy_adapters::probe::State;
    use airy_adapters::rtcap::Grade;
    use intent::Refusal;

    #[test]
    fn a_failed_read_becomes_text_instead_of_vanishing() {
        let entry = item("探针", Err(Refusal::invalid("airy-probe 未安装")));
        assert_eq!(entry.line(), "探针  airy-probe 未安装");
    }

    #[test]
    fn probe_states_have_distinct_words() {
        assert_eq!(probe_word(State::Ok), "正常");
        assert_eq!(probe_word(State::Partial), "部分");
        assert_eq!(probe_word(State::Blocked), "不可用");
    }

    #[test]
    fn grades_have_distinct_words() {
        assert_eq!(grade_word(Grade::Ready), "完整");
        assert_eq!(grade_word(Grade::Soft), "软实时");
        assert_eq!(grade_word(Grade::None), "不可用");
    }

    #[test]
    fn yes_and_no_stay_short() {
        assert_eq!(yes_no(true), "是");
        assert_eq!(yes_no(false), "否");
    }

    #[test]
    fn a_missing_command_is_reported_as_not_installed() {
        let err = run("airy-definitely-absent", &["status", "--json"]).unwrap_err();
        assert!(err.detail().contains("未安装"), "{}", err.detail());
    }

    #[test]
    fn a_non_zero_exit_is_reported_with_its_code() {
        let err = run("sh", &["-c", "exit 3"]).unwrap_err();
        assert!(err.detail().contains("退出码 3"), "{}", err.detail());
    }
}
