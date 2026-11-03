# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 内存与换页上限的补齐
#
# slice 单元里声明 MemoryMax 之后，越过上限的页并不会被拒绝，而是被换出去。
# 285H 现场：会话 scope 记账 swap 13.8G，整机 swap 16G 只剩 1G，回收路径
# 长期在「换出—再缺页」之间往返，整机因此卡死而不是某个进程失败。要让
# 内存与换页合计也有天花板，必须同时声明 MemorySwapMax。
#
# 但只有 cgroup v2 上 systemd 才会把 MemorySwapMax 写成 memory.swap.max；
# 目标机的 hybrid 层级上实测：瞬态服务声明 MemorySwapMax=300M，systemctl
# show 回报 314572800，cgroup 里的 memory.memsw.limit_in_bytes 却仍是
# infinity。属性存在、值也对，实际没有任何约束——这正是本包存在的理由：
# 补上 systemd 在 v1 上不写的那一半。
#
# 声明仍然只有一处：百分比从 slice 单元与 drop-in 里读，代码不另立一张
# 表，改上限只改单元文件。v1 的 memsw 记账是「内存 + 换页」合计，所以
# 目标值取两者之和；两个百分比相加正好是物理内存一整个，常驻部分因此
# 停在九成或八成半。v2 上 systemd 自己会写，本模块只在缺失或不一致时
# 补写，正常路径下只做核对。
#
# 顺序不是随便排的：memsw 上限不得低于内存上限，内核对这种写入直接
# 返回 EINVAL。启动早期 slice 目录可能是本单元刚建出来的，memory 上限
# 还是 infinity，此时先写 memsw 必然失败——现场实测如此。所以第一步是
# 把内存上限压到单元声明的 MemoryMax（已一致就跳过），第二步才是合计
# 上限。systemd 之后接管这个 slice 时写的是同一个值，不会与这里打架。
#
# 写入前先比对当前用量：目标低于已用量时不得把上限压到用量以下，memsw 上限
# 一旦低于在用量，内核会当场回收或 OOM，把一条配置动作变成一次事故——这是
# 「装完包就杀掉正在跑的模型」的那条路径。
#
# 但也不能因此什么都不做。285H 现场：声明目标 31 GiB，memsw 在用量 40.6 GiB，
# 守卫于是永久拒写，换页从此没有任何上界，整机在全局回收里停住两三分钟。
# 折中做法是「只用余量封顶」：上限取 max(目标, 在用量 + 余量)，且绝不高于
# 现值——每轮只允许收紧、不允许放松，用量回落后自然收敛到目标。写入值永远
# 不低于在用量，因此不会触发当场回收；越过封顶的增长被挡在组内，代价是一个
# 实例被杀，而不是整机停顿。只读判定仍报 tight，因为限额确实不在目标上。
#
# 设计依据：docs-linux/170-performance/07-rt-matrix.md §5.1~§5.3。

