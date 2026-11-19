// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 审批链（root 预置批准文件）
//
// 高危动作执行前必须命中一条 root 投放的批准文件，否则拒绝。批准以
// 「argv 数组按 NUL 连接的 SHA-256」为准，不用命令行字符串：同一序列只有
// 一种解释，与 shell 的引号与展开规则无关。
//
// 目录与文件都视为不可信输入。任一文件属主不是约定属主、或 group/other
// 持有写位、或是符号链接、或解析不出来，整条链判不可信并拒绝——宁可误
// 拒，也不能把「被改过的文件」当成合法批准。
//
// 本版不做模式匹配（如 `dnf install *`），一条命令一个批准；交互式审批与
// 一次性凭据依赖 logind 会话，顺延到 dock 侧。
//
// SSoT: 0.0.5 §14.9；30-interfaces/11-input-ingress.md §5.1。
// ===========================================================================

//! 批准文件校验：目录扫描、属主与写位、摘要匹配与有效期。

use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(test)]
use std::os::unix::fs::PermissionsExt;
#[cfg(test)]
use std::path::PathBuf;

/// 批准文件目录；由安装脚本或 root 手工投放。
pub const DIR: &str = "/etc/airymaxos/system/tui/approvals.d";

/// 校验参数：约定属主与当前时刻。生产固定为 root 与系统时钟，用例可替换。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Rule {
    /// 批准文件与目录必须归属的 uid。
    pub owner: u32,
    /// 当前 Unix 时刻，用于判定 `expires`。
    pub now: u64,
}

impl Rule {
    /// 生产口径：属主为 root，时刻取系统时钟。
    pub fn root() -> Self {
        Self {
            owner: 0,
            now: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_or(0, |age| age.as_secs()),
        }
    }
}

/// 命中一条批准文件的结果；文件名进审计行。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Permit {
    /// 命中的批准文件名，形如 `<action>.conf`。
    pub file: String,
}

/// 拒绝原因；三种都映射到 `approval-required` 与 `-EPERM`，审计行按需区分。
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Refusal {
    /// 没有对应动作的批准文件，或摘要不匹配。
    Missing,
    /// `expires` 已过。
    Expired,
    /// 目录或文件不可信：属主、写位、类型或内容任一不满足。
    Insecure,
}

impl Refusal {
    /// 审计行里的拒绝原因，取值与 §14.9 一致。
    pub fn reason(self) -> &'static str {
        match self {
            Self::Missing => "no_approval",
            Self::Expired => "approval_expired",
            Self::Insecure => "approval_insecure",
        }
    }
}

/// 按生产路径校验一条请求；摘要由 [`crate::exec::digest`] 计算。
pub fn check(action: &str, digest: &str) -> Result<Permit, Refusal> {
    check_in(Path::new(DIR), &Rule::root(), action, digest)
}

/// 以给定目录与参数校验；用例走这里，生产走 [`check`]。
pub fn check_in(dir: &Path, rule: &Rule, action: &str, digest: &str) -> Result<Permit, Refusal> {
    let entry = scan(dir, rule, action)?.ok_or(Refusal::Missing)?;
    if entry.digest != digest {
        return Err(Refusal::Missing);
    }
    if entry.expires.is_some_and(|end| end < rule.now) {
        return Err(Refusal::Expired);
    }
    Ok(Permit {
        file: format!("{action}.conf"),
    })
}

/// 一条批准文件解析后的内容。
#[derive(Default)]
struct Entry {
    digest: String,
    expires: Option<u64>,
}

/// 扫描目录并校验整条链，返回目标动作的条目（不存在则 `None`）。
fn scan(dir: &Path, rule: &Rule, action: &str) -> Result<Option<Entry>, Refusal> {
    if !dir_ok(dir, rule)? {
        return Ok(None);
    }
    let mut found = None;
    for item in fs::read_dir(dir).map_err(|_| Refusal::Insecure)? {
        visit(
            &item.map_err(|_| Refusal::Insecure)?.path(),
            rule,
            action,
            &mut found,
        )?;
    }
    Ok(found)
}

