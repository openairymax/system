# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 开机九阶段打印契约回归测试
#
# 在临时目录里以桩 logger 驱动 ui.sh，逐字比对四种动词的输出形态、
# 契约表（序号、名称、显示宽度、总数）、三类契约拒绝，以及只读判定
# （ui_due）不占标记的语义；宽度在这里独立用 unicodedata 复算，避免表里的
# 宽度与终端实际渲染分叉。

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
UI = SCRIPTS / "ui.sh"


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.journal = self.dir / "journal.log"
        self.logger = self.dir / "logger"
        self.logger.write_text(
            "#!/usr/bin/env bash\n"
            'printf "%s\\n" "${@: -1}" >>"${UI_JOURNAL:?}"\n',
            encoding="utf-8",
        )
        self.logger.chmod(0o755)

    def run_ui(self, body: str, **env2: str) -> subprocess.CompletedProcess:
        script = f". {UI}\n{body}\n"
        env = dict(os.environ)
        env["PATH"] = f"{self.dir}:{env['PATH']}"
        env["UI_JOURNAL"] = str(self.journal)
        env["LANG"] = "C.UTF-8"
        env.update(env2)
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def run_ui_to_file(self, body: str, target: Path, **env2: str) -> int:
        script = f". {UI}\nexec >\"${{UI_CONSOLE}}\"\n{body}\n"
        env = dict(os.environ)
        env["PATH"] = f"{self.dir}:{env['PATH']}"
        env["UI_JOURNAL"] = str(self.journal)
        env["UI_CONSOLE"] = str(target)
        env["LANG"] = "C.UTF-8"
        env.update(env2)
        with target.open("w", encoding="utf-8") as handle:
            return subprocess.run(
                ["bash", "-c", script],
                stdout=handle,
                stderr=subprocess.DEVNULL,
                env=env,
                check=False,
            ).returncode

    def stage_table(self) -> list[tuple[str, str, str]]:
        out = self.run_ui('printf "%s\\n" "${UI_STAGES[@]}"')
        self.assertEqual(out.returncode, 0, out.stderr)
        return [tuple(row.split("|")) for row in out.stdout.splitlines()]

    def test_stage_table_is_the_contract(self):
        rows = self.stage_table()
        self.assertEqual(len(rows), 9, "阶段总数必须为九")
        self.assertEqual([r[0] for r in rows], [str(n) for n in range(1, 10)])
        names = [r[1] for r in rows]
        self.assertEqual(len(set(names)), len(names), "阶段名必须唯一")
        for _, name, columns in rows:
            self.assertEqual(int(columns), width(name), f"「{name}」宽度登记不符")

    def test_begin_line_names_the_stage(self):
        out = self.run_ui("ui_begin 数据分区")
        self.assertEqual(out.returncode, 0, out.stderr)
        line = out.stdout.rstrip("\n")
        self.assertTrue(line.startswith("[ 3/9 ] 数据分区"), line)
        self.assertTrue(line.endswith("..."), line)

    def test_ok_line_carries_key_values(self):
        out = self.run_ui("ui_begin 数据分区")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_ui("ui_ok 数据分区 mounted=/data dev=/dev/nvme0n1p4")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout, "[ 3/9 ] 数据分区        ok   mounted=/data dev=/dev/nvme0n1p4\n"
        )

    def test_note_and_fail_tokens(self):
        out = self.run_ui(
            'ui_note 运行时带宽 runtime=950000 group=yes\n'
            'ui_fail 桌面就绪 "lightdm 未 active（mode=gui）"'
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(
            lines[0], "[ 4/9 ] 运行时带宽      note runtime=950000 group=yes"
        )
        self.assertEqual(
            lines[1], "[ 8/9 ] 桌面就绪        FAIL lightdm 未 active（mode=gui）"
        )

    def test_fail_without_criteria_is_not_silent(self):
        out = self.run_ui("ui_fail 桌面就绪")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "[ 8/9 ] 桌面就绪        FAIL\n")

    def test_columns_align_across_names(self):
        out = self.run_ui("ui_ok 基础挂载 x=1\nui_ok 桌面RT共存 x=1")
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = out.stdout.splitlines()
        cols = [width(row[: row.index("ok")]) for row in rows]
        self.assertEqual(cols[0], cols[1], "token 列必须对齐")

    def test_beat_reports_used_and_limit(self):
        out = self.run_ui("ui_beat 换页上限 40 60")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout, "[ 5/9 ] 换页上限        wait used=40s limit=60s\n"
        )

    def test_journal_receives_the_same_line(self):
        out = self.run_ui("ui_ok 自检与取证 probe=clean")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            self.journal.read_text(encoding="utf-8"), out.stdout
        )

    def test_console_gets_the_line_when_stdout_is_a_pipe(self):
        console = self.dir / "console"
        console.write_text("", encoding="utf-8")
        out = self.run_ui("ui_ok 基础挂载 x=1", UI_CONSOLE=str(console))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(console.read_text(encoding="utf-8"), out.stdout)

    def test_console_line_is_not_doubled_when_stdout_is_console(self):
        console = self.dir / "console"
        rc = self.run_ui_to_file("ui_ok 桌面就绪 unit=lightdm", console)
        self.assertEqual(rc, 0)
        self.assertEqual(
            console.read_text(encoding="utf-8"),
            "[ 8/9 ] 桌面就绪        ok   unit=lightdm\n",
        )

    def test_total_comes_from_the_table(self):
        out = self.run_ui("ui_total")
        self.assertEqual(out.stdout, "9\n")

    def _chans(self, active_text: str) -> str:
        """活动控制台末位决定 /dev/console 的落点，通道表据此成形。"""
        serial = self.dir / "ttyS0"
        serial.symlink_to("/dev/null")
        active = self.dir / "active"
        active.write_text(active_text, encoding="utf-8")
        out = self.run_ui(
            'printf "%s\\n" "${CON_CH[*]}"',
            AIRY_CON_SERIAL=str(serial),
            AIRY_CON_ACTIVE=str(active),
            UI_CONSOLE=str(self.dir / "console"),
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_serial_leads_when_console_is_the_vt(self):
        """命令行末位是 tty0 时 /dev/console 指本地屏，串口必须另写一份。"""
        self.assertEqual(
            self._chans("ttyS0 tty0\n"),
            f"{self.dir}/ttyS0 {self.dir}/console",
        )

    def test_serial_dedup_when_it_is_the_console(self):
        self.assertEqual(self._chans("tty0 ttyS0\n"), f"{self.dir}/ttyS0")

    def test_begin_rejects_usage(self):
        out = self.run_ui("ui_begin")
        self.assertEqual(out.returncode, 64, out.stderr)
        self.assertEqual(out.stdout, "")
        self.assertIn("用法: ui_begin <阶段名>", out.stderr)

    def test_begin_rejects_unknown_stage(self):
        out = self.run_ui("ui_begin 桌面美容")
        self.assertEqual(out.returncode, 5, out.stderr)
        self.assertIn("不在契约表", out.stderr)

    def test_unknown_stage_is_rejected(self):
        out = self.run_ui("ui_ok 桌面美容 x=1")
        self.assertEqual(out.returncode, 5, out.stderr)
        self.assertIn("不在契约表", out.stderr)

    def test_contract_error_reaches_console(self):
        """报错时屏幕上没有阶段行，诊断必须自己落到同一条通道。"""
        console = self.dir / "console"
        console.write_text("", encoding="utf-8")
        out = self.run_ui("ui_ok 桌面美容 x=1", UI_CONSOLE=str(console))
        self.assertEqual(out.returncode, 5, out.stderr)
        self.assertIn(
            "airymax-boot: 阶段名不在契约表: 桌面美容",
            console.read_text(encoding="utf-8"),
        )

    def test_sourcing_twice_is_harmless(self):
        out = self.run_ui(f". {UI}\n. {UI}\nui_ok 基础挂载 x=1")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(out.stdout.splitlines()), 1)


