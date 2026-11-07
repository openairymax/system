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
# 归属分层：err 日志不能一律判失败。基底与上游组件本身就会打印 err（rtkit
# 拿不到实时优先级、LDAP 插件找不到 schema），这些在真机上消除不掉；一律
# 判失败只会让判据长期为红，失去信号价值。因此 err 按单元归属分三层：
#
#   自有   UNIT / _SYSTEMD_UNIT / _SYSTEMD_USER_UNIT / SYSLOG_IDENTIFIER 带
#          airymax- 或 airy- 前缀，即本产品自己的单元。任何一条都是缺陷，
#          必须为 0。
#   固件   内核转发的 ACPICA 诊断（「ACPI BIOS Error (bug)」「ACPI Error」）。
#          它们说的是主板 DSDT 的表缺陷，发行版不改固件就消除不掉，且每次
#          开机逐字重现。本层仍原样列进明细供现场定位，但不计入预算、不判
#          失败；固件面由 `airy-observe fw` 与 T19–T21 单独判定。
#   外来   其余全部。按内容去重后与预算（budget）比较；预算随发行版本单调
#          下降至 0，构成棘轮。预算由调用方给出（发行版在 CI 侧以文件形式
#          版本化），本模块不内置任何魔数，缺省 0 即最严。
#
# 归属取 UNIT 优先：systemd 自己发的「Failed to start X.」由 PID 1 落盘，
# _SYSTEMD_UNIT 是发起者 init.scope，被谈论的单元在 UNIT 字段。只读
# _SYSTEMD_UNIT 会把本产品单元失败记成外来噪声，用 285H 的 journald JSON
# 取证过（_SYSTEMD_UNIT=init.scope、UNIT=airymax-smoke-report.service）。
#
# 同一进程反复崩溃时每行只差 pid，故先做 pid 归一化再按「内容 + 单元」去重，
# 否则基线无法跨启动比较。
#
# 判定只认观测：unit 失败、自有 err、外来 err 超预算、包差集任一成立都判
# 失败；任一证据源不可读判 unknown。退出码 0 干净、1 有失败证据、3 证据不全。
#
# 判定者不把自己的结论当证据：排除名单（--exclude）里的单元两侧都不计。验收
# 上报单元承载的是同一轮 TAP 的结论，把它算进证据就形成自指——它红过一次，
# 同一次开机的后续每一轮都会再读到这条旧结论，真实根因因此被盖住。