"""AirymaxOS memory and swap ceiling (X-05)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ABSENT",
    "APPLIED",
    "DIFFERS",
    "DEFAULT_MEMINFO",
    "DEFAULT_ROOT",
    "DEFAULT_UNITDIR",
    "HEADROOM_PCT",
    "HELD",
    "OK",
    "TIGHT",
    "Bound",
    "Report",
    "Step",
    "apply",
    "bounds",
    "inspect",
]

DEFAULT_MEMINFO = "/proc/meminfo"

DEFAULT_ROOT = "/sys/fs/cgroup"

DEFAULT_UNITDIR = "/usr/lib/systemd/system"

MEMINFO_KEY = "MemTotal"

CONTROLLERS = "cgroup.controllers"

MEM_DIR = "memory"

V1_MEM = "memory.limit_in_bytes"

V1_MEM_USAGE = "memory.usage_in_bytes"

V1_MEMSW = "memory.memsw.limit_in_bytes"

V1_USAGE = "memory.memsw.usage_in_bytes"

V2_SWAP = "memory.swap.max"

# 声明来源与它在 memory 层级里的目录。两层不是分区而是两个独立天花板：
# agent 侧由单元拉起，会话池兜住 AirymaxRT 直接 exec 的进程。单元名与
# 目录名不同是因为含 `-` 的 slice 会展开成父子两级。
SOURCES: tuple[tuple[str, str], ...] = (
    ("airymax-agent.slice", "airymax.slice/airymax-agent.slice"),
    ("user.slice.d/50-airymax-memory.conf", "user.slice"),
)

OK = "ok"

APPLIED = "applied"

DIFFERS = "differs"

TIGHT = "tight"

HELD = "held"

ABSENT = "absent"

# 封顶余量：声明上限的百分比。取太小会把正常增长也挡在组内，取太大会让
# 封顶失去意义；按整机内存的百分之几留出一次推理请求的峰值即可。
HEADROOM_PCT = 5


@dataclass(frozen=True, slots=True)
class Bound:
    """一条声明：上限来自哪份单元、落在哪个 slice、内存与换页各多少。"""

    unit: str
    path: str
    mem: int
    swap: int

    @property
    def total(self) -> int:
        """memsw 上限：内存与换页合计。"""
        return self.mem + self.swap


@dataclass(slots=True)
class Step:
    """一个 slice 的落地结果。"""

    path: str
    want: int
    have: int | None
    usage: int | None
    state: str
    note: str = ""

    def dump(self) -> dict[str, object]:
        return {
            "path": self.path,
            "want": self.want,
            "have": self.have,
            "usage": self.usage,
            "state": self.state,
            "note": self.note,
        }


@dataclass(slots=True)
class Report:
    """一次判定的全部结果；layout 为空表示记账树不可用。"""

    layout: str
    steps: list[Step] = field(default_factory=list)

    def dump(self) -> dict[str, object]:
        return {
            "layout": self.layout,
            "steps": [item.dump() for item in self.steps],
        }

    def count(self, state: str) -> int:
        return sum(1 for item in self.steps if item.state == state)


def _scan(path: Path) -> dict[str, str]:
    """读单元文件里的两条内存声明，忽略注释与空行。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("MemoryMax=", "MemorySwapMax=")):
            name, _, value = line.partition("=")
            found[name] = value.strip()
    return found


def _page() -> int:
    """内核按页记账，写进去的上限会被取整到页边界。"""
    try:
        return os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return 4096


def _bytes(text: str | None, total: int) -> int | None:
    """把百分比或绝对字节换成字节数；无法判读时返回 None。

    目标先按页向下对齐：285H 实测写入 29324840140 回读 29324836864，
    差值不足一页是内核取整而不是写入失败，不对齐会把每次落地都误判成
    absent，运维看到的就是「写了也不生效」。
    """
    if not text:
        return None
    if text.endswith("%"):
        try:
            value = total * int(text[:-1], 10) // 100
        except ValueError:
            return None
    else:
        try:
            value = int(text, 10)
        except ValueError:
            return None
    return value - value % _page()


def _total(meminfo: str) -> int:
    """读 MemTotal；单位是 kB，换算成字节。"""
    try:
        text = Path(meminfo).read_text(encoding="ascii")
    except OSError:
        return 0
    for line in text.splitlines():
        if not line.startswith(MEMINFO_KEY + ":"):
            continue
        fields = line.partition(":")[2].split()
        try:
            return int(fields[0], 10) * 1024
        except (IndexError, ValueError):
            return 0
    return 0


def bounds(unitdir: str, meminfo: str = DEFAULT_MEMINFO) -> list[Bound]:
    """收集两份单元里的声明；缺一条百分比就跳过该条并留待报告。"""
    total = _total(meminfo)
    if total <= 0:
        return []
    found: list[Bound] = []
    for unit, path in SOURCES:
        values = _scan(Path(unitdir) / unit)
        mem = _bytes(values.get("MemoryMax"), total)
        swap = _bytes(values.get("MemorySwapMax"), total)
        if mem is None or swap is None:
            continue
        found.append(Bound(unit, path, mem, swap))
    return found


def _layout(root: str) -> tuple[str, Path, str]:
    """识别记账布局，返回（类型，memory 根目录，换页上限文件名）。

    memsw 接口整组缺失说明内核没有编入换页记账，换页上限无从落地；
    这与「文件在但写入被拒」是两种故障，分别报 absent 与写入未生效，
    排障时才不会绕到权限上。285H 属于后者：文件齐全、记账正常，只是
    systemd 在 hybrid 层级上不写。
    """
    base = Path(root)
    if (base / CONTROLLERS).is_file():
        return "v2", base, V2_SWAP
    mem = base / MEM_DIR
    if (mem / V1_MEMSW).is_file():
        return "v1", mem, V1_MEMSW
    if (mem / V1_USAGE).is_file():
        return "v1", mem, ""
    return "", base, ""


def _read(path: Path) -> int | None:
    """读上限值；``max`` 与 v1 的哨兵值都表示不限，按不可解析处理。"""
    try:
        text = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        value = int(text, 10)
    except ValueError:
        return None
    return value if value < (1 << 62) else None


