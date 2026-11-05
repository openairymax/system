# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 启动卡点自证回归测试
#
# 以桩 systemctl 提供默认目标、作业表、单元属性与失败记账，验证四件事：
# 健康路径一个字都不输出；目标未达成且作业表逐字不变时一次性带出可归因
# 的事实；作业表在变（反复重启）同样带出事实并以 churn=1 区分；诊断行
# 不落 journal 的 err 桶。桩只复刻 systemctl 的对外输出，不复制任何判定
# 逻辑。

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import units_lint  # noqa: E402

SCRIPT = ROOT / "scripts" / "boot_watch.sh"

SYSTEMCTL = """#!/usr/bin/env bash
set -uo pipefail
cmd="$1"
shift
case "${cmd}" in
get-default)
    printf '%s\\n' "${FAKE_TARGET:-graphical.target}"
    ;;
is-active)
    unit="${!#}"
    case "${unit}" in
    *display-manager*|*lightdm*) exit "${FAKE_DM:-0}" ;;
    *) exit "${FAKE_ACTIVE:-0}" ;;
    esac
    ;;
list-jobs)
    n=0
    [ -r "${FAKE_SEQ}/n" ] && n="$(cat "${FAKE_SEQ}/n")"
    n=$((n + 1))
    printf '%s\\n' "${n}" >"${FAKE_SEQ}/n"
    f="${FAKE_SEQ}/job.${n}"
    [ -r "${f}" ] || f="${FAKE_SEQ}/job.last"
    cat "${f}"
    ;;
list-units)
    printf '%s\\n' "${FAKE_FAILED:-}"
    ;;
show)
    prop=''
    while [ "$#" -gt 0 ]; do
        case "$1" in
        -p) prop="$2"; shift 2 ;;
        *) shift ;;
        esac
    done
    case "${prop}" in
    ActiveState)
        if [ "${FAKE_ACTIVE:-0}" -eq 0 ]; then printf 'active\\n'
        else printf 'inactive\\n'; fi
        ;;
    SubState) printf 'dead\\n' ;;
    MainPID) printf '812\\n' ;;
    Result) printf 'success\\n' ;;
    *) printf 'unknown\\n' ;;
    esac
    ;;
esac
"""

JOB = "1 lightdm.service start running\n2 airy-boot-watch.service start running\n"

PLYMOUTH = """#!/usr/bin/env bash
set -uo pipefail
printf '%s\\n' "$*" >>"${FAKE_PLY_LOG:?}"
case "$1" in
--ping) exit "${FAKE_PLY_PING:-0}" ;;
esac
exit 0
"""


