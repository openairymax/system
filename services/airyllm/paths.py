# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 大模型运行根定位
#
# 运行根只有 model.ROOT 一个来源；测试与恢复介质可以显式传入别处，但
# 不读环境变量——systemd 单元不带会话环境，允许 env 覆盖只会让「这次
# 实际用了哪个根」在事后无法审计。

"""AirymaxOS local model runtime paths (K-20-02).

设计依据：docs-linux/100-operations/07-systemd-integration.md §2
（服务运行目录与状态落点）。

本层只做路径推导与目录补建，不读清单、不校验资产。

"""

from __future__ import annotations

from pathlib import Path

from . import model

__all__ = ["base", "ensure", "manifest", "models", "status"]

MODE = 0o750


def base(root: str | Path | None = None) -> Path:
    """运行根；``None`` 取契约常量。"""
    return Path(model.ROOT if root is None else root)


def models(root: str | Path | None = None) -> Path:
    """模型资产目录。"""
    return base(root) / model.MODELS


def manifest(root: str | Path | None = None) -> Path:
    """模型清单文件。"""
    return base(root) / model.MANIFEST_FILE


def status(root: str | Path | None = None) -> Path:
    """运行状态文件。"""
    return base(root) / model.STATUS_FILE


def ensure(root: str | Path | None = None) -> Path:
    """补建运行根与模型目录，返回运行根。

    装机后由单元文件交付目录，二者本就存在；这里只覆盖手工部署与
    测试环境。只建缺失的那一层，不改既有目录的属主与权限——它们归
    systemd 的 StateDirectory 管，运行期改写会让下次启动的属主回收
    失去基准。
    """
    home = base(root)
    for path in (home, models(home)):
        if not path.is_dir():
            path.mkdir(parents=True, mode=MODE)
    return home