/// 校验单个条目，并在名字对上动作时解析出条目内容。
fn visit(path: &Path, rule: &Rule, action: &str, found: &mut Option<Entry>) -> Result<(), Refusal> {
    let name = file_name(path)?;
    if !name.ends_with(".conf") || !file_ok(path, rule)? {
        return Err(Refusal::Insecure);
    }
    if name == format!("{action}.conf") {
        *found = Some(load(path)?);
    }
    Ok(())
}

/// 目录必须存在且可信；不存在返回 `Ok(false)`（尚无批准）。
fn dir_ok(dir: &Path, rule: &Rule) -> Result<bool, Refusal> {
    let meta = match fs::symlink_metadata(dir) {
        Ok(meta) => meta,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(_) => return Err(Refusal::Insecure),
    };
    let trust = meta.is_dir() && meta.uid() == rule.owner && meta.mode() & 0o022 == 0;
    if !trust {
        return Err(Refusal::Insecure);
    }
    Ok(true)
}

/// 单个条目必须是普通文件、约定属主、且 group/other 无写位。
fn file_ok(path: &Path, rule: &Rule) -> Result<bool, Refusal> {
    let meta = fs::symlink_metadata(path).map_err(|_| Refusal::Insecure)?;
    Ok(meta.is_file() && meta.uid() == rule.owner && meta.mode() & 0o022 == 0)
}

/// 取文件名的 UTF-8 形式；非 UTF-8 名字无法与动作对上，按不可信处理。
fn file_name(path: &Path) -> Result<String, Refusal> {
    path.file_name()
        .and_then(|name| name.to_str())
        .map(str::to_owned)
        .ok_or(Refusal::Insecure)
}

/// 解析批准文件；任何未知键、缺字段或格式错误都返回不可信。
fn load(path: &Path) -> Result<Entry, Refusal> {
    let text = fs::read_to_string(path).map_err(|_| Refusal::Insecure)?;
    let mut entry = Entry::default();
    for line in text.lines().map(str::trim).filter(|line| meaningful(line)) {
        fill(&mut entry, line)?;
    }
    if entry.digest.is_empty() {
        return Err(Refusal::Insecure);
    }
    Ok(entry)
}

/// 空行与 `#` 注释不参与解析。
fn meaningful(line: &str) -> bool {
    !line.is_empty() && !line.starts_with('#')
}

/// 解析一行 `键 = 值`；未知键即不可信。
fn fill(entry: &mut Entry, line: &str) -> Result<(), Refusal> {
    let (key, value) = line.split_once('=').ok_or(Refusal::Insecure)?;
    let value = value.trim();
    match key.trim() {
        "match" => entry.digest = match_of(value)?,
        "expires" => entry.expires = Some(value.parse().map_err(|_| Refusal::Insecure)?),
        _ => return Err(Refusal::Insecure),
    }
    Ok(())
}

/// 取 `sha256:<hex>` 里的摘要；长度或字符不合规即不可信。
fn match_of(value: &str) -> Result<String, Refusal> {
    let text = value.trim_matches('"');
    let hex = text.strip_prefix("sha256:").ok_or(Refusal::Insecure)?;
    let shaped = hex.len() == 64 && hex.bytes().all(|byte| byte.is_ascii_hexdigit());
    if !shaped {
        return Err(Refusal::Insecure);
    }
    Ok(hex.to_ascii_lowercase())
}

