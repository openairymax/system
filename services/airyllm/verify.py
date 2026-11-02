# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型资产校验
#
# 清单只声明「应当是什么」，本层回答「磁盘上实际是什么」。两者不符即
# 硬失败，不做部分放行：模型权重被截断或替换后仍能加载，只会把故障
# 推迟到推理阶段，届时现象与根因相隔很远。
#
# 资产路径先按运行根展开再解析，解析结果必须仍在运行根内：清单是外部
# 交付物，符号链接可以绕开纯字符串检查，故只认解析后的真实位置。

"""AirymaxOS model asset verification (K-20-04).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（资产以 sha256 绑定，不符即硬失败）。

"""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import model
from .failure import Failure

__all__ = ["CHUNK", "check", "digest"]

CHUNK = 1 << 20


def digest(path: Path, chunk: int = CHUNK) -> str:
    """计算文件的 sha256 摘要。

    Args:
        path: 待计算的文件。
        chunk: 单次读取字节数；大权重文件不整体进内存。

    Returns:
        64 位小写十六进制摘要。
    """
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            hasher.update(block)
    return hasher.hexdigest()


def check(root: Path, entry: model.Manifest) -> None:
    """逐件核对清单资产与磁盘实物。

    Args:
        root: 运行根，清单路径相对它展开。
        entry: 待核对的清单。

    Raises:
        Failure: 任一资产缺失、越界、尺寸或摘要不符。
    """
    home = Path(root).resolve()
    for asset in entry.assets:
        _one(home, asset)


def _one(home: Path, asset: model.Asset) -> None:
    path = _place(home, asset)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise Failure(f"资产不可读: {asset.path}",
                      model.Reason.NO_MODEL, asset.path) from exc
    if not path.is_file():
        raise Failure(f"资产不是普通文件: {asset.path}",
                      model.Reason.NO_MODEL, asset.path)
    if size != asset.size:
        raise Failure(f"资产尺寸不符: {asset.path}",
                      model.Reason.ASSET_CORRUPT, asset.path)
    if digest(path) != asset.sha256:
        raise Failure(f"资产摘要不符: {asset.path}",
                      model.Reason.DIGEST_MISMATCH, asset.path)


def _place(home: Path, asset: model.Asset) -> Path:
    path = (home / asset.path).resolve()
    try:
        path.relative_to(home)
    except ValueError as exc:
        raise Failure(f"资产路径越界: {asset.path}",
                      model.Reason.ASSET_CORRUPT, asset.path) from exc
    return path