class BootWatchCase(unittest.TestCase):
    """构造桩 systemctl 与隔离的输出通道。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.bin = base / "bin"
        self.bin.mkdir()
        tool = self.bin / "systemctl"
        tool.write_text(SYSTEMCTL, encoding="utf-8")
        tool.chmod(0o755)
        ply = self.bin / "plymouth"
        ply.write_text(PLYMOUTH, encoding="utf-8")
        ply.chmod(0o755)
        self.ply = base / "plymouth.log"
        self.ply.write_text("", encoding="utf-8")
        self.seq = base / "seq"
        self.seq.mkdir()
        self.console = base / "console"
        self.console.write_text("", encoding="utf-8")
        self.serial = base / "ttyS0"
        # 串口通道必须是字符设备才会被脚本选中；以链接指向 /dev/null 得到
        # 一个可写、且内核 tty 名与实际一致（ttyS0）的替身。
        self.serial.symlink_to("/dev/null")
        self.active = base / "active"
        self.active.write_text("tty1\n", encoding="utf-8")

    def jobs(self, *texts: str) -> None:
        for idx, text in enumerate(texts, start=1):
            (self.seq / f"job.{idx}").write_text(text, encoding="utf-8")
        (self.seq / "job.last").write_text(texts[-1], encoding="utf-8")

    def run_watch(self, **env2: str):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.update(
            {
                "UI_CONSOLE": str(self.console),
                "AIRY_WATCH_SERIAL": str(self.serial),
                "AIRY_WATCH_ACTIVE": str(self.active),
                "FAKE_SEQ": str(self.seq),
                "AIRY_WATCH_POLL": "1",
                "AIRY_WATCH_WIN": "2",
                "FAKE_PLY_LOG": str(self.ply),
                "LANG": "C.UTF-8",
            }
        )
        env.update(env2)
        return subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_healthy_silent(self) -> None:
        self.jobs(JOB)
        res = self.run_watch(FAKE_ACTIVE="0")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "")

    def test_stuck_report(self) -> None:
        self.jobs(JOB)
        res = self.run_watch(FAKE_ACTIVE="1", FAKE_FAILED="")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(
            "AIRYMAX-BOOT-STUCK target=graphical.target active=inactive",
            res.stdout,
        )
        self.assertIn("AIRYMAX-BOOT-JOBS n=1", res.stdout)
        self.assertIn(
            "AIRYMAX-BOOT-JOB unit=lightdm.service type=start state=running",
            res.stdout,
        )
        self.assertIn(
            "AIRYMAX-BOOT-UNIT unit=lightdm.service active=inactive",
            res.stdout,
        )
        self.assertIn("AIRYMAX-BOOT-FAILED n=0", res.stdout)
        self.assertIn("AIRYMAX-BOOT-CONSOLE active=", res.stdout)
        self.assertIn("AIRYMAX-BOOT-CMDLINE argv=\"", res.stdout)
        self.assertIn("AIRYMAX-BOOT-HINT cmd=\"systemctl list-jobs;", res.stdout)

    def test_progress_reported(self) -> None:
        """作业表在变同样要报：反复重启的单元让作业表每轮都不同。"""
        self.jobs(JOB, JOB + "3 multi-user.target start waiting\n")
        res = self.run_watch(FAKE_ACTIVE="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("AIRYMAX-BOOT-STUCK target=graphical.target", res.stdout)
        self.assertIn("churn=1", res.stdout)

    def test_self_filtered(self) -> None:
        self.jobs(JOB)
        res = self.run_watch(FAKE_ACTIVE="1")
        self.assertNotIn("unit=airy-boot-watch", res.stdout)

    def test_no_dm_reported(self) -> None:
        """目标已激活但登录管理器没在跑：桌面从没起来，同样要报。"""
        self.jobs(JOB)
        res = self.run_watch(FAKE_ACTIVE="0", FAKE_DM="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(
            "AIRYMAX-BOOT-STUCK target=graphical.target active=active",
            res.stdout,
        )
        self.assertIn("reason=nodm", res.stdout)

    def test_headless_silent(self) -> None:
        """无图形剖面没有登录管理器属正常，不得据此判卡点。"""
        self.jobs(JOB)
        res = self.run_watch(
            FAKE_TARGET="multi-user.target", FAKE_ACTIVE="0", FAKE_DM="1"
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout, "")

    def test_no_stderr(self) -> None:
        """诊断行不得进入 journal 的 err 桶（V-15 判据之一）。"""
        self.jobs(JOB)
        res = self.run_watch(FAKE_ACTIVE="1")
        self.assertEqual(res.stderr, "")

    def test_console_mirror(self) -> None:
        self.jobs(JOB)
        self.run_watch(FAKE_ACTIVE="1")
        text = self.console.read_text(encoding="utf-8")
        self.assertIn("AIRYMAX-BOOT-STUCK", text)

    def test_serial_reached_when_console_is_vt(self) -> None:
        """/dev/console 落到 tty0 时，报告仍须抵达 /dev/console 与串口两侧。

        产品命令行是 console=ttyS0,115200 console=tty0：ttyS0 在活动列表里，
        但 /dev/console 指向 tty0。按「在列表里即同源」去重会让串口现场失明。
        """
        self.jobs(JOB)
        self.active.write_text("ttyS0 tty0\n", encoding="utf-8")
        res = self.run_watch(FAKE_ACTIVE="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("AIRYMAX-BOOT-STUCK", res.stdout)
        self.assertIn(
            "AIRYMAX-BOOT-STUCK", self.console.read_text(encoding="utf-8")
        )

    def test_serial_dedup_when_it_is_console(self) -> None:
        """串口正是 /dev/console 的落点时只写一份，不在串口重复出行。"""
        self.jobs(JOB)
        self.active.write_text("tty0 ttyS0\n", encoding="utf-8")
        res = self.run_watch(FAKE_ACTIVE="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("AIRYMAX-BOOT-STUCK", res.stdout)
        self.assertEqual(self.console.read_text(encoding="utf-8"), "")

    def test_failed_count(self) -> None:
        self.jobs(JOB)
        res = self.run_watch(
            FAKE_ACTIVE="1", FAKE_FAILED="lightdm.service loaded failed\n"
        )
        self.assertIn("AIRYMAX-BOOT-FAILED n=1", res.stdout)

    def test_splash_hidden_on_stuck(self) -> None:
        """rhgb 下屏幕被 plymouth 接管，卡点结论必须先把画面收起来。"""
        self.jobs(JOB)
        self.run_watch(FAKE_ACTIVE="1")
        self.assertIn("--hide-splash", self.ply.read_text(encoding="utf-8"))

    def test_splash_kept_when_healthy(self) -> None:
        self.jobs(JOB)
        self.run_watch(FAKE_ACTIVE="0")
        self.assertEqual(self.ply.read_text(encoding="utf-8"), "")

    def test_splash_kept_when_daemon_absent(self) -> None:
        """命令行没有 rhgb 时 plymouth 只是装着的，不得去碰它。"""
        self.jobs(JOB)
        self.run_watch(FAKE_ACTIVE="1", FAKE_PLY_PING="1")
        self.assertEqual(self.ply.read_text(encoding="utf-8"), "--ping\n")


class LintCase(unittest.TestCase):
    """脚本静态契约校验器。"""

    def test_lint_ok(self) -> None:
        self.assertEqual(units_lint.check_script(SCRIPT), [])


if __name__ == "__main__":
    unittest.main()
