// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 主题
//
// 引导期只有一套配色，但它必须住在数据文件里：顶栏与状态栏的颜色属于
// 外观决策，不应该跟着绘制逻辑一起改版本。`themes/airy-dark.toml` 是唯一
// 来源，编译期嵌入，运行期不读磁盘——开机早期没有可靠的根文件系统，
// 主题文件读不到时不能变成黑屏。
//
// 解析是严格的：只认 `[bar]`、`[foot]` 两个分区与 `fg`/`bg`/`attrs` 三个键，
// 未知分区、未知键、越界色号、未知属性都判失败并带行号。静默忽略拼错的
// 键会让主题看起来「生效了一半」，比直接失败更难查。
//
// SSoT: 0.0.5 方案 §14.3。
// ===========================================================================

//! 引导期 TUI 的配色主题。

use core::fmt::Display;

use intent::Refusal;

use crate::screen::{Attr, Color, Style};

/// 编译期嵌入的主题正文。
const BUILTIN: &str = include_str!("../../../themes/airy-dark.toml");

/// 一套主题：只覆盖顶栏与状态栏，正文沿用默认样式。
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Theme {
    /// 顶栏。
    pub bar: Style,
    /// 状态栏。
    pub foot: Style,
}

/// 主题文件里的分区。
#[derive(Clone, Copy)]
enum Sect {
    /// 顶栏。
    Bar,
    /// 状态栏。
    Foot,
}

impl Theme {
    /// 内置主题；正文损坏属于构建缺陷，直接中止。
    pub fn builtin() -> Self {
        Self::parse(BUILTIN).expect("内置主题必须可解析")
    }

    /// 基础档主题：只用默认前景背景，保留加粗与反显。
    pub fn plain() -> Self {
        Self {
            bar: Style {
                attr: Attr::BOLD,
                ..Style::DEFAULT
            },
            foot: Style {
                attr: Attr::REV,
                ..Style::DEFAULT
            },
        }
    }

    /// 解析主题正文；行号从 1 起。
    pub fn parse(text: &str) -> Result<Self, Refusal> {
        let mut theme = Self::default();
        let mut sect = None;
        for (at, raw) in text.lines().enumerate() {
            let line = raw.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            sect = Some(step(&mut theme, sect, line, at)?);
        }
        Ok(theme)
    }
}

/// 处理一行，返回当前分区。
fn step(theme: &mut Theme, sect: Option<Sect>, line: &str, at: usize) -> Result<Sect, Refusal> {
    if let Some(name) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
        return sect_of(name.trim());
    }
    let sect = sect.ok_or_else(|| refuse_at(at, "键出现在任何分区之前"))?;
    let (key, val) = line
        .split_once('=')
        .ok_or_else(|| refuse_at(at, "不是「键 = 值」形式"))?;
    apply(theme, sect, key.trim(), val.trim(), at)?;
    Ok(sect)
}

/// 分区名到分区的映射。
fn sect_of(name: &str) -> Result<Sect, Refusal> {
    match name {
        "bar" => Ok(Sect::Bar),
        "foot" => Ok(Sect::Foot),
        other => Err(Refusal::invalid(format!("未知主题分区 {other}"))),
    }
}

/// 写入一个键。
fn apply(theme: &mut Theme, sect: Sect, key: &str, val: &str, at: usize) -> Result<(), Refusal> {
    let style = match sect {
        Sect::Bar => &mut theme.bar,
        Sect::Foot => &mut theme.foot,
    };
    match key {
        "fg" => style.fg = color_of(val, at)?,
        "bg" => style.bg = color_of(val, at)?,
        "attrs" => style.attr = attrs_of(val, at)?,
        _ => return Err(refuse_at(at, format!("未知主题键 {key}"))),
    }
    Ok(())
}

/// 解析颜色：`default` 或 0–255。
fn color_of(val: &str, at: usize) -> Result<Color, Refusal> {
    if val == "default" {
        return Ok(Color::Default);
    }
    match val.parse::<u8>() {
        Ok(idx) => Ok(Color::Idx(idx)),
        Err(_) => Err(refuse_at(at, format!("色号 {val} 不是 0–255"))),
    }
}

/// 解析属性集合；`none` 表示清空。
fn attrs_of(val: &str, at: usize) -> Result<Attr, Refusal> {
    let mut attr = Attr::NONE;
    for name in val.split(',').map(str::trim) {
        attr = attr.with(attr_of(name, at)?);
    }
    Ok(attr)
}

/// 单个属性名到属性位的映射。
fn attr_of(name: &str, at: usize) -> Result<Attr, Refusal> {
    match name {
        "none" => Ok(Attr::NONE),
        "bold" => Ok(Attr::BOLD),
        "dim" => Ok(Attr::DIM),
        "underline" => Ok(Attr::UNDER),
        "reverse" => Ok(Attr::REV),
        other => Err(refuse_at(at, format!("未知属性 {other}"))),
    }
}

/// 带行号的拒绝。
fn refuse_at(at: usize, what: impl Display) -> Refusal {
    Refusal::invalid(format!("主题第 {} 行：{}", at + 1, what))
}

#[cfg(test)]
mod tests {
    use super::Theme;
    use crate::screen::{Attr, Color};

    #[test]
    fn the_builtin_theme_paints_a_deep_blue_bar() {
        let theme = Theme::builtin();
        assert_eq!(theme.bar.bg, Color::Idx(17));
        assert_eq!(theme.bar.attr, Attr::BOLD);
    }

    #[test]
    fn the_builtin_theme_reverses_the_state_bar() {
        assert_eq!(Theme::builtin().foot.attr, Attr::REV);
    }

    #[test]
    fn keys_override_only_their_own_slot() {
        let theme = Theme::parse("[bar]\nfg = 3\n[foot]\nbg = 0\nattrs = none\n").unwrap();
        assert_eq!(theme.bar.fg, Color::Idx(3));
        assert_eq!(theme.foot.bg, Color::Idx(0));
        assert_eq!(theme.foot.attr, Attr::NONE);
    }

    #[test]
    fn an_unknown_key_is_refused_with_its_line() {
        let err = Theme::parse("[bar]\nbgo = 17\n").unwrap_err();
        assert!(err.detail().contains("第 2 行"), "{}", err.detail());
    }

    #[test]
    fn a_colour_outside_the_palette_is_refused() {
        assert!(Theme::parse("[bar]\nbg = 256\n").is_err());
        assert!(Theme::parse("[bar]\nbg = blue\n").is_err());
    }

    #[test]
    fn a_key_before_any_section_is_refused() {
        assert!(Theme::parse("bg = 1\n").is_err());
    }

    #[test]
    fn comments_and_blank_lines_are_ignored() {
        let theme = Theme::parse("# 注释\n\n[bar]\n\n  bg = 4  \n").unwrap();
        assert_eq!(theme.bar.bg, Color::Idx(4));
    }
}
