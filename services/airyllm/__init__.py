# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 大模型运行环境服务包
#
# 清单、探测与运行三层的命名空间入口。对外只导出数据契约，导入本包
# 不读文件、不探测硬件、不启动推理引擎。

"""AirymaxOS local model runtime package (K-20).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
与 docs-linux/100-operations/07-systemd-integration.md。
"""

from __future__ import annotations

from .model import (
    ASSET_FIELDS,
    MANIFEST_FIELDS,
    MANIFEST_FILE,
    MODELS,
    REASON_SEP,
    ROOT,
    RUNTIME_FIELDS,
    SCHEMA_VERSION,
    STATUS_FILE,
    Asset,
    Backend,
    Manifest,
    Reason,
    Runtime,
    State,
    Status,
)
from .model import __all__ as _contract

__all__ = list(_contract)
