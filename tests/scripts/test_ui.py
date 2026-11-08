# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 开机九阶段打印契约回归测试
#
# 在临时目录里以桩 logger 驱动 ui.sh，逐字比对四种动词的输出形态、
# 契约表（序号、名称、显示宽度、ASCII 代号、总数）、两类通道（console
# 只出 ASCII，journal 出中文）、契约拒绝，以及只读判定（ui_due）不占标记
# 的语义；宽度在这里独立用 unicodedata 复算，避免表里的宽度与终端实际
# 渲染分叉。
#
# console 侧必须是纯 ASCII 是本轮回归的主判据：285H 实机开屏九行全部是
# 方块，根因是 VT 字体（latarcyrheb-sun16）没有 CJK 字形，而阶段名是中文。

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

    def stage_table(self) -> list[tuple[str, str, str, str]]:
        out = self.run_ui('printf "%s\\n" "${UI_STAGES[@]}"')
        self.assertEqual(out.returncode, 0, out.stderr)
        return [tuple(row.split("|")) for row in out.stdout.splitlines()]

    def row(self, index: int, name: str, token: str, tail: str = "") -> str:
        """一行阶段结论的精确形态：名称补到 UI_COL，带关键值时 token 补到 5 列。"""
        text = f"[ {index}/9 ] {name}{' ' * (16 - width(name))}"
        return text + (f"{token:<5}{tail}" if tail else token)

    def test_stage_table_is_the_contract(self):
        rows = self.stage_table()
        self.assertEqual(len(rows), 9, "阶段总数必须为九")
        self.assertEqual([r[0] for r in rows], [str(n) for n in range(1, 10)])
        names = [r[1] for r in rows]
        self.assertEqual(len(set(names)), len(names), "阶段名必须唯一")
        codes = [r[3] for r in rows]
        self.assertEqual(len(set(codes)), len(codes), "console 代号必须唯一")
        for _, name, columns, code in rows:
            self.assertEqual(int(columns), width(name), f"「{name}」宽度登记不符")
            self.assertTrue(code.isascii(), f"「{code}」必须是 ASCII")
            self.assertFalse(any(c.isspace() for c in code), code)

    def test_begin_line_names_the_stage(self):
        out = self.run_ui("ui_begin 数据分区")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, self.row(3, "data", "...") + "\n")

    def test_ok_line_carries_key_values(self):
        out = self.run_ui("ui_ok 数据分区 mounted=/data dev=/dev/nvme0n1p4")
        self.assertEqual(out.returncode, 0, out.stderr)
        tail = "mounted=/data dev=/dev/nvme0n1p4"
        self.assertEqual(out.stdout, self.row(3, "data", "ok", tail) + "\n")
        self.assertEqual(
            self.journal.read_text(encoding="utf-8"),
            self.row(3, "数据分区", "ok", tail) + "\n",
        )

    def test_note_and_fail_tokens(self):
        out = self.run_ui(
            "ui_note 运行时带宽 runtime=950000 group=yes\n"
            "ui_fail 桌面就绪 reason=session"
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout.splitlines(),
            [
                self.row(4, "bandwidth", "note", "runtime=950000 group=yes"),
                self.row(8, "desktop", "FAIL", "reason=session"),
            ],
        )

    def test_fail_without_criteria_is_not_silent(self):
        out = self.run_ui("ui_fail 桌面就绪")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, self.row(8, "desktop", "FAIL") + "\n")

    def test_console_tail_is_sanitised_and_journal_keeps_it(self):
        """调用方误传中文时屏幕只损失措辞，序号与 token 仍可判读。"""
        out = self.run_ui('ui_fail 桌面就绪 "lightdm 未 active"')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            out.stdout, self.row(8, "desktop", "FAIL", "lightdm ? active") + "\n"
        )
        self.assertIn("lightdm 未 active", self.journal.read_text(encoding="utf-8"))

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
            out.stdout, self.row(5, "swaplimit", "wait", "used=40s limit=60s") + "\n"
        )

    def test_journal_receives_the_stage_name(self):
        out = self.run_ui("ui_ok 自检与取证 probe=clean")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(
            self.journal.read_text(encoding="utf-8"),
            self.row(9, "自检与取证", "ok", "probe=clean") + "\n",
        )
        self.assertNotIn("自检", out.stdout)

    def test_console_gets_the_line_when_stdout_is_a_pipe(self):
        console = self.dir / "console"
        console.write_text("", encoding="utf-8")
        out = self.run_ui("ui_ok 基础挂载 x=1", UI_CONSOLE=str(console),
                          **self.local_only())
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(console.read_text(encoding="utf-8"), out.stdout)

    def test_console_line_is_not_doubled_when_stdout_is_console(self):
        console = self.dir / "console"
        rc = self.run_ui_to_file("ui_ok 桌面就绪 unit=lightdm", console,
                                 **self.local_only())
        self.assertEqual(rc, 0)
        self.assertEqual(
            console.read_text(encoding="utf-8"),
            self.row(8, "desktop", "ok", "unit=lightdm") + "\n",
        )

    def local_only(self) -> dict[str, str]:
        """把通道判定钉在本目录：无串口、活动控制台是本地屏。

        通道表默认读宿主机的 /sys/class/tty/console/active。宿主的命令行末位
        恰好是串口时（CI 的 runner 就是这样），con_init 认定 /dev/console 已
        落在串口上而把本地屏从通道里去掉，于是「重定向出去的落点拿到整行」
        这条判据随宿主硬件变色。它要判的其实只有「stdout 不是终端时补写
        一份」，串口侧由 _chans 的用例分别覆盖，所以这里把两个输入都钉死。
        """
        active = self.dir / "active"
        active.write_text("tty0\n", encoding="utf-8")
        return {
            "AIRY_CON_SERIAL": str(self.dir / "absent-ttyS0"),
            "AIRY_CON_ACTIVE": str(active),
        }

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
        self.assertEqual(
            console.read_text(encoding="utf-8"),
            "airymax-boot: stage-not-in-contract\n",
        )
        self.assertIn("桌面美容", out.stderr)

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

    def test_marker_dir_ignores_runtime_dir(self) -> None:
        """标记目录是全局共享的，不受单元私有运行时目录牵引。

        取 RUNTIME_DIRECTORY 会让六个阶段单元各写一份标记，闸门退化成
        「每单元每开机一次」；且 airy-power 的运行时目录名与其余单元不同，
        同一阶段在两条路径上互不可见。
        """
        run = self.dir / "run"
        res = self.run_ui(
            'ui_gate "电源与热治理"',
            UI_STATE=str(self.dir / "ui"),
            RUNTIME_DIRECTORY=str(run),
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.dir / "ui" / "6").exists())
        self.assertFalse((run / "6").exists())


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


