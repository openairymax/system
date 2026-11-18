// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// 执行面的入口白名单与请求模型
//
// 意图只能落到打包清单里的 9 个入口：名字不在表内即拒绝，不猜相近动作。
// 实参按 argv 原样传递，不经 shell，也不做任何字符串拼接。本片（A-02-01）
// 只钉住名单与请求形状；子进程建立、管道限长与审计留给后续片。
//
// 名字与入口放在同一张表的一行里，解析与反查都只读这张表，不另设一份
// 名字清单去对齐。
//
// SSoT: 30-interfaces/11-input-ingress.md §5.1；0.0.5 §14.9（F-04b）。
// ===========================================================================

//! 白名单入口与一次执行请求的形状。

use intent::TraceId;

/// 入口的安装目录；子进程拉起的绝对路径由它与入口名拼成。
pub const BIN_DIR: &str = "/usr/bin";

/// 白名单内的入口。
#[repr(usize)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Prog {
    /// 屏幕与键盘背光。
    Bright = 0,
    /// deb 现场转 RPM。
    Deb = 1,
    /// 分区、挂载与磁盘健康。
    Disk = 2,
    /// 本地推理服务。
    Model = 3,
    /// 本地包分派。
    Pkg = 4,
    /// 电源档位。
    Power = 5,
    /// 追踪与诊断。
    Probe = 6,
    /// 实时带宽与换页上限。
    RtCap = 7,
    /// 系统更新。
    Update = 8,
}

/// 名字与入口的一一对应；行序即判别值，`airy-rt-lat.c` 是源文件不是入口。
pub const PROGRAMS: [(&str, Prog); 9] = [
    ("airy-bright", Prog::Bright),
    ("airy-deb", Prog::Deb),
    ("airy-disk", Prog::Disk),
    ("airy-model", Prog::Model),
    ("airy-pkg", Prog::Pkg),
    ("airy-power", Prog::Power),
    ("airy-probe", Prog::Probe),
    ("airy-rt-cap", Prog::RtCap),
    ("airy-update", Prog::Update),
];

impl Prog {
    /// 入口名，与打包清单逐条对应。
    pub fn name(self) -> &'static str {
        PROGRAMS[self as usize].0
    }

    /// 按名字匹配白名单；不在表内返回 `None`，调用方据此拒绝。
    pub fn parse(name: &str) -> Option<Self> {
        PROGRAMS
            .iter()
            .find(|(item, _)| *item == name)
            .map(|(_, prog)| *prog)
    }
}

/// 一次执行请求；实参已由路由层按动作定义生成，执行面不再解释语义。
#[derive(Clone, Debug)]
pub struct Request {
    /// 白名单入口。
    pub prog: Prog,
    /// 实参；原样传给子进程，不解释、不拼接、不经 shell。
    pub args: Vec<String>,
    /// 追踪号；失败与审计行都要带它。
    pub trace: TraceId,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_rows_match_their_discriminants() {
        for (row, (_, prog)) in PROGRAMS.iter().enumerate() {
            assert_eq!(*prog as usize, row, "{prog:?}");
            assert_eq!(prog.name(), PROGRAMS[row].0, "{prog:?}");
        }
    }

    #[test]
    fn names_round_trip_through_the_whitelist() {
        for (name, prog) in PROGRAMS {
            assert_eq!(Prog::parse(name), Some(prog), "{name}");
        }
    }

    #[test]
    fn names_outside_the_whitelist_are_refused() {
        for name in ["sh", "airy-rt-lat", "airy-disk ", "/usr/bin/airy-disk"] {
            assert_eq!(Prog::parse(name), None, "{name:?}");
        }
    }
}
