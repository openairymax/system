// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// AirymaxOS 意图执行面（airy-tui-exec）
//
// 承载意图到进程的落地：argv 数组直传 execve、批准文件匹配、审计字段
// 落盘。不提供 shell 拼接路径。公开面按片填入：A-02-01 先钉入口白名单
// 与结果形状，子进程与审计随后补。
// ===========================================================================

//! 意图执行面的公共接口。

pub mod approve;
pub mod exec;
pub mod normalize;
pub mod sandbox;
