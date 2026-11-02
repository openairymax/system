# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行环境自检检查项
#
# 八项检查按依赖顺序执行：运行根可写、清单可解析、资产与摘要相符、
# 引擎可执行、引擎能跑、后端能选、启动参数能派生、判定能落盘。顺序
# 即依赖——后一项总要前一项的结果，因此不并行、不重排，也不允许
# 某一项失败后继续执行后续项：那只会把同一次故障打印八遍。
#
# 唯一新增的判定是「引擎能跑」：其余各层都只读文件，而清单与实物
# 相符并不代表引擎能在本机加载。冒烟只问版本，不加载权重——加载要
# 几十秒，且失败原因会混进权重与显存，与「装完能不能用」无关。
#
# 状态文件在最末尾写一次：判定先由已通过的检查派生，写入后再回读
# 比对。写不进或读不回，都以 status 一项失败告终，而不是留一个只有
# 内存里正确的结论。

"""AirymaxOS model runtime self-test steps (K-23-05).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（完全断网可自检、清单与实物不符即硬失败、降级须留 degraded:<原因>）。

"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .. import accel, manifest, model, paths, runtime, serve, status, verify
from ..failure import Failure
from .verdict import CHECKS, Report, Result

__all__ = ["DETAIL_MAX", "SMOKE_TIMEOUT", "run"]

SMOKE_TIMEOUT = 10

DETAIL_MAX = 60

PROBE = ".selftest"

VERSION = "--version"


@dataclass
class Facts:
    """检查过程中逐项累积的事实；各检查只读前置项写下的字段。"""

    home: Path
    seen: frozenset[model.Backend]
    entry: model.Manifest | None = None
    backend: model.Backend = model.Backend.CPU
    reason: model.Reason | None = None
    digest: str = ""


def run(root: str | Path | None = None,
        seen: frozenset[model.Backend] | None = None) -> Report:
    """跑完全部检查并把判定落盘。

    Args:
        root: 运行根；``None`` 时取契约根。
        seen: 可用的加速后端；``None`` 时现场探测。

    Returns:
        报告；判定失败时首个失败项即归因来源，被阻塞的项逐条记名。
    """
    facts = Facts(paths.base(root), accel.detect() if seen is None else seen)
    rows: list[Result] = []
    steps = (_root, _load, _assets, _engine, _smoke, _pick, _argv)
    for index, step in enumerate(steps):
        row = step(facts)
        rows.append(row)
        if not row.ok:
            rows.extend(_blocked(CHECKS[index + 1:len(steps)], row))
            break
    verdict = _status(facts, rows)
    row = _save(facts.home, verdict)
    rows.append(row)
    return Report(tuple(rows), verdict if row.ok else _status(facts, rows))


def _root(facts: Facts) -> Result:
    try:
        paths.ensure(facts.home)
        probe = facts.home / PROBE
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Result("root", False, f"运行根不可写: {exc}",
                      model.Reason.STATE_UNWRITABLE)
    return Result("root", True, str(facts.home))


def _load(facts: Facts) -> Result:
    try:
        facts.entry = manifest.load(paths.manifest(facts.home))
    except Failure as exc:
        return Result("manifest", False, str(exc), exc.reason)
    facts.digest = verify.digest(paths.manifest(facts.home))
    return Result("manifest", True, f"digest={facts.digest[:12]}")


def _assets(facts: Facts) -> Result:
    count = len(facts.entry.assets)
    try:
        verify.check(facts.home, facts.entry)
    except Failure as exc:
        return Result("assets", False, str(exc), exc.reason)
    return Result("assets", True, f"{count} asset(s) verified")


def _engine(facts: Facts) -> Result:
    try:
        runtime.check(facts.entry)
    except Failure as exc:
        return Result("engine", False, str(exc), exc.reason)
    return Result("engine", True, f"version={facts.entry.runtime.version}")


def _smoke(facts: Facts) -> Result:
    argv = [facts.entry.runtime.path, VERSION]
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=SMOKE_TIMEOUT, check=False)
    except OSError as exc:
        return Result("smoke", False, f"引擎无法执行: {exc}",
                      model.Reason.NO_RUNTIME)
    except subprocess.TimeoutExpired:
        return Result("smoke", False, f"引擎 {SMOKE_TIMEOUT}s 内未返回",
                      model.Reason.NO_RUNTIME)
    if done.returncode != 0:
        return Result("smoke", False, f"引擎退出码 {done.returncode}",
                      model.Reason.NO_RUNTIME)
    return Result("smoke", True, _banner(done))


def _pick(facts: Facts) -> Result:
    try:
        backend, reason = accel.pick(facts.entry.runtime.backends, facts.seen)
    except Failure as exc:
        return Result("backend", False, str(exc), exc.reason)
    facts.backend, facts.reason = backend, reason
    if reason is None:
        return Result("backend", True, backend.value)
    return Result("backend", True,
                  backend.value + model.REASON_SEP + reason.value)


def _argv(facts: Facts) -> Result:
    try:
        argv = serve.argv(facts.entry, facts.backend, facts.home)
    except Failure as exc:
        return Result("serve", False, str(exc), exc.reason)
    return Result("serve", True, f"{len(argv)} arg(s), {argv[0]}")


def _save(home: Path, want: model.Status) -> Result:
    try:
        status.store(home, want)
        kept = status.load(home)
    except OSError as exc:
        return Result("status", False, f"状态不可写: {exc}",
                      model.Reason.STATE_UNWRITABLE)
    if kept != want:
        return Result("status", False, "状态回读与写入不一致",
                      model.Reason.STATE_UNWRITABLE)
    return Result("status", True, f"state={want.state.value}")


def _status(facts: Facts, rows: list[Result]) -> model.Status:
    bad = next((row for row in rows if not row.ok), None)
    if bad is not None:
        reason = bad.reason if bad.reason is not None \
            else model.Reason.NO_RUNTIME
        return model.Status(model.State.FAILED, model.Backend.CPU,
                            reason, facts.digest)
    state = model.State.READY if facts.reason is None else model.State.DEGRADED
    return model.Status(state, facts.backend, facts.reason, facts.digest)


def _blocked(names: tuple[str, ...], cause: Result) -> tuple[Result, ...]:
    reason = cause.reason if cause.reason is not None \
        else model.Reason.NO_RUNTIME
    return tuple(Result(name, False, f"blocked by {cause.name}", reason)
                 for name in names)


def _banner(done: subprocess.CompletedProcess) -> str:
    lines = (done.stdout or done.stderr or "").strip().splitlines()
    return lines[0][:DETAIL_MAX] if lines else "exit 0"
