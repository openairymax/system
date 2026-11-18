// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// AirymaxOS 语音识别网关（asr-gateway）
//
// 承载模型可用性探测、识别会话与「模型缺失即显式 unavailable」的降级
// 语义。N-04 先落地模型清单与识别结果两层：前者决定通道是否可用，后者
// 决定结果能否进入可编辑缓冲区。采集设备与进程侧 IPC 随后续切片补齐。
// ===========================================================================

//! 引导期 TUI 语音通路的公共接口。

mod asr;
mod capture;

use core::fmt;

pub use asr::{AudioRef, Availability, Transcript};
pub use capture::{Manifest, Model, MANIFEST};

/// 网关错误；每一种都对应状态栏上的确定取值。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AsrError {
    /// 清单或模型文件读不出来；表现为 `asr=unavailable`。
    Io,
    /// 清单不是合法 JSON 或缺字段。
    Json,
    /// 字段存在但取值不合法。
    Field,
    /// 实物字节数与清单不符。
    Size,
    /// 实物摘要与清单不符。
    Digest,
    /// 识别结果为空；表现为 `asr=empty`。
    Empty,
}

impl fmt::Display for AsrError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::Io => "清单或模型不可读",
            Self::Json => "清单不是合法 JSON",
            Self::Field => "清单字段取值非法",
            Self::Size => "模型字节数与清单不符",
            Self::Digest => "模型摘要与清单不符",
            Self::Empty => "识别结果为空",
        };
        f.write_str(text)
    }
}

impl std::error::Error for AsrError {}
