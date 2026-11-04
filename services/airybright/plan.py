# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 背光读、写与相对调节
#
# 三个动作只在「写不写」上不同，判定统一交给 report，避免读路径和写路径
# 各自发明一套成功口径。
#
# 写入必须回读：部分机型的 `brightness` 会被固件二次换算，写 400 读回 398
# 是正常的（误差一格以内按落地处理），但写 400 读回 0 说明这条通路根本没
# 生效，不能报成功。回读用同一节点，不用 `actual_brightness`——后者是面板
# 实测值，会随环境光与省电策略漂移，拿它判「写进去了没有」会误判。
#
# 相对调节以每台设备自己的当前读数为基准：屏幕 40%、键盘 100% 时各 `+10`
# 应当是 50% 与 100%（上限夹住），而不是把键盘拉到 50%。

"""AirymaxOS backlight operations."""

from __future__ import annotations

import errno

from . import device, level, report
from .policy import Policy, Target
from .report import Report, Row

__all__ = ["apply", "read", "step"]

KINDS = {
    Target.SCREEN: (device.SCREEN,),
    Target.KEYBOARD: (device.KBD,),
    Target.ALL: (device.SCREEN, device.KBD),
}

REASONS = {
    errno.EACCES: "denied",
    errno.EROFS: "read-only",
    errno.ENOENT: "gone",
    errno.EPERM: "denied",
}


def read(policy: Policy, target: Target) -> Report:
    """只读评估：每台主设备一行，缺席的一边也留一行。"""
    devs = device.pick(policy, target)
    rows = [report.row_of(dev) for dev in devs]
    return _wrap("read", policy, target, rows, devs)


def apply(policy: Policy, target: Target, pct: int) -> Report:
    """把目标百分比写到每台主设备并回读。"""
    devs = device.pick(policy, target)
    rows = [_put(dev, pct, _floor(policy, dev)) for dev in devs]
    return _wrap("write", policy, target, rows, devs)


def step(policy: Policy, target: Target, delta: int) -> Report:
    """相对调节：每台设备以自身读数为基准走一步。"""
    devs = device.pick(policy, target)
    rows = [_nudge(dev, delta, _floor(policy, dev)) for dev in devs]
    return _wrap("write", policy, target, rows, devs)


def _wrap(mode: str, policy: Policy, target: Target, rows: list[Row],
          devs: tuple[device.Device, ...]) -> Report:
    """缺席的一边补一行，报告与「目标里应该有哪几条」始终对齐。"""
    have = {dev.kind for dev in devs}
    rows.extend(report.missing(kind) for kind in KINDS[target]
                if kind not in have)
    return Report(mode=mode, target=target.value, floor=policy.floor,
                  rows=tuple(rows))


def _floor(policy: Policy, dev: device.Device) -> int:
    """屏幕受下限保护，键盘不受：关掉键盘背光是合法用法。"""
    return policy.floor if dev.kind == device.SCREEN else 0


def _nudge(dev: device.Device, delta: int, floor: int) -> Row:
    base = dev.level
    if base is None:
        return _failed(dev, "no-reading")
    pct = level.shift(level.pct_of(base, dev.maximum), delta, floor)
    return _put(dev, pct, floor)


def _put(dev: device.Device, pct: int, floor: int) -> Row:
    """写一个目标百分比；节点消失与被拒是两件事，分开报。"""
    want = level.raw_of(pct, dev.maximum, floor)
    goal = level.clamp(pct, floor)
    if not dev.node.exists():
        return _failed(dev, "gone", want, goal)
    if not dev.writable:
        return _failed(dev, "read-only", want, goal)
    try:
        dev.node.write_text(f"{want}\n", encoding="ascii")
    except OSError as exc:
        reason = REASONS.get(exc.errno, "write-failed")
        return _failed(dev, reason, want, goal)
    return _check(dev, want, goal)


def _check(dev: device.Device, want: int, pct: int) -> Row:
    """回读同一条节点；误差一格以内算落地。"""
    now = device.read_int(dev.node)
    if now is None:
        return _failed(dev, "no-readback", want, pct)
    landed = abs(level.pct_of(now, dev.maximum) - pct) <= 1
    return Row.of(dev, raw=now, want=want, pct=pct, landed=landed,
                  error=None if landed else "readback")


def _failed(dev: device.Device, error: str, want: int | None = None,
            pct: int | None = None) -> Row:
    return Row.of(dev, raw=dev.level, want=want, pct=pct, error=error)
