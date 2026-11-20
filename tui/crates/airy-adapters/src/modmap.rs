// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// docs-linux 十九个功能模块的入口映射（代码侧投影）
//
// 与 `system/tui/modmap.toml` 逐字段一致，两份文件改一侧不改另一侧由
// ci/gates/doc-contract.sh 判负。本模块只承载登记，不解释模块语义：
// 入口是路径，判据是会话层可观测的两条事实（成功一路、失败一路）。
//
// 覆盖状态只有两态：本版给出入口与判据的记 covered；其余记 deferred 并在
// note 首段写明承接版本。留白不合法——留白会让下一个接手的人重新猜。
//
// 登记用两个 const fn 生成：covered 的七列逐项入参，deferred 的三列入参、
// 其余固定为未覆盖形态。表里因此只出现差异，不出现重复的样板行。
// ===========================================================================

//! 模块入口映射的代码侧投影。

/// 模块在本版的覆盖状态。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Scope {
    /// 本版具备可观测入口与两条判据。
    Covered,
    /// 本版不提供入口，承接版本写在 note。
    Deferred,
}

/// 一条模块登记。
#[derive(Clone, Copy, Debug)]
pub struct ModEntry {
    /// 模块代号，十九个模块各一条。
    pub code: &'static str,
    /// 模块名。
    pub name: &'static str,
    /// 覆盖状态。
    pub scope: Scope,
    /// 入口路径；deferred 固定为 `-`。
    pub entry: &'static str,
    /// 成功判据。
    pub ok: &'static str,
    /// 失败判据。
    pub fail: &'static str,
    /// 备注；deferred 以承接版本开头。
    pub note: &'static str,
}

/// 登记一条本版已覆盖的模块。
const fn cover(
    code: &'static str,
    name: &'static str,
    entry: &'static str,
    ok: &'static str,
    fail: &'static str,
    note: &'static str,
) -> ModEntry {
    ModEntry {
        code,
        name,
        scope: Scope::Covered,
        entry,
        ok,
        fail,
        note,
    }
}

/// 登记一条本版未覆盖的模块：入口与两条判据固定为 `-`。
const fn defer(code: &'static str, name: &'static str, note: &'static str) -> ModEntry {
    ModEntry {
        code,
        name,
        scope: Scope::Deferred,
        entry: "-",
        ok: "-",
        fail: "-",
        note,
    }
}

