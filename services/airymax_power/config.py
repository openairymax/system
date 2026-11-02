# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度的人读结论文本
#
# 门禁与运维看到的是同一份档位表，但两者需要的不是同一段话：门禁只要
# 退出码，运维要的是「这次为什么降档、下一步查什么」。这里只放文本，不放
# 判定——判定在 guard 与 tuning，避免同一结论出现第二个计算处。

"""AirymaxOS power advice."""

from __future__ import annotations

from .policy import Policy
from .supply import LOW_CHARGE
from .thermal import CRITICAL_MARGIN, HOT_MARGIN, WARN_MARGIN, Severity
from .tuning import State

__all__ = ["advice", "reference", "temp_advice"]

ADVICE = {
    State.OK: "档位已与当前供电和热状态一致",
    State.PARTIAL: "部分控制点未落地，检查可选档位、sysfs 权限与其它调优进程",
    State.FAILED: "存在控制点但全部写入失败，检查 sysfs 权限与其它调优进程",
    State.UNSUPPORTED: "本机未暴露 platform_profile、governor 或 EPP",
}

HEAT = {
    Severity.UNKNOWN: "没有可读的有效热区，温度状态未知",
    Severity.OK: "热余量充足，保持当前档位",
    Severity.WARN: "热余量收窄，继续观察节流计数",
    Severity.HOT: "已进入降档区间，切低功耗档位并降频",
    Severity.CRITICAL: "已达或越过热区 critical 门限，随时可能保护性关机",
}


def advice(state: State) -> str:
    return ADVICE[state]


def temp_advice(severity: Severity) -> str:
    return HEAT[severity]


def reference(policy: Policy) -> str:
    rows = (
        ("ac", policy.ac),
        ("battery", policy.battery),
        ("cool", policy.cool),
    )
    lines = [
        "# AirymaxOS 电源与温度参考（只打印，不落盘）",
        f"# {'source':<8} {'platform_profile':<16} "
        f"{'governor':<20} epp",
    ]
    lines.extend(
        f"{name:<8} {wanted.profile:<16} "
        f"{'|'.join(wanted.governors):<20} {'|'.join(wanted.epps)}"
        for name, wanted in rows
    )
    lines.append(
        "# 温度余量 = critical - temp（毫摄氏度）："
        f"<={CRITICAL_MARGIN} critical，<={HOT_MARGIN} hot，"
        f"<={WARN_MARGIN} warn"
    )
    lines.append(f"# 电池电量 <= {LOW_CHARGE}% 时按 cool 档处理")
    return "\n".join(lines)
