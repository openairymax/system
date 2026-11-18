// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// ===========================================================================
// airy-disk --json 的消费侧类型
//
// 契约由 0.0.4 的 airy-disk 钉住：字段名、层级与枚举 token 以目标机实测
// 输出为准，本层只声明消费侧类型，不改写字段、不补齐契约外语义。
// 实测样张在 tests/golden/disk-*.json：status 与 layout/health 取自目标
// 机运行；本机没有带分区的磁盘，parts[] 与 install 的样张由 0.0.4 的
// service 序列化器直接产出（模型与记录模块共用同一 dump）。
// SSoT: 0.0.5 方案 §2.3、30-interfaces 的磁盘契约表。
// ===========================================================================

//! `airy-disk --json` 三个子命令的消费侧类型。

use intent::Refusal;
use serde::Deserialize;

use crate::decode;

/// 本版支持的契约版本；与 0.0.4 的 `model.SCHEMA_VERSION` 一致。
pub const SCHEMA_VERSION: u32 = 1;

/// `airy-disk status --json` 的顶层对象。
#[derive(Debug, Deserialize)]
pub struct Status {
    /// 契约版本，当前为 1。
    pub schema: u32,
    /// 拓扑分类。
    pub kind: Topo,
    /// 探测到的磁盘，按内核枚举顺序。
    pub disks: Vec<Disk>,
    /// 与安装期选盘记录的比对结果。
    pub install: Install,
}

/// 拓扑分类。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum Topo {
    /// 没有可用磁盘。
    Empty,
    /// 单盘。
    Single,
    /// 多盘。
    Multi,
    /// 双系统。
    DualBoot,
}

/// 单块磁盘。
#[derive(Debug, Deserialize)]
pub struct Disk {
    /// 内核名，如 `nvme0n1`。
    pub name: String,
    /// 容量，字节。
    pub size: u64,
    /// 承载总线。
    pub bus: Bus,
    /// 分区表类型。
    pub table: Table,
    /// 是否只读。
    pub ro: bool,
    /// 是否可移动。
    pub removable: bool,
    /// 型号；设备未上报时为空串。
    pub model: String,
    /// 序列号；设备未上报时为空串。
    pub serial: String,
    /// 分区，按分区号升序。
    pub parts: Vec<Part>,
}

/// 块设备承载总线。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Bus {
    /// NVMe。
    Nvme,
    /// SATA。
    Sata,
    /// SCSI。
    Scsi,
    /// USB。
    Usb,
    /// eMMC／SD。
    Mmc,
    /// VirtIO。
    Virtio,
    /// 无法归类。
    Other,
}

/// 分区表类型，token 与 `lsblk` 的 `pttype` 一致。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub enum Table {
    /// 无分区表。
    #[serde(rename = "")]
    None,
    /// GPT。
    #[serde(rename = "gpt")]
    Gpt,
    /// MBR（token 为 `dos`）。
    #[serde(rename = "dos")]
    Dos,
    /// 无法识别。
    #[serde(rename = "unknown")]
    Unknown,
}

/// 分区文件系统，token 与 `lsblk` 的 `fstype` 一致。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
pub enum Fs {
    /// 未格式化。
    #[serde(rename = "")]
    None,
    /// ext4。
    #[serde(rename = "ext4")]
    Ext4,
    /// XFS。
    #[serde(rename = "xfs")]
    Xfs,
    /// Btrfs。
    #[serde(rename = "btrfs")]
    Btrfs,
    /// FAT12/16/32。
    #[serde(rename = "vfat")]
    Vfat,
    /// NTFS。
    #[serde(rename = "ntfs")]
    Ntfs,
    /// 交换区。
    #[serde(rename = "swap")]
    Swap,
    /// 无法归类。
    #[serde(rename = "other")]
    Other,
}

/// 分区在布局中的角色。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    /// 系统分区。
    System,
    /// 数据分区。
    Data,
    /// EFI 系统分区。
    Esp,
    /// 交换分区。
    Swap,
    /// 布局外的分区。
    Foreign,
}

