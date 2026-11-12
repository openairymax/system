# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 升级通道服务包
#
# 版本发现、基底勘误与状态读写的命名空间入口。对外只导出数据契约，
# 网络取件与事务执行不因导入本包而生效。

"""AirymaxOS update channel package (V-14).

设计依据：docs-linux/190-distribution/04-update-mechanism.md §2。
"""

from __future__ import annotations

from .feed import Untrusted, load_manifest
from .identity import Verdict, compare
from .local import Probe, local_identity
from .model import (
    CHANNELS,
    CHANNEL_STABLE,
    EXIT_FAIL,
    EXIT_NEW,
    EXIT_OK,
    EXIT_REINSTALL,
    EXIT_UNKNOWN,
    FEED_URL,
    FIELDS,
    ISO_FIELDS,
    ISO_OPTIONAL,
    KERNEL_FIELDS,
    NOTES_HOSTS,
    SCHEMA_VERSION,
    STATE_DIR,
    STATE_FILE,
    Asset,
    Identity,
    Kernel,
    Manifest,
)
from .model import __all__ as _contract
from .net import Fetch, Unreachable, fetch_manifest
from .state import (
    Corrupt,
    Failure,
    Finding,
    State,
    load,
    record_error,
    record_ok,
    record_sent,
    save,
    state_path,
)

_EXTRA = [
    "Corrupt",
    "Failure",
    "Fetch",
    "Finding",
    "Probe",
    "State",
    "Unreachable",
    "Untrusted",
    "Verdict",
    "compare",
    "fetch_manifest",
    "load",
    "load_manifest",
    "local_identity",
    "record_error",
    "record_ok",
    "record_sent",
    "save",
    "state_path",
]

# 契约常量的顺序由 model 决定；类型与其他符号沿用「合并后按名排序」
# 的既有排列，读者按名字找即可，不必记住来源模块。
_CONSTS = [name for name in _contract if name.isupper()]
_TYPES = [name for name in _contract if not name.isupper()]

__all__ = [*_CONSTS, *sorted([*_TYPES, *_EXTRA])]
