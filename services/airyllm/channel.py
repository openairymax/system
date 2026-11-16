# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型资产通道
#
# 模型体积以 GB 计，不能随系统包发布；资产由安装介质或离线包提供，装机
# 后由本层收进运行根。通道只认清单：源目录没有清单、清单与实物不符，都
# 在写入任何目标文件之前失败——半份资产比没有资产更难归因。
#
# 目标侧按「同尺寸同摘要则跳过」收敛：重跑同一份离线包应当无副作用，已
# 损坏的副本则被源文件替换。每件资产经临时文件原子落位，清单最后落位作为
# 提交点；跨文件不具原子性，中断后重跑一次即收敛，而运行层的摘要校验会把
# 中断状态判成失败，不会让它冒充就绪。
#
# 落位权限固定为可读：安装通常以 root 执行，服务以 airy 运行；若沿用
# 0600 之类由 umask 决定的权限，服务会因读不到权重而失败。清单 runtime.path
# 指向的那一件落在运行根内时是可执行文件，单独置可执行位——引擎与权重由
# 同一份离线包交付，引擎来自系统包时该路径在运行根之外，本层不碰权限。

"""AirymaxOS local model asset channel (K-21-01).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（资产以 sha256 绑定，不符即硬失败；运行期不发起网络请求）。

"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path

from . import manifest, model, paths, verify
from .failure import Failure

__all__ = ["apply"]

FILE_MODE = 0o644

RUN_MODE = 0o755

DIR_MODE = 0o755


def apply(source: str | Path,
          root: str | Path | None = None) -> tuple[model.Manifest, int]:
    """把一份离线模型包收进运行根并复核。

    Args:
        source: 含 ``manifest.json`` 与资产文件的目录。
        root: 运行根；``None`` 时取契约根。

    Returns:
        ``(清单, 实际写入的文件数)``；目标已一致时为 0。

    Raises:
        Failure: 源清单或源资产不可信；此时不写任何目标文件。
        OSError: 目标目录不可写或磁盘不足。
    """
    src = Path(source)
    entry = manifest.load(paths.manifest(src))
    verify.check(src, entry)
    home = paths.ensure(root)
    engine = _engine(entry)
    copied = sum(
        _sync(src, home, asset,
              RUN_MODE if asset.path == engine else FILE_MODE)
        for asset in entry.assets)
    _file(paths.manifest(src), paths.manifest(home), FILE_MODE)
    verify.check(home, entry)
    return entry, copied


def _engine(entry: model.Manifest) -> str:
    """清单引擎在运行根内的相对路径；引擎在运行根之外时为空串。

    离线包把引擎与权重一起交付，包内文件的相对位置与运行根一一对应，
    落位后清单声明的绝对路径就真实可执行。引擎由系统包提供时路径不在
    运行根内，这里返回空串，任何资产都不会被当作可执行文件。
    """
    try:
        return str(Path(entry.runtime.path).relative_to(model.ROOT))
    except ValueError:
        return ""


def _sync(src: Path, home: Path, asset: model.Asset, mode: int) -> bool:
    """同步一件资产；返回是否真的写入。"""
    target = home / asset.path
    if _same(target, asset, mode):
        return False
    _file(src / asset.path, target, mode)
    return True


def _same(path: Path, asset: model.Asset, mode: int) -> bool:
    """目标是否已是清单描述的那一件资产。

    符号链接一律视为不一致：只比较链接指向的内容，会让一次越界的旧
    链接在摘要碰巧相同时被保留下来。权限位不同同样重写，旧副本以只读
    位落下的引擎可在重跑后恢复可执行，不必先手工删除。
    """
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_size != asset.size or stat.S_IMODE(info.st_mode) != mode:
            return False
        return verify.digest(path) == asset.sha256
    except OSError:
        return False


def _file(origin: Path, target: Path, mode: int) -> None:
    """以临时文件原子落位，避免目标出现半截内容。"""
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    handle, temp = tempfile.mkstemp(dir=parent, prefix=".asset-")
    try:
        with os.fdopen(handle, "wb") as sink, origin.open("rb") as source:
            shutil.copyfileobj(source, sink, verify.CHUNK)
            sink.flush()
            os.fsync(sink.fileno())
        os.chmod(temp, mode)
        os.replace(temp, target)
    except BaseException:
        os.unlink(temp)
        raise
