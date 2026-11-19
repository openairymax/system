// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
// SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
// ===========================================================================
// ASR 模型发现与校验
//
// 资产包 `airy-asr-model` 只承诺三件事：模型目录里有 manifest.json、该
// 清单的字段能对上、清单里的 sha256 与实物一致。任何一条不满足都必须
// 让网关报 `asr=unavailable`，而不是带着半个模型继续跑。
//
// sha256 由 `intent` crate 提供：审批文件匹配与模型校验共用同一份实现，
// 不允许各自手写一份。本文件只负责把清单字段与实物对上。
// ===========================================================================

use std::fs;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::AsrError;

/// 清单文件名，固定放在模型目录根部。
pub const MANIFEST: &str = "manifest.json";

/// 模型清单；字段与 §14.7 一一对应。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Manifest {
    name: String,
    version: String,
    digest: [u8; 32],
    bytes: u64,
}

impl Manifest {
    /// 从 JSON 字节解析并校验；未知字段忽略，缺失或格式错即拒绝。
    pub fn parse(raw: &[u8]) -> Result<Self, AsrError> {
        let wire: Wire = serde_json::from_slice(raw).map_err(|_| AsrError::Json)?;
        if wire.name.is_empty() || wire.version.is_empty() {
            return Err(AsrError::Field);
        }
        if wire.bytes == 0 {
            return Err(AsrError::Field);
        }
        let digest = hex32(&wire.sha256)?;
        Ok(Self {
            name: wire.name,
            version: wire.version,
            digest,
            bytes: wire.bytes,
        })
    }

    /// 模型名。
    pub fn name(&self) -> &str {
        &self.name
    }

    /// 模型版本。
    pub fn version(&self) -> &str {
        &self.version
    }

    /// 模型字节数。
    pub fn bytes(&self) -> u64 {
        self.bytes
    }

    /// 模型摘要的小写十六进制。
    pub fn digest(&self) -> String {
        intent::sha256::hex_of(&self.digest)
    }
}

/// 已校验的模型：清单与实物对得上才存在。
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Model {
    manifest: Manifest,
    path: PathBuf,
}

impl Model {
    /// 读取清单与模型文件并逐项核对字节数与摘要。
    pub fn open(manifest: &Path, file: &Path) -> Result<Self, AsrError> {
        let raw = fs::read(manifest).map_err(|_| AsrError::Io)?;
        let manifest = Manifest::parse(&raw)?;
        let data = fs::read(file).map_err(|_| AsrError::Io)?;
        if data.len() as u64 != manifest.bytes {
            return Err(AsrError::Size);
        }
        if intent::sha256::digest(&data) != manifest.digest {
            return Err(AsrError::Digest);
        }
        Ok(Self {
            manifest,
            path: file.to_path_buf(),
        })
    }

    /// 清单。
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }

    /// 模型文件路径。
    pub fn path(&self) -> &Path {
        &self.path
    }
}

/// 清单的线上形态；只声明清单承诺的四个字段。
#[derive(Deserialize)]
struct Wire {
    name: String,
    version: String,
    sha256: String,
    bytes: u64,
}

/// 解析 64 位十六进制摘要；大写按小写处理。
fn hex32(text: &str) -> Result<[u8; 32], AsrError> {
    let bytes = text.as_bytes();
    if bytes.len() != 64 {
        return Err(AsrError::Field);
    }
    let mut out = [0u8; 32];
    for (i, pair) in bytes.chunks_exact(2).enumerate() {
        let hi = nibble(pair[0])?;
        let lo = nibble(pair[1])?;
        out[i] = (hi << 4) | lo;
    }
    Ok(out)
}