/// 用例目录：每次以进程号与名字区分，避免互踩。
#[cfg(test)]
fn stage(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("airy-approval-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("建用例目录");
    fs::set_permissions(&dir, fs::Permissions::from_mode(0o755)).expect("目录权限");
    dir
}

#[cfg(test)]
fn put(dir: &Path, name: &str, text: &str, mode: u32) {
    let path = dir.join(name);
    fs::write(&path, text).expect("写批准文件");
    fs::set_permissions(&path, fs::Permissions::from_mode(mode)).expect("文件权限");
}

#[cfg(test)]
fn rule(now: u64) -> Rule {
    Rule {
        owner: rustix::process::getuid().as_raw(),
        now,
    }
}

#[cfg(test)]
const GOOD: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matching_digest_grants_the_action() {
        let dir = stage("grant");
        put(
            &dir,
            "disk.grow.conf",
            &format!("match = \"sha256:{GOOD}\""),
            0o644,
        );
        let permit = check_in(&dir, &rule(100), "disk.grow", GOOD).expect("应命中");
        assert_eq!(permit.file, "disk.grow.conf");
        fs::remove_dir_all(&dir).expect("清理");
    }

    #[test]
    fn absent_or_different_digest_is_refused() {
        let dir = stage("missing");
        put(
            &dir,
            "disk.grow.conf",
            &format!("match = \"sha256:{GOOD}\""),
            0o644,
        );
        assert_eq!(
            check_in(&dir, &rule(100), "disk.resize", GOOD).err(),
            Some(Refusal::Missing),
            "没有对应动作的批准文件"
        );
        let other = "f".repeat(64);
        assert_eq!(
            check_in(&dir, &rule(100), "disk.grow", &other).err(),
            Some(Refusal::Missing),
            "摘要不匹配视同没有批准"
        );
        fs::remove_dir_all(&dir).expect("清理");
    }

    #[test]
    fn expired_approval_is_refused() {
        let dir = stage("expired");
        put(
            &dir,
            "disk.grow.conf",
            &format!("match = \"sha256:{GOOD}\"\nexpires = 500\n"),
            0o644,
        );
        assert!(
            check_in(&dir, &rule(500), "disk.grow", GOOD).is_ok(),
            "未过期"
        );
        assert_eq!(
            check_in(&dir, &rule(501), "disk.grow", GOOD).err(),
            Some(Refusal::Expired)
        );
        fs::remove_dir_all(&dir).expect("清理");
    }

    #[test]
    fn writable_or_foreign_files_poison_the_chain() {
        let dir = stage("insecure");
        put(
            &dir,
            "disk.grow.conf",
            &format!("match = \"sha256:{GOOD}\""),
            0o664,
        );
        assert_eq!(
            check_in(&dir, &rule(100), "disk.grow", GOOD).err(),
            Some(Refusal::Insecure),
            "group 写位即不可信"
        );
        fs::set_permissions(
            dir.join("disk.grow.conf"),
            fs::Permissions::from_mode(0o644),
        )
        .expect("恢复权限");
        let stranger = Rule {
            owner: rule(100).owner.wrapping_add(1),
            now: 100,
        };
        assert_eq!(
            check_in(&dir, &stranger, "disk.grow", GOOD).err(),
            Some(Refusal::Insecure),
            "属主不是约定用户即不可信"
        );
        fs::remove_dir_all(&dir).expect("清理");
    }

    #[test]
    fn broken_entries_are_insecure() {
        let dir = stage("broken");
        put(&dir, "disk.grow.conf", "digest = 1\n", 0o644);
        assert_eq!(
            check_in(&dir, &rule(100), "disk.grow", GOOD).err(),
            Some(Refusal::Insecure),
            "未知键"
        );
        put(&dir, "disk.grow.conf", "match = \"sha256:abc\"\n", 0o644);
        assert_eq!(
            check_in(&dir, &rule(100), "disk.grow", GOOD).err(),
            Some(Refusal::Insecure),
            "摘要长度不足"
        );
        put(&dir, "notes.txt", "", 0o644);
        fs::remove_file(dir.join("disk.grow.conf")).expect("删除");
        assert_eq!(
            check_in(&dir, &rule(100), "disk.grow", GOOD).err(),
            Some(Refusal::Insecure),
            "目录里出现非批准文件"
        );
        fs::remove_dir_all(&dir).expect("清理");
    }

    #[test]
    fn missing_directory_is_no_approval() {
        let dir = std::env::temp_dir().join(format!("airy-approval-{}-absent", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        assert_eq!(
            check_in(&dir, &rule(100), "disk.grow", GOOD).err(),
            Some(Refusal::Missing)
        );
    }

    #[test]
    fn reasons_match_the_audit_vocabulary() {
        assert_eq!(Refusal::Missing.reason(), "no_approval");
        assert_eq!(Refusal::Expired.reason(), "approval_expired");
        assert_eq!(Refusal::Insecure.reason(), "approval_insecure");
    }
}
