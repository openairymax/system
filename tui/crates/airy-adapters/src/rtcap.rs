// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// airy-rt-cap matrix --json 的消费侧类型
//
// 实时能力不是布尔值：八项条件各自独立，缺一项对应一种不同的症状。因此
// 矩阵按项上报，flag 三态里 unknown 表示「这次读不到」，不能折叠成 no——
// 折叠会让运维去换内核，而实际问题在挂载与权限上。
//
// pending 是生产侧按 no 汇总出来的名单，这里照收不重算：消费侧重算迟早
// 与生产侧口径分叉。grade 与 flag 是稳定 token，逐字声明，不做前缀改写。
// 实测样张见 tests/golden/rtcap-matrix.json。
// ===========================================================================

//! `airy-rt-cap matrix --json` 的消费侧类型。

use intent::Refusal;
use serde::Deserialize;

use crate::decode;

/// 一次完整探测的结果。
#[derive(Debug, Deserialize)]
pub struct Matrix {
    /// 整机实时等级。
    pub grade: Grade,
    /// 明确缺失的条件名；unknown 不计入。
    pub pending: Vec<String>,
    /// 八项条件的探测结论。
    pub caps: Vec<Cap>,
}

/// 整机实时等级。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Grade {
    /// PREEMPT_RT、带宽与组记账均就绪。
    Ready,
    /// 实时类可用但内核不是 PREEMPT_RT。
    Soft,
    /// 实时带宽不可用或未暴露接口。
    None,
}

/// 单项标志。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Flag {
    /// 条件成立。
    Yes,
    /// 条件不成立。
    No,
    /// 这次读不到，不得当作不支持。
    Unknown,
}

/// 一项能力的探测结论。
#[derive(Debug, Deserialize)]
pub struct Cap {
    /// 条件名，与生产侧常量逐字一致。
    pub name: String,
    /// 三态标志。
    pub flag: Flag,
    /// 判定依据或缺失原因。
    pub detail: String,
}

impl Matrix {
    /// 解析 matrix 载荷；字段与枚举不受支持即拒绝。
    pub fn parse(bytes: &[u8]) -> Result<Self, Refusal> {
        decode(bytes)
    }
}