/// 单个十六进制字符。
fn nibble(byte: u8) -> Result<u8, AsrError> {
    match byte {
        b'0'..=b'9' => Ok(byte - b'0'),
        b'a'..=b'f' => Ok(byte - b'a' + 10),
        b'A'..=b'F' => Ok(byte - b'A' + 10),
        _ => Err(AsrError::Field),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 清单的 JSON 形态；测试只关心四个字段。
    fn manifest(name: &str, digest: &str, bytes: u64) -> Vec<u8> {
        format!(r#"{{"name":"{name}","version":"1","sha256":"{digest}","bytes":{bytes}}}"#)
            .into_bytes()
    }

    #[test]
    fn manifest_roundtrips() {
        let digest = intent::sha256::hex(b"model");
        let parsed = Manifest::parse(&manifest("tiny", &digest, 5)).expect("可解析");
        assert_eq!(parsed.name(), "tiny");
        assert_eq!(parsed.version(), "1");
        assert_eq!(parsed.bytes(), 5);
        assert_eq!(parsed.digest(), digest);
    }

    #[test]
    fn manifest_accepts_uppercase_digest() {
        let digest = intent::sha256::hex(b"model").to_uppercase();
        let parsed = Manifest::parse(&manifest("tiny", &digest, 5)).expect("可解析");
        assert_eq!(parsed.digest(), digest.to_lowercase());
    }

    #[test]
    fn manifest_rejects_bad_digest() {
        assert_eq!(
            Manifest::parse(&manifest("tiny", "abc", 5)),
            Err(AsrError::Field)
        );
        let odd = "z".repeat(64);
        assert_eq!(
            Manifest::parse(&manifest("tiny", &odd, 5)),
            Err(AsrError::Field)
        );
    }

    #[test]
    fn manifest_rejects_empty_fields() {
        let digest = intent::sha256::hex(b"model");
        assert_eq!(
            Manifest::parse(&manifest("", &digest, 5)),
            Err(AsrError::Field)
        );
        assert_eq!(
            Manifest::parse(&manifest("tiny", &digest, 0)),
            Err(AsrError::Field)
        );
        assert_eq!(Manifest::parse(b"{}"), Err(AsrError::Json));
    }

    #[test]
    fn model_verifies_digest_and_size() {
        let dir = scratch("ok");
        let body = b"offline model";
        let digest = intent::sha256::hex(body);
        fs::write(dir.join("manifest.json"), manifest("tiny", &digest, 13)).expect("可写");
        fs::write(dir.join("model.bin"), body).expect("可写");
        let model = Model::open(&dir.join("manifest.json"), &dir.join("model.bin")).expect("可开");
        assert_eq!(model.manifest().name(), "tiny");
        assert_eq!(model.path(), dir.join("model.bin"));
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn model_rejects_wrong_size() {
        let dir = scratch("size");
        let digest = intent::sha256::hex(b"offline model");
        fs::write(dir.join("manifest.json"), manifest("tiny", &digest, 4)).expect("可写");
        fs::write(dir.join("model.bin"), b"offline model").expect("可写");
        assert_eq!(
            Model::open(&dir.join("manifest.json"), &dir.join("model.bin")),
            Err(AsrError::Size)
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn model_rejects_wrong_digest() {
        let dir = scratch("digest");
        let digest = intent::sha256::hex(b"other");
        fs::write(dir.join("manifest.json"), manifest("tiny", &digest, 13)).expect("可写");
        fs::write(dir.join("model.bin"), b"offline model").expect("可写");
        assert_eq!(
            Model::open(&dir.join("manifest.json"), &dir.join("model.bin")),
            Err(AsrError::Digest)
        );
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn missing_manifest_is_io_error() {
        let dir = scratch("missing");
        assert_eq!(
            Model::open(&dir.join("manifest.json"), &dir.join("model.bin")),
            Err(AsrError::Io)
        );
        let _ = fs::remove_dir_all(&dir);
    }

    /// 每个用例独占一个临时目录，避免并发测试互踩。
    fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("airy-asr-{}-{tag}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("可建目录");
        dir
    }
}