class LocaleCase(Case):
    """折叠按字节：排序 locale 下不得把字母数字换成 '?'。

    run 42426433371 的冷启动串口里，九阶段行整行退化成「[ ?/? ] ???????」：
    sed 的方括号区间在 UTF-8 locale 下走排序规则，'[ -~]' 不再是
    0x20..0x7E，标点之外全被判为非 ASCII。判据只有一条——同一输入在 C 与
    排序 locale 下的输出必须逐字相同。测试机没有对应 locale 时跳过，
    不把「跑不了」伪装成「通过」。
    """

    CANDIDATES = ("en_US.UTF-8", "zh_CN.UTF-8")

    def collation_locale(self) -> str:
        """按能否真正加载判定：locale -a 不列出 LOCPATH 下临时生成的 locale。"""
        for name in self.CANDIDATES:
            env = {**os.environ, "LC_ALL": name}
            res = subprocess.run(["locale", "charmap"], capture_output=True,
                                 text=True, env=env, check=False)
            if res.returncode == 0 and res.stdout.strip() == "UTF-8":
                return name
        return ""

    def test_fold_is_locale_independent(self):
        locale = self.collation_locale()
        if not locale:
            self.skipTest("没有可用的排序 locale")
        body = "ui_ok 桌面就绪 unit=lightdm class=user session=1"
        c = self.run_ui(body, LC_ALL="C", LANG="C")
        u = self.run_ui(body, LC_ALL=locale, LANG=locale)
        self.assertEqual(c.returncode, 0, c.stderr)
        self.assertEqual(u.returncode, 0, u.stderr)
        self.assertEqual(u.stdout, c.stdout)


if __name__ == "__main__":
    sys.exit(unittest.main())
