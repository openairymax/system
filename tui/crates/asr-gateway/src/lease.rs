// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// 录音设备租约
//
// 「网关独占录音设备」不能只写在文档里：两个网关实例同时读到同一路
// PCM，各自产出半段识别结果，用户看到的是随机的错句，而状态栏仍显示
// ready。租约把独占做成可失败的操作——第二个实例在 claim 时拿到 busy，
// 状态栏据此显示 `asr=busy`。
//
// 用 flock 而不是 PID 锁文件：锁随打开的文件描述符存在，进程退出（含
// 崩溃）时内核自动释放，不存在陈旧锁；锁文件方案要么留死锁，要么再加
// 一套回收策略，而这套策略本身又会出错。
//
// 打开设备只申请读权限：采集不需要写回设备。打开失败按 `asr=unavailable`
// 处理（设备不存在、权限被撤销），与「模型缺失」共用同一个用户可见状态。
//
// SSoT: 0.0.5 方案 §14.7、§14.13。
// ===========================================================================

//! 录音设备的独占租约。

use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};

use rustix::fs::{flock, FlockOperation};

use crate::AsrError;

/// 一次独占认领；Drop 时关闭文件即释放。
#[derive(Debug)]
pub struct Lease {
    device: PathBuf,
    /// 只用于持有描述符；Drop 时关闭即释放。
    _file: File,
}

impl Lease {
    /// 独占认领设备。
    ///
    /// 打不开报 `Io`（`asr=unavailable`），已被别的实例持锁报 `Busy`
    /// （`asr=busy`）；两种情况都不带半开的设备继续跑。
    pub fn claim(device: impl AsRef<Path>) -> Result<Self, AsrError> {
        let device = device.as_ref();
        let file = OpenOptions::new()
            .read(true)
            .open(device)
            .map_err(|_| AsrError::Io)?;
        match flock(&file, FlockOperation::NonBlockingLockExclusive) {
            Ok(()) => Ok(Self {
                device: device.to_path_buf(),
                _file: file,
            }),
            Err(rustix::io::Errno::WOULDBLOCK) => Err(AsrError::Busy),
            Err(_) => Err(AsrError::Io),
        }
    }

    /// 租约覆盖的设备路径。
    pub fn device(&self) -> &Path {
        &self.device
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    #[test]
    fn a_second_claim_reports_busy() {
        let path = scratch("busy");
        let first = Lease::claim(&path).expect("可认领");
        assert_eq!(first.device(), path);
        assert_eq!(Lease::claim(&path).err(), Some(AsrError::Busy));
        drop(first);
        assert!(Lease::claim(&path).is_ok());
        let _ = fs::remove_dir_all(path.parent().expect("有父目录"));
    }

    #[test]
    fn a_missing_device_is_unavailable() {
        let path = scratch("missing");
        let gone = path.with_file_name("absent");
        assert_eq!(Lease::claim(&gone).err(), Some(AsrError::Io));
        assert_eq!(AsrError::Io.availability().status(), "unavailable");
        let _ = fs::remove_dir_all(path.parent().expect("有父目录"));
    }

    #[test]
    fn releasing_one_lease_does_not_drop_another() {
        let first_path = scratch("first");
        let second_path = scratch("second");
        let first = Lease::claim(&first_path).expect("可认领");
        let second = Lease::claim(&second_path).expect("可认领");
        drop(first);
        assert_eq!(Lease::claim(&first_path).map(|_| ()), Ok(()));
        assert_eq!(Lease::claim(&second_path).err(), Some(AsrError::Busy));
        drop(second);
        let _ = fs::remove_dir_all(first_path.parent().expect("有父目录"));
    }

    #[test]
    fn busy_maps_to_the_status_bar_token() {
        assert_eq!(AsrError::Busy.availability().status(), "busy");
    }

    /// 每个用例独占一个文件，避免并发测试互踩。
    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("airy-lease-{}-{tag}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("可建目录");
        let path = dir.join("pcm");
        fs::write(&path, b"device").expect("可写");
        path
    }
}
