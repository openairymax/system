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
# 判失败只会让判据长期为红，失去信号价值。因此 err 按归属分四层，每层各
# 有一份预算，互不挪用：
#
#   自有   UNIT / _SYSTEMD_UNIT / _SYSTEMD_USER_UNIT / SYSLOG_IDENTIFIER 带
#          airymax- 或 airy- 前缀，即本产品自己的单元。另外，任何「dumped
#          core」崩溃行也归这一层：崩溃来自镜像交付的组件，是可以修的缺陷，
#          记进预算等于承认「可以崩」。这一层任何一条都是缺陷，必须为 0。
#   平台   内核转发的 ACPICA 诊断（「ACPI BIOS Error (bug)」「ACPI Error」）
#          与热区告警。它们说的是主板 DSDT 与固件表的缺陷，发行版不改固件
#          就消除不掉，每次开机逐字重现，且条目数随机型而变。这一层的预算
#          按「机器指纹」记账：条目跟着主板走，同一块盘换到另一台机器上，
#          旧主板的条目一条也不继承——否则一台机器的 ACPI 噪音会把别的
#          机器的预算撑大，棘轮就在换机时失效。
#   基底   其余可归属条目（单元名可读、但不是本产品的）。这一层是上游组件
#          自身的缺陷面，按条数与预算比较，随版本单调下降至 0。
#   未知   读不到归属的条目（纯文本日志、无单元字段）。unknown 不等于通过，
#          一律判失败，也不参与预算。
#
# 四层各自独立计数、独立比较。0.0.4 把「平台」与「基底」合成一个 foreign
# 计数时，ACPI 行与 rtkit 行共用一份预算：收掉一条基底缺陷腾出的额度会被
# 平台噪音吃掉，棘轮看似在收紧而实际没有；反过来，换代机器多出两条 ACPI
# 行就让判据变红，判据随即被当成噪声忽略。分账是让这两件事各自可判的前提。
#
# 归属取 UNIT 优先：systemd 自己发的「Failed to start X.」由 PID 1 落盘，
# _SYSTEMD_UNIT 是发起者 init.scope，被谈论的单元在 UNIT 字段。只读
# _SYSTEMD_UNIT 会把本产品单元失败记成外来噪声，用 285H 的 journald JSON
# 取证过（_SYSTEMD_UNIT=init.scope、UNIT=airymax-smoke-report.service）。
#
# 同一进程反复崩溃时每行只差 pid，故先做 pid 归一化再按「内容 + 单元」去重，
# 否则基线无法跨启动比较。单元名同样带易变分量：用户会话是 session-N.scope，
# 每次登录换号；coredump 是 systemd-coredump@<序号>-<pid>.service，每次崩溃
# 换号。不归一化时同一件事按登录次数与崩溃次数重复计账，285H 实测 ldapdb
# 一行噪声被 22 个会话记成 22 条，预算随之失去单调性。归一化后单元名只保留
# 「哪一类东西」这一层信息。
#
# 判定只认观测：unit 失败、自有 err、未知归属 err、任一层的条数超出它的
# 预算、包差集任一成立都判失败；任一证据源不可读判 unknown。退出码 0 干净、
# 1 有失败证据、3 证据不全。预算文件缺少当前机器所属的那一组同样判失败：
# 「读不到预算就放行」等于把棘轮拆掉，而一次静默放宽就足以让判据失去意义。
#
# 判定者不把自己的结论当证据：排除名单（--exclude）里的单元两侧都不计。验收
# 上报单元承载的是同一轮 TAP 的结论，把它算进证据就形成自指——它红过一次，
# 同一次开机的后续每一轮都会再读到这条旧结论，真实根因因此被盖住。

