# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# airy-locale 命令行测试
#
# 四条子命令都要有成功与失败两条路径：list/show 只读；gen 的跳过与生成
# 分别验证；check 的退出码要能区分「矩阵坏」「配置错」「文件缺」。
# 生成路径以 mock 代替 localedef，命令组装仍按真实 argv 断言。

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airylocale import cli  # noqa: E402


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conf = self.root / "locale.conf"
        self.conf.write_text("LANG=zh_CN.UTF-8\nLC_TIME=en_US.UTF-8\n",
                             encoding="utf-8")

    def locale_dir(self, *names: str) -> None:
        base = self.root / "usr/lib/locale"
        for name in names:
            (base / name).mkdir(parents=True, exist_ok=True)


class ListTest(Base):
    def test_json_lists_all(self) -> None:
        code, out, _ = run("list", "--json", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_OK)
        data = json.loads(out)
        self.assertEqual(data["default"], "en_US.UTF-8")
        self.assertEqual(len(data["locales"]), 8)
        self.assertFalse(any(item["generated"] for item in data["locales"]))

    def test_generated_marks_directory(self) -> None:
        self.locale_dir("en_US.UTF-8")
        code, out, _ = run("list", "--json", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_OK)
        marks = {item["posix"]: item["generated"]
                 for item in json.loads(out)["locales"]}
        self.assertTrue(marks["en_US.UTF-8"])
        self.assertFalse(marks["zh_CN.UTF-8"])

    def test_table_has_header(self) -> None:
        code, out, _ = run("list", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("PRIO", out.splitlines()[0])
        self.assertIn("zh_CN.UTF-8", out)


class ShowTest(Base):
    def test_show_json(self) -> None:
        code, out, _ = run("show", "--conf", str(self.conf), "--json")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(json.loads(out)["LANG"], "zh_CN.UTF-8")

    def test_missing_conf(self) -> None:
        code, _, err = run("show", "--conf", str(self.root / "absent"))
        self.assertEqual(code, cli.EXIT_ENV)
        self.assertIn("没有 locale 配置", err)


class CheckTest(Base):
    def test_matrix_only_ok(self) -> None:
        code, out, _ = run("check", "--matrix-only")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("AIRYMAX-LOCALE-CHECK ok", out)

    def test_conf_ok(self) -> None:
        code, _, _ = run("check", "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_OK)

    def test_conf_outside_matrix(self) -> None:
        self.conf.write_text("LANG=xx_YY.UTF-8\n", encoding="utf-8")
        code, _, err = run("check", "--conf", str(self.conf))
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("不在矩阵内", err)

    def test_missing_explicit_conf(self) -> None:
        code, _, err = run("check", "--conf", str(self.root / "absent"))
        self.assertEqual(code, cli.EXIT_ENV)
        self.assertIn("没有 locale 配置", err)

    def test_require_generated(self) -> None:
        code, _, err = run("check", "--conf", str(self.conf), "--root",
                           str(self.root), "--require-generated")
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("未生成", err)
        self.locale_dir("en_US.UTF-8", "zh_CN.UTF-8")
        code, _, _ = run("check", "--conf", str(self.conf), "--root",
                         str(self.root), "--require-generated")
        self.assertEqual(code, cli.EXIT_OK)

    def test_bad_matrix(self) -> None:
        bad = self.root / "bad.tsv"
        bad.write_text("en_US.UTF-8\ten\tUS\tUTF-8\n", encoding="utf-8")
        code, _, err = run("check", "--matrix", str(bad), "--matrix-only")
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("5 列", err)

    def test_missing_matrix(self) -> None:
        code, _, err = run("check", "--matrix", str(self.root / "absent.tsv"),
                           "--matrix-only")
        self.assertEqual(code, cli.EXIT_ENV)
        self.assertIn("读取矩阵失败", err)


class GenTest(Base):
    def test_dry_run_p0(self) -> None:
        code, out, _ = run("gen", "--dry-run", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("localedef -i en_US -f UTF-8", out)
        self.assertIn("localedef -i zh_CN -f UTF-8", out)
        self.assertIn("ok=2 skip=0 fail=0", out)

    def test_skip_generated(self) -> None:
        self.locale_dir("en_US.UTF-8")
        code, out, _ = run("gen", "--dry-run", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("skip en_US.UTF-8", out)
        self.assertIn("ok=1 skip=1 fail=0", out)

    def test_unknown_name(self) -> None:
        code, _, err = run("gen", "--dry-run", "xx_YY.UTF-8")
        self.assertEqual(code, cli.EXIT_POLICY)
        self.assertIn("矩阵内没有", err)

    def test_all_names(self) -> None:
        code, out, _ = run("gen", "--dry-run", "--all", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("ok=8 skip=0 fail=0", out)

    def test_run_success(self) -> None:
        done = mock.Mock(returncode=0)
        with mock.patch.object(cli.shutil, "which", return_value="/usr/bin/localedef"):
            with mock.patch.object(cli.subprocess, "run", return_value=done) as call:
                code, out, _ = run("gen", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("ok=2 skip=0 fail=0", out)
        first = call.call_args_list[0].args[0]
        self.assertEqual(first[:4], ["/usr/bin/localedef", "-i", "en_US", "-f"])

    def test_run_failure(self) -> None:
        done = mock.Mock(returncode=1)
        with mock.patch.object(cli.shutil, "which", return_value="/usr/bin/localedef"):
            with mock.patch.object(cli.subprocess, "run", return_value=done):
                code, out, _ = run("gen", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_RUN)
        self.assertIn("fail=2", out)

    def test_missing_localedef(self) -> None:
        with mock.patch.object(cli.shutil, "which", return_value=None):
            code, _, err = run("gen", "--root", str(self.root))
        self.assertEqual(code, cli.EXIT_ENV)
        self.assertIn("缺少 localedef", err)


if __name__ == "__main__":
    unittest.main()