def _put(path: Path, value: int) -> bool:
    """写入后回读；cgroupfs 会静默夹值，只信返回值等于没写。"""
    try:
        path.write_text(f"{value}\n", encoding="ascii")
    except OSError:
        return False
    return _read(path) == value


def _hold(want: int, usage: int, head: int, have: int | None) -> int:
    """封顶值：不低于在用量、不高于现值，只在目标之上保留一份余量。"""
    target = want if want > usage + head else usage + head
    if have is not None and have < target:
        target = have
    target -= target % _page()
    return usage if target < usage else target


def _ceil(
    home: Path,
    name: str,
    want: int,
    usage: int | None,
    plan: bool,
    head: int = 0,
) -> Step:
    """把单个上限调到 want；已一致、超用、未落地各有一种结论。"""
    path = home / name
    if not path.is_file():
        return Step(name, want, None, usage, ABSENT, f"缺少 {name}")
    have = _read(path)
    if have == want:
        return Step(name, want, have, usage, OK)
    if usage is not None and usage > want:
        if not plan or head <= 0:
            return Step(name, want, have, usage, TIGHT, "在用量已超目标")
        want = _hold(want, usage, head, have)
        if have == want:
            return Step(name, want, have, usage, HELD, "现值已在封顶之内")
        if not _put(path, want):
            return Step(name, want, _read(path), usage, ABSENT, "写入未生效")
        note = "在用量已超目标，按现值加余量封顶"
        return Step(name, want, _read(path), usage, HELD, note)
    if not plan:
        return Step(name, want, have, usage, DIFFERS)
    if not _put(path, want):
        return Step(name, want, _read(path), usage, ABSENT, "写入未生效")
    return Step(name, want, _read(path), usage, APPLIED)


def _merge(mem: Step | None, swap: Step) -> str:
    """合并内存与换页两侧结论；任何一侧没落地都不得被另一侧掩盖。"""
    if swap.state in (ABSENT, TIGHT):
        return swap.state
    states = {swap.state}
    if mem is not None:
        states.add(mem.state)
    for state in (HELD, DIFFERS, APPLIED):
        if state in states:
            return state
    return OK


def _step(root: Path, file: str, item: Bound, plan: bool) -> Step:
    """处理一个 slice：先压低内存上限，再设合计上限，超用时封顶。"""
    home = root / item.path
    if not home.is_dir():
        try:
            home.mkdir(parents=True, exist_ok=True)
        except OSError:
            return Step(
                item.path, item.total, None, None, ABSENT, "目录不可创建"
            )
    head = item.total * HEADROOM_PCT // 100
    if file == V1_MEMSW:
        mem = _ceil(
            home, V1_MEM, item.mem, _read(home / V1_MEM_USAGE), plan, head
        )
        if mem.state in (ABSENT, TIGHT):
            note = f"{mem.note}（{V1_MEM}）"
            return Step(
                item.path, mem.want, mem.have, mem.usage, mem.state, note
            )
        landed = mem.have if mem.have is not None else mem.want
        want, usage = landed + item.swap, _read(home / V1_USAGE)
    else:
        mem = None
        want, usage = item.swap, None
    swap = _ceil(home, file, want, usage, plan, head)
    note = swap.note or (mem.note if mem is not None else "")
    return Step(item.path, swap.want, swap.have, swap.usage,
                _merge(mem, swap), note)


def _report(
    root: str,
    unitdir: str,
    meminfo: str,
    plan: bool,
) -> Report:
    """两种布局共用一条判定路径，plan 只决定末步是否落笔。"""
    kind, base, file = _layout(root)
    report = Report(kind)
    if not kind:
        return report
    found = bounds(unitdir, meminfo)
    if not file:
        report.steps = [
            Step(item.path, item.total, None, None, ABSENT, "未开 memsw 记账")
            for item in found
        ]
        return report
    report.steps = [_step(base, file, item, plan) for item in found]
    return report


def inspect(
    root: str = DEFAULT_ROOT,
    unitdir: str = DEFAULT_UNITDIR,
    meminfo: str = DEFAULT_MEMINFO,
) -> Report:
    """只读判定，不写任何接口。"""
    return _report(root, unitdir, meminfo, False)


def apply(
    root: str = DEFAULT_ROOT,
    unitdir: str = DEFAULT_UNITDIR,
    meminfo: str = DEFAULT_MEMINFO,
) -> Report:
    """落地上限并回读；在用量已超目标时按现值加余量封顶。"""
    return _report(root, unitdir, meminfo, True)