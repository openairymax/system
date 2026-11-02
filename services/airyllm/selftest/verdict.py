# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行环境自检结论
#
# 自检的读者有三个：装完机对着终端的人、抓 journal 的运维、按退出码
# 分支的验收脚本。三者看到的是同一份结论——检查项名、通过与否、一行
# 依据——因此这里只定义结构，不定义措辞；措辞由渲染层统一给出。
#
# 检查项顺序即执行顺序，也即依赖顺序：前一项不通过，后一项没有可判定
# 的对象。顺序写死在 CHECKS 里而不是由各检查函数自行声明，是为了让
# 「被阻塞的项」也能按名补全，而不是从输出里消失。

"""AirymaxOS model runtime self-test verdict (K-23-02).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（完全断网可自检；不得静默跳过）与
docs-linux/190-distribution/04-update-mechanism.md §5.3
（首启自检的判定形态）。

"""

from __future__ import annotations

from dataclasses import dataclass

from .. import model

__all__ = ["CHECKS", "Report", "Result"]

CHECKS = (
    "root",
    "manifest",
    "assets",
    "engine",
    "smoke",
    "backend",
    "serve",
    "status",
)


@dataclass(frozen=True, slots=True)
class Result:
    """单项检查的结论。

    Attributes:
        name: 检查项名，取自 :data:`CHECKS`。
        ok: 是否通过。
        detail: 一行依据；失败时说明具体原因，通过时给出关键取值。
        reason: 失败归因；通过时为 ``None``。
    """

    name: str
    ok: bool
    detail: str = ""
    reason: model.Reason | None = None


@dataclass(frozen=True, slots=True)
class Report:
    """一次自检的全部结论。

    Attributes:
        rows: 按 :data:`CHECKS` 顺序排列的结论，不含被阻塞的项以外的省略。
        status: 由结论派生的运行判定，可直接落盘供外部工具读取。
    """

    rows: tuple[Result, ...]
    status: model.Status