/// 单个分区。
#[derive(Debug, Deserialize)]
pub struct Part {
    /// 内核名，如 `nvme0n1p2`。
    pub name: String,
    /// 容量，字节。
    pub size: u64,
    /// 文件系统。
    pub fs: Fs,
    /// 挂载点；未挂载为 null。
    pub mount: Option<String>,
    /// 卷标；未设置为 null。
    pub label: Option<String>,
    /// 分区 UUID；未上报为 null。
    pub partuuid: Option<String>,
    /// 布局角色。
    pub role: Role,
}

/// 安装期选盘记录与现场的比对结果。
#[derive(Debug, Deserialize)]
pub struct Install {
    /// 记录本身的状态。
    pub state: InstallState,
    /// 记录与现场的关系。
    pub verdict: Verdict,
    /// 不一致的字段名，取值 `disk`、`size`、`bus`。
    pub diff: Vec<String>,
    /// 安装时选中的盘名。
    pub disk: String,
    /// 安装时记录的容量，字节。
    pub bytes: u64,
    /// 安装时记录的总线；未知记为 `-`，比对时跳过。
    pub bus: String,
    /// 安装时的固件类型。
    pub firmware: String,
    /// 安装时的布局。
    pub layout: String,
    /// 安装介质。
    pub medium: String,
}

/// 选盘记录本身的状态。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum InstallState {
    /// 记录文件不存在。
    Absent,
    /// 记录存在但不可用。
    Invalid,
    /// 记录可用。
    Ok,
}

/// 记录与现场的关系；缺失、损坏与不一致各占一个 token。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum Verdict {
    /// 一致。
    Match,
    /// 不一致，明细见 `diff`。
    Mismatch,
    /// 没有记录。
    Absent,
    /// 记录不可用。
    Invalid,
}

/// `airy-disk layout --json` 的布局判定。
#[derive(Debug, Deserialize)]
pub struct Layout {
    /// 布局分类；缺任一侧时为 `none`。
    pub kind: LayoutKind,
    /// 判定结论。
    pub verdict: LayoutState,
    /// 承载 `/` 的磁盘名。
    pub root: String,
    /// 承载 `/data` 的磁盘名。
    pub data: String,
}

/// 布局分类。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum LayoutKind {
    /// 系统与数据分区同盘。
    Single,
    /// 系统与数据分区分盘。
    Dual,
    /// 缺任一侧，不下结论。
    None,
}

/// 布局判定结论。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum LayoutState {
    /// 系统与数据分区齐备。
    Ok,
    /// 未找到根分区。
    NoRoot,
    /// 未找到数据分区。
    NoData,
    /// 数据分区不是盘上最后一号，无法扩容。
    NotLast,
}

/// `airy-disk health --json` 的健康判定。
#[derive(Debug, Deserialize)]
pub struct Health {
    /// 判定结论。
    pub verdict: HealthState,
    /// 被判定的分区名；无数据分区时为空串。
    pub part: String,
    /// 容量，字节。
    pub total: u64,
    /// 可用容量，字节。
    pub free: u64,
    /// 已用百分比。
    pub used: u32,
    /// 修复建议；健康时为空串。
    pub advice: String,
}

/// 健康判定结论。
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "kebab-case")]
pub enum HealthState {
    /// 健康。
    Ok,
    /// 未找到数据分区。
    NoData,
    /// 数据分区只读挂载。
    ReadOnly,
    /// 使用率达到上限。
    LowSpace,
    /// 拓扑认为已挂载而内核未挂载。
    Unavailable,
}

impl Status {
    /// 解析 status 载荷；schema 不是 [`SCHEMA_VERSION`] 即拒绝。
    pub fn parse(bytes: &[u8]) -> Result<Self, Refusal> {
        let status: Self = decode(bytes)?;
        if status.schema == SCHEMA_VERSION {
            Ok(status)
        } else {
            Err(Refusal::unsupported(status.schema))
        }
    }
}

impl Layout {
    /// 解析 layout 载荷；字段与枚举不受支持即拒绝。
    pub fn parse(bytes: &[u8]) -> Result<Self, Refusal> {
        decode(bytes)
    }
}

impl Health {
    /// 解析 health 载荷；字段与枚举不受支持即拒绝。
    pub fn parse(bytes: &[u8]) -> Result<Self, Refusal> {
        decode(bytes)
    }
}
