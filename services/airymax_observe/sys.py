# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# 系统完整性清点
#
# 0.0.4 的收口判据把「装好了」与「装完整了」分开：机器能进桌面，不等于系统
# 自洽。三件事各有独立证据源，不能互相替代：
#
#   units   本次开机处于 failed 的单元。systemd 自己的判定，来源是
#           `systemctl --failed`，不解析日志、不猜原因。
#   logs    本次开机 err 级别及以上的内核/服务日志行。来源是
#           `journalctl -p err -b`，逐行去重后原样保留，供现场定位。
#   packs   镜像交付的包集合与实际安装集合之差。交付集合是装机时写下的
#           /etc/airymaxos-packages，安装集合来自 rpm 数据库；两者之差
#           分别叫 missing（该有而没有）与 extra（多出来的孤儿包）。
#
# 三个面各自三态：ok / 有问题 / unknown。unknown 只表示「读不到证据」，
# 绝不等于通过——与固件清点同一口径。缺证据时退出码必须能与「干净」区分。
#
# 判定只认观测：unit 失败、err 日志、包差集任一非空都判失败；任一证据源
# 不可读判 unknown。退出码 0 干净、1 有失败证据、3 证据不全。

"""AirymaxOS system inventory (J-31)."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MANIFEST", "Survey", "collect", "collect_live", "declared", "diff",
    "failed_units", "installed", "journal_errs", "read_text",
]

MANIFEST = "/etc/airymaxos-packages"

STATE_OK = "ok"

STATE_UNKNOWN = "unknown"

STATE_DRIFT = "drift"

STATE_ERRORS = "errors"

STATE_FAILED = "failed"

_UNIT = re.compile(
    r"^[A-Za-z0-9@._:\\-]+\."
    r"(?:service|socket|timer|mount|target|path|slice|scope"
    r"|swap|automount|device)$"
)

_STAMP = re.compile(r"^\[\s*[0-9.]+\]\s*")


@dataclass(frozen=True, slots=True)
class Survey:
    units: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    units_read: bool = True
    logs_read: bool = True
    packs_read: bool = True
    want_n: int = 0
    have_n: int = 0

    @property
    def units_state(self) -> str:
        if not self.units_read:
            return STATE_UNKNOWN
        return STATE_FAILED if self.units else STATE_OK

    @property
    def logs_state(self) -> str:
        if not self.logs_read:
            return STATE_UNKNOWN
        return STATE_ERRORS if self.errors else STATE_OK

    @property
    def packs_state(self) -> str:
        if not self.packs_read:
            return STATE_UNKNOWN
        return STATE_DRIFT if self.missing or self.extra else STATE_OK

    @property
    def state(self) -> str:
        if STATE_FAILED in self._states or STATE_ERRORS in self._states:
            return STATE_FAILED
        if STATE_DRIFT in self._states:
            return STATE_DRIFT
        return STATE_UNKNOWN if STATE_UNKNOWN in self._states else STATE_OK

    @property
    def _states(self) -> tuple[str, ...]:
        return (self.units_state, self.logs_state, self.packs_state)

    def dump(self) -> dict[str, object]:
        return {
            "units": {"state": self.units_state, "failed": list(self.units)},
            "logs": {"state": self.logs_state, "errors": list(self.errors)},
            "packages": {
                "state": self.packs_state,
                "declared": self.want_n,
                "installed": self.have_n,
                "missing": list(self.missing),
                "extra": list(self.extra),
            },
            "state": self.state,
        }


def collect(units_text: str | None, journal_text: str | None,
            rpm_text: str | None, manifest_text: str | None) -> Survey:
    """四份来源文本合成一份清点；None 表示该来源读不到。"""
    have = () if rpm_text is None else installed(rpm_text)
    want = () if manifest_text is None else declared(manifest_text)
    missing, extra = diff(want, have)
    return Survey(
        units=() if units_text is None else failed_units(units_text),
        errors=() if journal_text is None else journal_errs(journal_text),
        missing=missing,
        extra=extra,
        units_read=units_text is not None,
        logs_read=journal_text is not None,
        packs_read=manifest_text is not None and rpm_text is not None,
        want_n=len(want),
        have_n=len(have),
    )


def failed_units(text: str) -> tuple[str, ...]:
    """`systemctl --failed --no-legend --plain` 的首列，保持原顺序去重。"""
    names = [line.split()[0] for line in text.splitlines() if line.split()]
    return tuple(dict.fromkeys(name for name in names if _UNIT.match(name)))


def journal_errs(text: str) -> tuple[str, ...]:
    """err 及以上日志行；去掉时间戳前缀后按内容去重，顺序即发生顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        body = _STAMP.sub("", line.strip())
        if body and body not in seen:
            seen.add(body)
            out.append(body)
    return tuple(out)


def installed(text: str) -> tuple[str, ...]:
    """`rpm -qa --qf '%{NAME}\\n'` 的包名集合。"""
    return tuple(sorted({line.strip() for line in text.splitlines()
                         if line.strip()}))


def declared(text: str) -> tuple[str, ...]:
    """镜像交付清单：一行一个包名，# 起注释，空行忽略。"""
    return tuple(sorted({line.split()[0] for line in text.splitlines()
                         if line.strip() and not line.lstrip().startswith("#")}))


def diff(declared_names: tuple[str, ...],
         installed_names: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    """返回 (missing, extra)；两侧均已排序。"""
    want = set(declared_names)
    have = set(installed_names)
    return (tuple(sorted(want - have)), tuple(sorted(have - want)))


# 现场取数 -------------------------------------------------------------------


def read_text(path: str | Path | None) -> str | None:
    """读一个来源文件；None 或不可读时返回 None（调用方据此判 unknown）。"""
    if path is None:
        return None
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def run(argv: tuple[str, ...]) -> str | None:
    """跑一条只读命令；命令不存在或非零退出都按「读不到」处理。"""
    try:
        res = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return res.stdout if res.returncode == 0 else None


def units_live() -> str | None:
    return run(("systemctl", "--failed", "--no-legend", "--plain",
                "--no-pager"))


def journal_live() -> str | None:
    return run(("journalctl", "-p", "err", "-b", "--no-pager", "-o", "cat"))


def rpm_live() -> str | None:
    return run(("rpm", "-qa", "--qf", "%{NAME}\n"))


def collect_live(manifest: str | Path = MANIFEST) -> Survey:
    """现场取数；任何一路取不到都保留为 unknown，不做补位。"""
    return collect(units_live(), journal_live(), rpm_live(),
                   read_text(manifest))
