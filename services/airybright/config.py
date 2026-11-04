# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光参考文本
#
# `config` 子命令与报告尾行的建议都从这里取，避免说明文字在命令行与文档
# 之间各写一遍。文字只解释规则，不复述数字——数字来自 policy 与 report，
# 抄一遍就会与实现漂移。

"""AirymaxOS backlight reference text."""

from __future__ import annotations

from .policy import MARK, Policy
from .report import State

__all__ = ["ADVICE", "advice", "reference"]

ADVICE = {
    State.OK: "背光已按目标落地",
    State.PARTIAL: "部分目标未落地，按行内的 error 字段定位",
    State.FAILED: "写入被拒或回读不符，检查 brightness 的写权限与通路",
    State.ABSENT: "未发现可调背光：KMS 可能尚未接管，或本机没有可调背光",
}


def advice(state: State) -> str:
    return ADVICE[state]


def reference(policy: Policy) -> str:
    return "\n".join((
        f"{MARK} config backlight={policy.backlight} leds={policy.leds}"
        f" floor={policy.floor}%",
        "  order firmware>platform>raw，其次比 max_brightness，最后比名字",
        "  floor screen=1%（--floor 0 才允许关屏） keyboard=0%",
        "  exit 0 ok / 3 partial / 1 failed / 4 absent / 2 usage",
    ))