"""AirymaxOS system inventory (J-31)."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Budget", "DMI_REL", "DMI_ROOT", "Err", "MANIFEST", "OWNED_PREFIXES",
    "Survey",
    "budget_for", "collect", "collect_live", "declared", "diff", "dmi_live",
    "failed_units", "installed", "journal_errs", "machine_class", "machine_fp",
    "parse_budget", "read_text",
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

# 单元名里随会话/崩溃变化的编号。只收敛这三类，其余单元名原样参与去重：
# 会话 scope 与 coredump 单元的编号由运行期分配，不承载归属信息。
_CANON = (
    (re.compile(r"^session-\d+\.scope$"), "user-session"),
    (re.compile(r"^user@\d+\.service$"), "user-session"),
    (re.compile(r"^systemd-coredump@[0-9-]+\.service$"), "coredump"),
)

# 平台层的两条诊断形态。只认这两条：内核里其他 err（I/O 错误、驱动加载
# 失败）仍按基底计数，不能借平台层把真故障吞掉。热区告警限定在
# `thermal thermal_zoneN:` 这个内核前缀上，同一个驱动打印的其他告警不算。
_PLATFORM = re.compile(
    r"^(?:ACPI (?:BIOS Error \(bug\)|Error):|thermal thermal_zone\d+:)"
)

# 崩溃不属于可归因噪声。镜像交付的组件（桌面外壳、全局快捷键、显示后端）
# 崩一次就是本产品的缺陷，给这类行留预算等于把「崩了」改写成「在预算内崩
# 了」；因此无论崩溃发生在哪个单元，一律进自有层、恒判负。修代码，不加额度。
_CRASH = re.compile(r"dumped core|core dumped|Segmentation fault", re.I)

# DMI 里参与机器指纹的三项。产品名与主板名区分机型，BIOS 版本区分同一机型
# 的不同固件——固件升级后 ACPI 表的缺陷面会变，旧条目不该继续计额度。
_DMI = ("product_name", "board_name", "bios_version")

# DMI 取值里出现这些词即判为虚拟化环境。QEMU 的默认产品名是
# 「Standard PC (Q35 + ICH9, 2009)」，不含 QEMU 字样，故按机型名一并匹配。
_VIRT = ("qemu", "kvm", "bochs", "virtualbox", "vmware", "standard pc",
         "openstack", "bhyve")


@dataclass(frozen=True, slots=True)
class Err:
    """一条 err 日志：归一化后的正文、产生它的单元、归属与来源是否可判。"""

    text: str
    unit: str = ""
    owned: bool = False
    known: bool = True
    platform: bool = False


@dataclass(frozen=True, slots=True)
class Budget:
    """一层判据的预算：机器类别、平台层额度、基底层额度、这份预算是否成立。"""

    cls: str = "real"
    platform: int = 0
    base: int = 0
    read: bool = True


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
    budget: Budget = Budget()

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
    def platform_n(self) -> int:
        return sum(1 for err in self.errors if err.platform)

    @property
    def base_n(self) -> int:
        return (len(self.errors) - self.owned_n - self.unknown_n
                - self.platform_n)

    @property
    def logs_state(self) -> str:
        if not self.logs_read:
            return STATE_UNKNOWN
        if not self.budget.read:
            return STATE_ERRORS
        if (self.owned_n or self.unknown_n
                or self.platform_n > self.budget.platform
                or self.base_n > self.budget.base):
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
                "platform": self.platform_n,
                "base": self.base_n,
                "unknown": self.unknown_n,
                "class": self.budget.cls,
                "budget": {
                    "platform": self.budget.platform,
                    "base": self.budget.base,
                    "read": self.budget.read,
                },
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
            budget: Budget = Budget(),
            exclude: tuple[str, ...] = ()) -> Survey:
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
    raw = str(rec.get("UNIT") or rec.get("_SYSTEMD_UNIT")
              or rec.get("_SYSTEMD_USER_UNIT") or ident)
    kern = rec.get("_TRANSPORT") == "kernel" or ident == "kernel"
    owned = _own(raw, ident) or bool(_CRASH.search(text))
    return Err(text, _canon(raw), owned,
               platform=bool(kern and _PLATFORM.match(text)))


def _canon(unit: str) -> str:
    """把带运行期编号的单元名收敛成稳定名；其余名字原样返回。"""
    for pattern, name in _CANON:
        if pattern.match(unit):
            return name
    return unit


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


# 预算与机器身份 ---------------------------------------------------------------


def parse_budget(text: str, fp: str) -> dict[str, Budget]:
    """把 `err-budget` 解析成 {机器类别: 该组预算}。

    文件按机器类别分组，每组下按层分条，一条即一份额度：

        class=real
        platform <指纹> <日期> <摘要>
        base     <日期> <单元> <摘要>

    `platform` 的条目带机器指纹，只累加与当前机器相同的那些：条目跟着主板
    走，同一块盘换到另一台机器上不继承额度。`base` 的条目带单元名，写明
    「为何不是本产品的单元」。`owned` 与 `unknown` 两层的额度恒为 0，文件里
    出现这两层或认不出的条目即整组作废——给这两层留额度等于把判据拆掉。
    """
    groups: dict[str, list[int]] = {}
    bad: set[str] = set()
    cur = ""
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        word, *rest = body.split()
        if word.startswith("class="):
            cur = word.split("=", 1)[1]
            groups.setdefault(cur, [0, 0])
            continue
        if not cur or cur in bad:
            continue
        if word == "platform" and len(rest) >= 3:
            if rest[0] == fp:
                groups[cur][0] += 1
        elif word == "base" and len(rest) >= 3:
            groups[cur][1] += 1
        else:
            bad.add(cur)
    return {cls: Budget(cls, plat, base, cls not in bad)
            for cls, (plat, base) in groups.items()}


def budget_for(budgets: dict[str, Budget], cls: str) -> Budget:
    """取当前机器类别所属的那组预算；缺组即判负，不回退到另一组。"""
    return budgets.get(cls, Budget(cls=cls, read=False))


def machine_fp(values: dict[str, str]) -> str:
    """机器指纹：DMI 三项的短摘要，平台层预算按它记账。"""
    raw = "\x1f".join(values.get(key, "") for key in _DMI)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]


def machine_class(values: dict[str, str]) -> str:
    """机器类别：DMI 任一项命中虚拟化标记即 qemu，其余为 real。"""
    seen = " ".join(values.get(key, "") for key in _DMI).lower()
    return "qemu" if any(tag in seen for tag in _VIRT) else "real"


# 现场取数 -------------------------------------------------------------------


# DMI 在 sysfs 下的相对路径。`--root` 指向只读视图（单测沙箱、救援挂载）时
# 机器身份也取自那个视图，判据才可复现；不跟随视图就会读到宿主的主板。
DMI_REL = "devices/virtual/dmi/id"

DMI_ROOT = "/sys/" + DMI_REL


def dmi_live(root: str | Path = DMI_ROOT) -> dict[str, str]:
    """DMI 三项原始值；读不到的项记空串——设备树平台本就没有 DMI。

    空串仍然参与指纹：同一台无 DMI 的机器每次算出的指纹相同，跨机则不同，
    平台层预算的「换机不继承」在设备树平台上同样成立。
    """
    return {key: (read_text(Path(root) / key) or "").strip() for key in _DMI}


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
                 budget: Budget = Budget(),
                 exclude: tuple[str, ...] = ()) -> Survey:
    """现场取数；任何一路取不到都保留为 unknown，不做补位。"""
    return collect(units_live(), journal_live(), rpm_live(),
                   read_text(manifest), budget, exclude)
