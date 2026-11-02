# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 探针的三种取证编排
#
# status 只读清点，不改变机器状态，可在任何时刻跑；sample 打开一个追踪
# 窗口，采完把 enable 与 tracing_on 原样恢复；verify 在此基础上采样多轮
# 并对两条只读通道各发起一次写入试探，把「本机没有可写注入面」这件事
# 也变成报告的一部分。三者共用同一份发现、统计与通道检查实现，避免
# 「命令行看到的状态」与「验收看到的状态」出现两套口径。
#
# 采样轮数由调用方给定，编排层不做默认值决策：默认值属于命令行契约。

"""AirymaxOS probe orchestration."""

from __future__ import annotations

from pathlib import Path

from . import channels, events, stats, trace
from .paths import Paths
from .report import SAMPLE, STATUS, VERIFY, Report

__all__ = ["sample", "status", "verify"]

Outcomes = tuple[channels.Outcome, ...]

Samples = tuple[trace.Sample, ...]


def status(paths: Paths) -> Report:
    """只读清点：事件、通道与静态计数。"""
    return _build(STATUS, paths, (), ())


def sample(paths: Paths, window: float) -> Report:
    """开一个追踪窗口，采一轮事件计数。"""
    return _build(SAMPLE, paths, (), (_round(paths, window),))


def verify(paths: Paths, window: float, rounds: int) -> Report:
    """多轮采样并试探两条只读通道，验证计数是否随动作递增。"""
    shots = tuple(_round(paths, window) for _ in range(rounds))
    return _build(VERIFY, paths, channels.attempt(paths), shots)


def _round(paths: Paths, window: float) -> trace.Sample:
    return trace.record(Path(paths.tracefs), window, events.EVENTS)


def _build(mode: str, paths: Paths, tries: Outcomes, shots: Samples) -> Report:
    return Report(
        mode=mode,
        events=events.discover(Path(paths.tracefs)),
        channels=channels.inspect(paths),
        attempts=tries,
        stats=stats.snapshot(paths),
        samples=shots,
    )