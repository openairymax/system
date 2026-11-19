// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 终端能力降级
//
// 引导期界面不能假设终端一定支持 256 色。绑定到 tty1 时通常是 Linux 控制台
// （`TERM=linux`），它只认基础 SGR；这时如果继续发 `38;5;17` 之类的扩展
// 色，轻则显示错色，重则被终端当成未知序列。这里把「配色档」做成显式
// 判定：认得的彩色终端走内置主题，控制台、哑终端与没有 TERM 的环境退回
// 默认前景背景，只保留加粗与反显。
//
// 判定只看 TERM，不探测图形硬件：引导期 TUI 是纯文本渲染，没有 GPU 也应
// 当正常工作，这条路径不以任何 DRM 节点为前提。未知的非空 TERM 按彩色
// 处理，避免新终端被误判成单色。
//
// 非 tty 与极小终端的判定不在这里：前者由显示面在写第一个字节之前拒绝，
// 后者由帧循环退化成一行提示。三者共同构成 §14.13 的降级矩阵。
//
// SSoT: 0.0.5 方案 §14.3（主题）、§14.13（降级矩阵）。
// ===========================================================================

//! 终端能力降级：配色档与主题选择。

use crate::theme::Theme;

/// 终端能承受的配色档。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Palette {
    /// 支持 256 色索引。
    Rich,
    /// 只支持基础 SGR，使用默认前景背景。
    Basic,
}

/// 按 TERM 判定配色档；没有 TERM 时按控制台处理。
pub fn palette(term: Option<&str>) -> Palette {
    match term {
        Some("linux" | "dumb" | "vt100" | "cons25") => Palette::Basic,
        Some(_) => Palette::Rich,
        None => Palette::Basic,
    }
}

/// 按配色档取主题：基础档不发送扩展色。
pub fn theme(term: Option<&str>) -> Theme {
    match palette(term) {
        Palette::Rich => Theme::builtin(),
        Palette::Basic => Theme::plain(),
    }
}