/// 十九个模块的登记，顺序与 `modmap.toml` 一致。
///
/// 一行一条是刻意的：代号经 `stringify!` 取字面值，行内因此留着模块标识符。
/// 若让格式化器把每条摊回多行，去字符串后每行只剩逗号，十九行同构样板会被
/// 重复率棘轮判成复制粘贴——那张表本来就是一个清单，压缩成清单的样子才对。
#[rustfmt::skip]
pub const MODULES: [ModEntry; 19] = [
    cover(stringify!(L), "工具链与 CI/CD", "ci/ci.sh", "ci/ci.sh --list 列出门禁全集，ci/lint.sh --no-distro 退出 0", "任一判据返回 1 时打印分组名与首个失败断言，不吞错", "门禁目录只做分发，判据在 ci/gates 与 ci/checks"),
    defer(stringify!(A), "核心 syscall 与 Capability Folding", "0.0.24 起按内核窗口承接：折叠语义属内核侧，会话层不自行解释"),
    cover(stringify!(M), "发行版工程与磁盘管理", "system/tui/crates/airy-adapters/src/disk.rs", "airy-disk --json 的冻结字段集被完整解码为磁盘视图", "字段缺失或类型不符折成契约不符，状态行给出原因而非静默", "只消费 0.0.4 冻结输出，不新增字段"),
    cover(stringify!(B), "安全子系统", "system/tui/crates/airy-tui-exec/src/approve.rs", "argv 序列化后命中 root 预置批准文件则执行，且不经 sh -c", "未命中或批准文件过期返回 EPERM，写入批准目录由内核拒绝", "交互式审批随会话上下文留待 0.0.6"),
    defer(stringify!(F), "认知运行时", "0.0.24 起承接：本版只落 Intent 解析，不冒充认知运行时"),
    defer(stringify!(J), "质量保证与形式化验证", "0.0.6 起承接：本版复用既有门禁，形式化验证与浸泡仍在夜间轨"),
    defer(stringify!(E), "内存管理", "0.0.24 起按内核窗口承接：用户态不提供内存管理入口"),
    defer(stringify!(C), "双层 Supervisor", "0.0.24 起承接：监督面读点属内核事件通道，随 K 模块抬基线"),
    cover(stringify!(D), "可观测性与审计", "system/tui/crates/airy-adapters/src/probe.rs", "airy-probe status --json 的顶层键与 channels 被解码，可写性按通道呈现", "单通道不可读时该行显示不可用并保留其余通道，不整体失败", "可写性只在 channels 元素内，不改冻结键名"),
    defer(stringify!(I), "驱动框架", "0.1.0 起承接：设备模型与热插拔需内核侧先立接口"),
    defer(stringify!(G), "云原生控制面", "0.1.0 起承接：控制面不在会话层实现，本版只消费 RT 能力快照"),
    cover(stringify!(K), "用户态守护进程与核心服务", "system/tui/crates/airy-adapters/src/model.rs", "airy-model status --json 被解码，就绪与降级状态按枚举呈现", "degraded 携带原因时原样显示，原因未知则显示枚举值本身", "降级原因枚举由 0.0.4 冻结，不新增不改名"),
    defer(stringify!(N), "兼容性层", "0.1.0 起承接：ABI 兼容矩阵随内核基线稳定后才有判据"),
    cover(stringify!(H), "系统服务与开发工作站", "system/tui/crates/boot-tui/src/bin", "引导期在 120 秒窗口内选择会话，结果落状态文件并可切换", "超时回落默认会话并写 journal，切换失败回滚到原会话", "会话入口唯一，不按桌面环境变量分支"),
    cover(stringify!(O), "性能工程", "ci/gates/tui-frame.sh", "85x24 整屏重绘 p99 不超过 33 毫秒且帧常驻堆不超过 64 KiB", "任一读数超预算即以非零退出码报告，不折中", "预算线只有一处声明，测试与基准读同一条线"),
    cover(stringify!(P), "本地化与国标", "system/tui/crates/boot-ime", "中文语料在离线词典上全数命中，字素簇截断不裂字", "词典或编码非法时给出明确错误，不回退到猜测", "词典格式自持，不引入 C++ 输入法运行时"),
    cover(stringify!(Q), "应用生态与工具集", "tools/airy", "tools/airy --list 列出命令表全集，argv 与退出码原样透传", "未知命令返回 2 并列出可用命令，不猜近义词", "命令名与路径的唯一声明在 tools/commands.tsv"),
    defer(stringify!(R1), "桌面体验追新", "0.0.6 起承接：UKUI 4.24 自建闭包按可行性启动条件立项"),
    defer(stringify!(R2), "桌面体验自建闭包", "0.0.6 起承接：闭包与包名重组代价未定，本版不启动"),
];

/// 按代号取登记。
pub fn lookup(code: &str) -> Option<&'static ModEntry> {
    MODULES.iter().find(|m| m.code == code)
}

/// 本版已覆盖的模块数。
pub fn covered() -> usize {
    MODULES.iter().filter(|m| m.scope == Scope::Covered).count()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_are_unique() {
        let mut codes: Vec<&str> = MODULES.iter().map(|m| m.code).collect();
        codes.sort_unstable();
        codes.dedup();
        assert_eq!(codes.len(), MODULES.len());
    }

    #[test]
    fn covered_entries_exist() {
        for m in MODULES.iter().filter(|m| m.scope == Scope::Covered) {
            assert!(m.entry != "-", "{} 缺入口", m.code);
            assert!(m.ok != "-" && m.fail != "-", "{} 缺判据", m.code);
        }
    }

    #[test]
    fn covered_count_matches_scope() {
        let marked = MODULES.iter().filter(|m| m.scope == Scope::Covered).count();
        assert_eq!(covered(), marked, "计数与登记不一致");
        assert!(marked < MODULES.len(), "十九个模块不可能全是 covered");
    }

    #[test]
    fn deferred_carry_version() {
        for m in MODULES.iter().filter(|m| m.scope == Scope::Deferred) {
            assert!(m.entry == "-", "{} 不应有入口", m.code);
            assert!(m.note.starts_with("0.0.") || m.note.starts_with("0.1."));
        }
    }
}