"""AirymaxOS system inventory (J-31)."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Err", "MANIFEST", "OWNED_PREFIXES", "Survey", "collect", "collect_live",
    "declared", "diff", "failed_units", "installed", "journal_errs",
    "read_text",
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

# 产品自有单元的前缀。这些名字由本仓库的 unit 文件产生，出现 err 即本产品
# 缺陷；基底与上游组件的单元不在其中，按预算做棘轮。
OWNED_PREFIXES = ("airymax-", "airy-")

_PID_DUMP = re.compile(r"\bProcess \d+ \(")

_PID_WORD = re.compile(r"\bpid[=: ]+\d+\b", re.ASCII)

# ACPICA 的表缺陷诊断。只认这两条前缀：内核里其他 err（I/O 错误、驱动加载
# 失败）仍按外来层计数，不能借固件层把真故障吞掉。
_FW = re.compile(r"^ACPI (?:BIOS Error \(bug\)|Error):")


@dataclass(frozen=True, slots=True)
class Err:
    """一条 err 日志：归一化后的正文、产生它的单元、归属与来源是否可判。"""

    text: str
    unit: str = ""
    owned: bool = False
    known: bool = True
    firmware: bool = False


@dataclass(frozen=True, slots=True)
class Survey:
    units: tuple[str, ...] = ()
    errors: tuple[Err, ...] = ()
    missing: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    units_read: bool = True
    logs_read: bool = True
    packs_read: bool = True
    want_n: int = 0
    have_n: int = 0
    budget: int = 0

    @property
    def units_state(self) -> str:
        if not self.units_read:
            return STATE_UNKNOWN
        return STATE_FAILED if self.units else STATE_OK

    @property
    def owned_n(self) -> int:
        return sum(1 for err in self.errors if err.owned)

    @property
    def unknown_n(self) -> int:
        return sum(1 for err in self.errors if not err.known)

    @property
    def firmware_n(self) -> int:
        return sum(1 for err in self.errors if err.firmware)

    @property
    def foreign_n(self) -> int:
        return (len(self.errors) - self.owned_n - self.unknown_n
                - self.firmware_n)

    @property
    def logs_state(self) -> str:
        if not self.logs_read:
            return STATE_UNKNOWN
        if self.owned_n or self.unknown_n or self.foreign_n > self.budget:
            return STATE_ERRORS
        return STATE_OK

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
            "logs": {
                "state": self.logs_state,
                "errors": [err.text for err in self.errors],
                "owned": self.owned_n,
                "foreign": self.foreign_n,
                "firmware": self.firmware_n,
                "unknown": self.unknown_n,
                "budget": self.budget,
            },
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
            rpm_text: str | None, manifest_text: str | None,
            budget: int = 0, exclude: tuple[str, ...] = ()) -> Survey:
    """四份来源文本合成一份清点；None 表示该来源读不到。

    exclude 里的单元两侧都不计：判定者不把自己的结论当证据。验收上报单元
    的失败由它所承载的那轮 TAP 判定驱动，把它算进证据就形成自指——它红过
    一次，同一次开机里后续每一轮都会再读一次这条旧结论，真实根因被盖住
    （285H 实测）。真实失败单元仍由上报路径独立列出。
    """
    skip = frozenset(exclude)
    units = () if units_text is None else failed_units(units_text)
    errors = () if journal_text is None else journal_errs(journal_text)
    have = () if rpm_text is None else installed(rpm_text)
    want = () if manifest_text is None else declared(manifest_text)
    missing, extra = diff(want, have)
    return Survey(
        units=tuple(name for name in units if name not in skip),
        errors=tuple(err for err in errors if err.unit not in skip),
        missing=missing,
        extra=extra,
        units_read=units_text is not None,
        logs_read=journal_text is not None,
        packs_read=manifest_text is not None and rpm_text is not None,
        want_n=len(want),
        have_n=len(have),
        budget=budget,
    )


def failed_units(text: str) -> tuple[str, ...]:
    """`systemctl --failed --no-legend --plain` 的首列，保持原顺序去重。"""
    names = [line.split()[0] for line in text.splitlines() if line.split()]
    return tuple(dict.fromkeys(name for name in names if _UNIT.match(name)))


def journal_errs(text: str) -> tuple[Err, ...]:
    """err 及以上日志行，按归属分层并归一化去重，顺序即发生顺序。

    输入按 `journalctl -o json` 的行解析，从而拿到产生日志的单元。纯文本行
    （`-o cat` 的既有产物）也接受，但拿不到归属，计入「未知」层并直接判
    失败——unknown 不等于通过，这是本模块对三类证据源的统一口径。
    """
    seen: set[tuple[str, str]] = set()
    out: list[Err] = []
    for line in text.splitlines():
        rec = _err(line)
        if rec is None:
            continue
        key = (rec.text, rec.unit)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return tuple(out)


def _err(line: str) -> Err | None:
    body = line.strip()
    if not body:
        return None
    if body.startswith("{"):
        rec = _json(body)
        if rec is not None:
            return rec
    text = _norm(_STAMP.sub("", body))
    return Err(text, known=False) if text else None


def _json(body: str) -> Err | None:
    try:
        rec = json.loads(body)
    except ValueError:
        return None
    if not isinstance(rec, dict):
        return None
    msg = rec.get("MESSAGE", "")
    if isinstance(msg, list):
        msg = bytes(msg).decode("utf-8", "replace")
    text = _norm(str(msg))
    if not text:
        return None
    ident = str(rec.get("SYSLOG_IDENTIFIER") or "")
    unit = str(rec.get("UNIT") or rec.get("_SYSTEMD_UNIT")
               or rec.get("_SYSTEMD_USER_UNIT") or ident)
    kern = rec.get("_TRANSPORT") == "kernel" or ident == "kernel"
    return Err(text, unit, _own(unit, ident),
               firmware=bool(kern and _FW.match(text)))


def _own(*names: str) -> bool:
    return any(name.startswith(OWNED_PREFIXES) for name in names if name)


def _norm(text: str) -> str:
    """pid 归一化：同一进程反复崩溃时每行只差 pid，不归一化基线无法比较。"""
    text = _PID_DUMP.sub("Process <pid> (", text)
    return _PID_WORD.sub("pid <pid>", text)


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
    return run(("journalctl", "-p", "err", "-b", "--no-pager", "-o", "json"))


def rpm_live() -> str | None:
    return run(("rpm", "-qa", "--qf", "%{NAME}\n"))


def collect_live(manifest: str | Path = MANIFEST,
                 budget: int = 0,
                 exclude: tuple[str, ...] = ()) -> Survey:
    """现场取数；任何一路取不到都保留为 unknown，不做补位。"""
    return collect(units_live(), journal_live(), rpm_live(),
                   read_text(manifest), budget, exclude)
