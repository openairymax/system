# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# spec 单测
#
# 覆盖包名合法化、版本取上游、三种架构映射与拒绝、以及生成配方里的关键行：
# 显式 %files 清单、%{_prefix}、不使用 /* 通配。

"""Tests for the airydeb spec generator."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from airydeb import archive, control, err, spec  # noqa: E402
from deb_fixture import Tree  # noqa: E402

FIELDS = control.parse(
    "Package: demo_pkg\n"
    "Version: 1:2.0~rc1-3\n"
    "Architecture: amd64\n"
    "Description: demo 50% faster\n"
    " A longer line.\n")


class NameTest(unittest.TestCase):
    def test_keeps_underscore(self):
        self.assertEqual(spec.legal("demo_pkg"), "demo_pkg")

    def test_replaces_bad(self):
        self.assertEqual(spec.legal("demo pkg:1"), "demo-pkg-1")

    def test_upstream_strips_epoch_and_revision(self):
        self.assertEqual(spec.upstream("1:2.0~rc1-3"), "2.0~rc1")

    def test_upstream_without_revision(self):
        self.assertEqual(spec.upstream("2.0"), "2.0")

    def test_upstream_empty(self):
        self.assertEqual(spec.upstream(""), "0")


class ArchTest(unittest.TestCase):
    def test_maps_known(self):
        self.assertEqual(spec.arch({"architecture": "amd64"}), "x86_64")
        self.assertEqual(spec.arch({"architecture": "arm64"}), "aarch64")
        self.assertEqual(spec.arch({"architecture": "riscv64"}), "riscv64")

    def test_rejects_unknown(self):
        with self.assertRaises(err.Fault):
            spec.arch({"architecture": "i386"})

    def test_rejects_missing(self):
        with self.assertRaises(err.Fault):
            spec.arch({})


class MetaTest(unittest.TestCase):
    def test_returns_triplet(self):
        self.assertEqual(spec.meta(FIELDS), ("demo_pkg", "2.0~rc1", "x86_64"))

    def test_rejects_missing_name(self):
        with self.assertRaises(err.Fault):
            spec.meta({"version": "1.0", "architecture": "amd64"})


class TextTest(Tree):
    def build(self):
        tree = self.base / "tree"
        archive.unpack(archive.open_deb(self.deb()), tree)
        return spec.text(tree, FIELDS)

    def test_header(self):
        body = self.build()
        self.assertIn("Name:           demo_pkg", body)
        self.assertIn("Version:        2.0~rc1", body)
        self.assertIn("Release:        1%{?dist}.airydeb", body)
        self.assertIn("BuildArch:      x86_64", body)
        self.assertIn("AutoReqProv:    no", body)

    def test_install_copies_tree(self):
        tree = self.base / "tree"
        archive.unpack(archive.open_deb(self.deb()), tree)
        body = spec.text(tree, FIELDS)
        self.assertIn(f"cp -a {tree}/.", body)
        self.assertIn("%{buildroot}", body)

    def test_files_explicit(self):
        body = self.build()
        self.assertIn("%{_prefix}/bin/demo", body)
        self.assertIn("%{_prefix}/share/doc/demo/copyright", body)
        self.assertNotIn("/*", body)

    def test_no_dir_owned(self):
        body = self.build()
        self.assertNotIn("%dir", body)

    def test_dir_entries_skipped(self):
        tree = self.base / "tree"
        archive.unpack(archive.open_deb(self.deb()), tree)
        rows = spec.entries(tree)
        self.assertIn("%{_prefix}/bin/demo", rows)
        self.assertFalse([row for row in rows if row.startswith("%dir")])

    def test_escapes_percent(self):
        body = self.build()
        self.assertIn("demo 50%% faster", body)


class EntriesTest(unittest.TestCase):
    def test_empty_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(spec.entries(Path(tmp)), ())


if __name__ == "__main__":
    unittest.main()