class GateCase(Case):
    """一次开机只打一行的看门。"""

    def gate(
        self,
        name: str = "电源与热治理",
        state: str | None = None,
    ) -> subprocess.CompletedProcess:
        res = self.run_ui(
            f'ui_gate "{name}"',
            UI_STATE=state or str(self.dir / "ui"),
        )
        return res

    def test_first(self) -> None:
        res = self.gate()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.dir / "ui" / "6").exists())

    def test_second(self) -> None:
        self.assertEqual(self.gate().returncode, 0)
        self.assertEqual(self.gate().returncode, 1)

    def test_unknown(self) -> None:
        res = self.gate("不存在的阶段")
        self.assertEqual(res.returncode, 5, res.stderr)
        self.assertIn("阶段名不在契约表", res.stderr)

    def test_unwritable(self) -> None:
        state = "/proc/airy-no-such/ui"
        self.assertEqual(self.gate(state=state).returncode, 0)
        self.assertEqual(self.gate(state=state).returncode, 0)

    def test_runtime_dir(self) -> None:
        res = self.run_ui(
            'ui_gate "电源与热治理"',
            RUNTIME_DIRECTORY=str(self.dir / "run"),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.dir / "run" / "6").exists())


class DueCase(Case):
    """只读判定：回答「还该不该出结论」，但不占标记。"""

    def due(
        self,
        name: str = "电源与热治理",
        state: str | None = None,
    ) -> subprocess.CompletedProcess:
        return self.run_ui(
            f'ui_due "{name}"',
            UI_STATE=state or str(self.dir / "ui"),
        )

    def test_read_only(self) -> None:
        self.assertEqual(self.due().returncode, 0)
        self.assertFalse((self.dir / "ui" / "6").exists())
        self.assertEqual(self.due().returncode, 0)

    def test_consumed_by_gate(self) -> None:
        self.assertEqual(self.due().returncode, 0)
        gate = self.run_ui('ui_gate "电源与热治理"', UI_STATE=str(self.dir / "ui"))
        self.assertEqual(gate.returncode, 0, gate.stderr)
        self.assertEqual(self.due().returncode, 1)

    def test_unknown(self) -> None:
        res = self.due("不存在的阶段")
        self.assertEqual(res.returncode, 5, res.stderr)
        self.assertIn("阶段名不在契约表", res.stderr)

    def test_unwritable(self) -> None:
        self.assertEqual(self.due(state="/proc/airy-no-such/ui").returncode, 0)


if __name__ == "__main__":
    sys.exit(unittest.main())
