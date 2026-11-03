# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 电源与温度的人读结论文本
#
# 门禁与运维看到的是同一份档位表，但两者需要的不是同一段话：门禁只要
# 退出码，运维要的是「这次为什么降档、下一步查什么」。这里只放文本，不放
# 判定——判定在 guard 与 tuning，避免同一结论出现第二个计算处。

"""AirymaxOS power advice."""

from __future__ import annotations

from . import idle, load, stall
from .duty import CRITICAL_CHARGE, Duty
from .policy import Policy
from .supply import LOW_CHARGE
from .thermal import CRITICAL_MARGIN, HOT_MARGIN, WARN_MARGIN, Severity
from .tuning import State

__all__ = ["advice", "duty_advice", "reference", "temp_advice"]

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


def duty_advice(duty: Duty) -> str:
    if duty is Duty.POWEROFF:
        return "电量已到临界，apply 已请求受控关机"
    if duty is Duty.NOTIFY:
        return "电量偏低，已广播告警；插电后自动恢复"
    return ""


def reference(policy: Policy) -> str:
    rows = (
        ("ac", policy.ac),
        ("busy", policy.busy),
        ("weak", policy.cool),
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
    lines.append(
        f"# 电池电量 <= {LOW_CHARGE}% 广播告警，"
        f"<= {CRITICAL_CHARGE}% 受控关机（仅电池或适配器功率不足时）"
    )
    lines.append(
        "# 负载 strain 取 cpu/memory/io/swap 最严重项：PSI some avg10 门限 "
        f"cpu {load.PSI_CPU_HOT}/{load.PSI_CPU_WARN}、"
        f"memory {load.PSI_MEM_HOT}/{load.PSI_MEM_WARN}、"
        f"io {load.IO_HOT}/{load.IO_WARN}（hot/warn）"
    )
    lines.append(
        "# 无 PSI 时回退：1 分钟负载/核数 "
        f"{load.LOAD_HOT}/{load.LOAD_WARN}、"
        f"内存已用 {load.USE_HOT}/{load.USE_WARN}；"
        f"swap 已用 {load.SWAP_HOT}/{load.SWAP_WARN}"
    )
    lines.append("# AC 上 strain >= warn 走 busy；内存 hot 时广播一次告警")
    lines.append(
        f"# AC 且热区未到 hot 时，禁用退出延迟 > {idle.BUDGET_US}us 的空闲态；"
        "电池或过热时恢复固件默认"
    )
    lines.append(
        "# 散热设备只做清单，不写 cooling_device：风扇由 EC 与固件独占时"
        "（ACPI 未实现 _FSL），写入 cur_state 返回 ENODEV 且风扇转速不变；"
        "降温只能经 platform_profile、governor/EPP 与空闲深度"
    )
    lines.append(
        f"# 周期巡检每 {stall.INTERVAL:g}s 一次，间隔超出 "
        f"{stall.BUDGET:g}s 记为整机停顿（CLOCK_BOOTTIME，含挂起）；"
        "停顿已过去，只留证据不动作"
    )
    lines.append(f"# 关机 {policy.poweroff} poweroff --no-block，"
                 f"广播 {policy.wall}")
    return "\n".join(lines)
