# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# RT 带宽测试共用的临时机器
#
# 判定层与命令行层需要同一棵「带内核接口与 cgroup 目录的临时机器」。把它
# 收在一处，两个测试文件不再各抄一份目录拼装逻辑，测试树本身也不会因为
# 抄漏某个文件而让两条用例悄悄测不同的东西。

"""Shared temporary machine for the RT bandwidth tests (X-01)."""

from __future__ import annotations

import shlex
import tempfile
import unittest
from pathlib import Path

from airymax_rt import cgroup, matrix, policy

MOUNT = "cpu,cpuacct"

SHIM = """#!/bin/sh
case "$1" in
  show) cat {state}/"$3" ;;
  set-property) echo "${{4#CPUWeight=}}" > {state}/CPUWeight ;;
  *) exit 2 ;;
esac
"""


class Machine:
    """一台临时机器：节点按用例增删，不依赖宿主内核。"""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.sys = base / "sys"
        self.proc = base / "proc"
        self.cgroup = base / "cgroup"
        self.cmdline = self.proc / "cmdline"
        self.sys.mkdir()
        self.proc.mkdir()
        self.cgroup.mkdir()
        self.cmdline.write_text("", encoding="ascii")

    def put(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="ascii")

    def realtime(self, value: str = "1") -> None:
        self.put(self.sys / "kernel" / "realtime", value)

    def budget(self, value: str = "950000") -> None:
        self.put(
            self.proc / "sys" / "kernel" / "sched_rt_runtime_us", value
        )

    def deadline(self) -> None:
        self.put(
            self.proc / "sys" / "kernel" / "sched_deadline_period_max_us",
            "100000000",
        )

    def governor(self, value: str = "performance") -> None:
        self.put(
            self.sys / "devices" / "system" / "cpu" / "cpu0" / "cpufreq"
            / "scaling_governor",
            value,
        )

    def clocksource(self, value: str = "tsc") -> None:
        self.put(
            self.sys / "devices" / "system" / "clocksource"
            / "clocksource0" / "current_clocksource",
            value,
        )

    def group(self) -> None:
        mount = self.cgroup / "cpu,cpuacct"
        self.put(mount / cgroup.V1_FILE, "950000")

    def pressure(self, avg10: str = "0.00") -> Path:
        path = self.proc / "pressure" / "cpu"
        self.put(path, f"some avg10={avg10} avg60=0.00 avg300=0.00 total=0")
        return path

    def loadavg(self, one: float = 0.0) -> Path:
        path = self.proc / "loadavg"
        self.put(path, f"{one:.2f} 0.00 0.00 1/100 1")
        return path

    def cpus(self, count: int = 4) -> Path:
        path = self.proc / "cpuinfo"
        body = "".join(f"processor\t: {index}\n" for index in range(count))
        path.write_text(body, encoding="ascii")
        return path

    def roots(self) -> matrix.Roots:
        return matrix.Roots(
            sys=str(self.sys),
            proc=str(self.proc),
            cgroup=str(self.cgroup),
            cmdline=str(self.cmdline),
        )


class Shim:
    """以文件为状态的假 systemctl：只实现共存策略用到的三个动作。"""

    def __init__(self, base: Path, load: str = "loaded",
                 weight: str = "50") -> None:
        self.state = base / "unit"
        self.state.mkdir(parents=True)
        (self.state / "LoadState").write_text(load + "\n", encoding="ascii")
        (self.state / "CPUWeight").write_text(weight + "\n", encoding="ascii")
        self.exe = base / "systemctl"
        self.exe.write_text(
            SHIM.format(state=shlex.quote(str(self.state))),
            encoding="ascii",
        )
        self.exe.chmod(0o755)

    def weight(self) -> str:
        return (self.state / "CPUWeight").read_text(encoding="ascii").strip()

    def drop(self, key: str) -> None:
        (self.state / key).unlink()

    def deny_set(self) -> None:
        """查询照常、写入被拒：验证回读失败会被如实报告。"""
        self.exe.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            f'  show) cat {shlex.quote(str(self.state))}/"$3" ;;\n'
            "  *) exit 1 ;;\n"
            "esac\n",
            encoding="ascii",
        )


class Tree(unittest.TestCase):
    """一棵临时机器：默认带可读的 sysctl 接口，cgroup 树按用例搭建。"""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.base = Path(self._dir.name)
        self.policy()

    def policy(self, total: str = "950000\n", period: str = "1000000\n"):
        total_path = self.base / "runtime"
        period_path = self.base / "period"
        total_path.write_text(total, encoding="ascii")
        period_path.write_text(period, encoding="ascii")
        return policy.Policy(
            total_path=str(total_path),
            period_path=str(period_path),
            cgroup_root=str(self.base),
        )

    def mount(self) -> Path:
        mount = self.base / MOUNT
        mount.mkdir()
        (mount / cgroup.V1_FILE).write_text("950000\n", encoding="ascii")
        return mount

    def global_mount(self) -> Path:
        """只有公平调度文件的挂载点：内核未编 RT 组调度，改由全局预算限流。"""
        mount = self.base / MOUNT
        mount.mkdir()
        (mount / cgroup.V1_FAIR).write_text("1024\n", encoding="ascii")
        return mount

    def slice(self, mount: Path, name: str, value: int) -> None:
        path = mount / name
        path.mkdir()
        (path / cgroup.V1_FILE).write_text(f"{value}\n", encoding="ascii")

    def all_slices(self, value: int = 190000) -> Path:
        mount = self.mount()
        for name in policy.DEFAULT_SLICES:
            self.slice(mount, name, value)
        return mount

    def args(self, cmd: str) -> list[str]:
        """把子命令接到本机临时路径上。"""
        return [
            cmd,
            "--proc",
            str(self.base / "runtime"),
            "--period",
            str(self.base / "period"),
            "--cgroup",
            str(self.base),
        ]
