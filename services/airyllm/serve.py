# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 推理引擎启动参数
#
# 启动面只由清单与判定派生：权重取清单里唯一的 gguf，后端取判定结果，
# 其余监听参数在此固定。不读环境变量、不读会话配置——服务由 systemd
# 托管，参数若来自会话，重启后就会静默换一套监听面，事后无法审计。
#
# 监听地址固定回环：本机模型只服务本机的 agent 运行时，对外暴露等于把
# 无认证的推理端点交给整个网段。

"""AirymaxOS inference engine launch parameters (K-20-11).

设计依据：docs-linux/100-operations/07-systemd-integration.md §3
（服务单元的启动面必须固定）与
docs-workload/30-interfaces/02-api-reference.md（Gateway 占用 8080，
本地推理端点须另择端口）。

"""

from __future__ import annotations

from pathlib import Path

from . import model
from .failure import Failure

__all__ = ["HOST", "MODEL_SUFFIX", "PORT", "argv", "model_path"]

HOST = "127.0.0.1"

PORT = 11435

MODEL_SUFFIX = ".gguf"

GPU_LAYERS = 999


def model_path(entry: model.Manifest, home: Path) -> Path:
    """从清单资产里取出唯一的模型权重。

    Args:
        entry: 已通过校验的清单。
        home: 运行根。

    Returns:
        权重的绝对路径。

    Raises:
        Failure: 清单里的 gguf 不是恰好一个；此时无从判断该加载谁，
            猜一个只会把问题推到推理阶段。
    """
    names = [asset.path for asset in entry.assets
             if asset.path.endswith(MODEL_SUFFIX)]
    if len(names) != 1:
        raise Failure(f"清单须恰有一个 {MODEL_SUFFIX} 权重，"
                      f"实有 {len(names)}", model.Reason.NO_MODEL)
    return (Path(home) / names[0]).resolve()


def argv(entry: model.Manifest, backend: model.Backend,
         home: Path) -> list[str]:
    """拼出引擎的启动命令行。

    Args:
        entry: 已通过校验的清单。
        backend: 判定选定的后端。
        home: 运行根。

    Returns:
        参数列表，第 0 项为引擎可执行文件。
    """
    return [
        entry.runtime.path,
        "--model", str(model_path(entry, home)),
        "--alias", entry.name,
        "--host", HOST,
        "--port", str(PORT),
        "--n-gpu-layers", str(_layers(backend)),
    ]


def _layers(backend: model.Backend) -> int:
    """加速后端卸载全部层，CPU 后端显式卸载零层。"""
    return 0 if backend is model.Backend.CPU else GPU_LAYERS
