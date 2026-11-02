# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 大模型运行环境数据契约
#
# 运行根、清单、资产与状态的类型声明。模型与推理引擎全部来自本地，
# 运行期不发起网络请求；清单里的每个资产以 sha256 与实物绑定，不符
# 即硬失败。加速后端只是筛选条件，探测不到时按 CPU 路径运行，并在
# 状态里留下 degraded:<原因>，不允许静默降级。

"""AirymaxOS local model runtime types (K-20-01).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（资产清单与摘要校验）与 docs-linux/100-operations/07-systemd-integration.md
（服务单元与运行目录）。本模块只声明契约：不读文件、不探测硬件、不启动
引擎；取值与判定分别由清单层、探测层与运行层承担。

"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

__all__ = [
    "ASSET_FIELDS",
    "MANIFEST_FIELDS",
    "MANIFEST_FILE",
    "MODELS",
    "REASON_SEP",
    "ROOT",
    "ROOT_ENV",
    "RUNTIME_FIELDS",
    "SCHEMA_VERSION",
    "STATUS_FILE",
    "Asset",
    "Backend",
    "Manifest",
    "Reason",
    "Runtime",
    "State",
    "Status",
]

SCHEMA_VERSION = 1

ROOT = "/var/lib/airy-llm"

ROOT_ENV = "AIRYMAX_LLM_ROOT"

MODELS = "models"

MANIFEST_FILE = "manifest.json"

STATUS_FILE = "status.json"

REASON_SEP = ":"

ASSET_FIELDS = frozenset({"path", "size", "sha256"})

RUNTIME_FIELDS = frozenset({"engine", "version", "path", "backends"})

MANIFEST_FIELDS = frozenset({
    "schema",
    "name",
    "version",
    "quant",
    "runtime",
    "assets",
})


@unique
class Backend(Enum):
    """推理后端；token 写入状态文件，须与探测结果逐字对应。"""

    CPU = "cpu"
    CUDA = "cuda"
    ROCM = "rocm"
    VULKAN = "vulkan"


@unique
class State(Enum):
    """运行状态；``DEGRADED`` 必须同时给出原因。"""

    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


@unique
class Reason(Enum):
    """状态原因 token；``degraded:<原因>`` 用 :data:`REASON_SEP` 拼接。"""

    NO_ACCEL = "no-accel"
    NO_RUNTIME = "no-runtime"
    NO_MODEL = "no-model"
    NO_MANIFEST = "no-manifest"
    DIGEST_MISMATCH = "digest-mismatch"
    ASSET_CORRUPT = "asset-corrupt"


@dataclass(frozen=True, slots=True)
class Asset:
    """模型资产的最小描述。

    Attributes:
        path: 相对运行根的路径，不得含 ``..`` 或绝对前缀。
        size: 字节数。
        sha256: 64 位十六进制摘要。
    """

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class Runtime:
    """本地推理引擎的描述。

    Attributes:
        engine: 引擎名，如 ``llama.cpp``。
        version: 引擎版本，与安装记录一致。
        path: 可执行文件绝对路径。
        backends: 该引擎编译期支持的后端集合。
    """

    engine: str
    version: str
    path: str
    backends: tuple[Backend, ...] = (Backend.CPU,)


@dataclass(frozen=True, slots=True)
class Manifest:
    """模型清单；``assets`` 覆盖模型权重与配套配置。

    Attributes:
        schema: 清单结构版本，必须是 :data:`SCHEMA_VERSION`。
        name: 模型名。
        version: 模型版本。
        quant: 量化规格，如 ``q4_k_m``。
        runtime: 清单要求的本地引擎。
        assets: 随模型交付的资产列表。
    """

    schema: int
    name: str
    version: str
    quant: str
    runtime: Runtime
    assets: tuple[Asset, ...] = ()


@dataclass(frozen=True, slots=True)
class Status:
    """一次运行期判定的结果。

    Attributes:
        state: 运行状态。
        backend: 实际使用的后端。
        reason: ``DEGRADED`` 或 ``FAILED`` 时的原因。
        digest: 已校验清单的 sha256，供重复启动时快速复核。
    """

    state: State
    backend: Backend
    reason: Reason | None = None
    digest: str = ""
