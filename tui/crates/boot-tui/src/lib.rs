// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// AirymaxOS 引导期 TUI 会话层（boot-tui）
//
// 承载引导期 TUI 的终端初始化、帧循环、版面与外设状态面。公开面按
// 任务片逐片填入：U-01 起为终端与帧循环，U-05 起为状态面消费，
// U-09／U-10 为会话选择与模式切换的入口。
//
// 输入事件层（N-01／N-02）已落地键码、转义解码与按键事件源；屏幕单元
// 编码、字符网格、脏行绘制、终端显示面、帧循环与二进制入口（U-01）本片
// 落地，状态面随后续片填入。
// ===========================================================================

//! 引导期 TUI 会话层的公共接口。

pub mod degrade;
pub mod dialog;
pub mod display;
pub mod frame;
pub mod input;
pub mod keymap;
pub mod layout;
pub mod nav;
pub mod paint;
pub mod palette;
pub mod recover;
pub mod screen;
pub mod scrollback;
pub mod session;
pub mod session_store;
pub mod status;
pub mod theme;
pub mod trace_view;
pub mod wcwidth;
