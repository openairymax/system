# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 命令行分派与判定输出
#
# 每个子命令只做两件事：算出违规项、给出成功证据行。参数解析后的异常转换与
# 「JSON / 逐条问题 / 证据行」三种输出收在此处，子命令里不再重复同样的分支。

"""AirymaxOS command line dispatch and reporting."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

from .err import EXIT_OK, EXIT_POLICY, Fault

__all__ = ["boot", "emit"]


def boot(prog: str, argv: Sequence[str] | None,
         build: Callable[[Sequence[str] | None], Any]) -> int:
    """解析并分派；Fault 只在顶层转换成退出码。"""
    args = build(argv)
    try:
        return args.func(args)
    except Fault as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return exc.code


def emit(prog: str, bad: list[str], line: str, as_json: bool,
         body: dict[str, Any]) -> int:
    """判定结果的三路输出：JSON、逐条问题、成功证据行。"""
    if as_json:
        print(json.dumps(body, ensure_ascii=False, sort_keys=True))
    elif bad:
        print("\n".join(f"{prog}: {item}" for item in bad), file=sys.stderr)
    else:
        print(line)
    return EXIT_OK if not bad else EXIT_POLICY
