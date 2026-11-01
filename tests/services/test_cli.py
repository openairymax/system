# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘命令行回归测试
#
# 探测结果以固定拓扑注入，命令执行只用 /bin/true 与 /bin/false，
# 不触任何块设备。覆盖四命令的退出码与 JSON 形态、--snapshot 重放，
# 以及二次确认边界：确认名与计划盘名不一致、目标承载运行系统、非
# root 都必须在执行前被挡住。

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import cli, model, ops  # noqa: E402


def part(name: str, mount: str | None = None,
         fs: model.FsKind = model.FsKind.EXT4,
         role: model.Role = model.Role.FOREIGN) -> model.Partition:
    return model.Partition(name=name, size=1024, fs=fs, mount=mount,
                           role=role)


def sample() -> model.Topology:
    host = model.Disk(
        name="nvme0n1", size=1024**3, bus=model.Bus.NVME,
        table=model.TableKind.GPT,
        parts=(part("nvme0n1p2", "/", model.FsKind.BTRFS,
                    model.Role.SYSTEM),),
    )
    spare = model.Disk(
        name="sdb", size=2 * 1024**3, bus=model.Bus.SATA,
        table=model.TableKind.GPT,
    )
    data = model.Disk(
        name="sdc", size=2 * 1024**3, bus=model.Bus.SATA,
        table=model.TableKind.GPT,
        parts=(part("sdc1", model.DATA_MOUNT, model.FsKind.XFS,
                    model.Role.DATA),),
    )
    return model.Topology(kind=model.TopoKind.MULTI,
                          disks=(host, spare, data))


def plan(disk: str = "sdb") -> ops.Plan:
    item = ops.Step(action=ops.Action.WIPE, target=f"/dev/{disk}",
                    argv=("/bin/true",))
    return ops.Plan(disk=disk, steps=(item,))


def replay() -> dict[str, object]:
    """一份与 sample() 不同的快照：单盘 virtio，/ 与 /data 同盘。"""
    disk = model.Disk(
        name="vda", size=1024**3, bus=model.Bus.VIRTIO,
        table=model.TableKind.GPT,
        parts=(part("vda1", "/", model.FsKind.BTRFS, model.Role.SYSTEM),
               part("vda2", model.DATA_MOUNT, model.FsKind.XFS,
                    model.Role.DATA)),
    )
    return model.dump(model.Topology(kind=model.TopoKind.SINGLE,
                                     disks=(disk,)))


def save(obj: dict[str, object]) -> str:
    """把一份 status 快照落到临时文件，返回路径。"""
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8")
    with handle:
        json.dump(obj, handle)
    return handle.name


class Case(unittest.TestCase):
    """统一注入拓扑、捕获输出，并让 root 判定可控。"""

    def call(self, *argv: str, topo: model.Topology | None = None,
             root: bool = True,
             record: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("airydisk.probe.probe",
                                           return_value=topo or sample()))
            stack.enter_context(mock.patch("airydisk.cli.os.geteuid",
                                           return_value=0 if root else 1000))
            path = record or (Path(stack.enter_context(
                tempfile.TemporaryDirectory())) / "install-disk.json")
            stack.enter_context(mock.patch("airydisk.record.PATH",
                                           str(path)))
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class StatusTest(Case):
    """拓扑输出：人类可读与 JSON 同源。"""

    def test_human(self) -> None:
        code, out, _ = self.call("status")
        self.assertEqual(code, 0)
        self.assertIn("nvme0n1", out)
        self.assertIn("/data", out)

    def test_json(self) -> None:
        code, out, _ = self.call("--json", "status")
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertEqual(obj["kind"], "multi")
        self.assertEqual(len(obj["disks"]), 3)

    def test_install_absent(self) -> None:
        """没有选盘记录时说 absent，不把缺失当成不一致。"""
        code, out, _ = self.call("--json", "status")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["install"]["verdict"], "absent")

    def test_install_match(self) -> None:
        """记录与现场一致时 verdict 为 match，且不列 diff。"""
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "install-disk.json"
            rec.write_text(json.dumps({
                "schema": 1, "disk": "nvme0n1", "bytes": 1024**3,
                "bus": "nvme", "firmware": "uefi", "layout": "single",
                "medium": "sda",
            }), encoding="utf-8")
            code, out, _ = self.call("--json", "status", record=rec)
        self.assertEqual(code, 0)
        install = json.loads(out)["install"]
        self.assertEqual(install["verdict"], "match")
        self.assertEqual(install["diff"], [])
        self.assertEqual(install["layout"], "single")

    def test_install_mismatch(self) -> None:
        """容量与记录不符时列出差异字段。"""
        with tempfile.TemporaryDirectory() as tmp:
            rec = Path(tmp) / "install-disk.json"
            rec.write_text(json.dumps({
                "schema": 1, "disk": "nvme0n1", "bytes": 2 * 1024**3,
                "bus": "nvme",
            }), encoding="utf-8")
            code, out, _ = self.call("status", record=rec)
        self.assertEqual(code, 0)
        self.assertIn("mismatch", out)
        self.assertIn("diff=size", out)


class LayoutTest(Case):
    """布局判定：通过返回 0，缺数据分区返回 3 并给出稳定 token。"""

    def test_json(self) -> None:
        code, out, _ = self.call("--json", "layout")
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertEqual(obj["kind"], "dual")
        self.assertEqual(obj["verdict"], "ok")
        self.assertEqual(obj["root"], "nvme0n1")
        self.assertEqual(obj["data"], "sdc")

    def test_human(self) -> None:
        code, out, _ = self.call("layout")
        self.assertEqual(code, 0)
        self.assertIn("ok", out)
        self.assertIn("root=nvme0n1", out)
        self.assertIn("data=sdc", out)

    def test_no_data(self) -> None:
        bare = model.Topology(
            kind=model.TopoKind.SINGLE,
            disks=(model.Disk(name="nvme0n1", size=1024**3,
                              bus=model.Bus.NVME,
                              table=model.TableKind.GPT,
                              parts=(part("nvme0n1p1", "/"),)),),
        )
        code, out, _ = self.call("--json", "layout", topo=bare)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["verdict"], "no-data")


class SnapshotTest(Case):
    """--snapshot：重放已存证据；结果只取决于快照，不取决于本机。"""

    def snap(self, obj: dict[str, object] | None = None) -> str:
        path = save(replay() if obj is None else obj)
        self.addCleanup(Path(path).unlink)
        return path

    def test_layout_replay(self) -> None:
        code, out, _ = self.call("--json", "layout",
                                 "--snapshot", self.snap())
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertEqual(obj["kind"], "single")
        self.assertEqual(obj["root"], "vda")
        self.assertEqual(obj["verdict"], "ok")

    def test_status_replay(self) -> None:
        code, out, _ = self.call("--json", "status",
                                 "--snapshot", self.snap())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["disks"][0]["name"], "vda")

    def test_stdin(self) -> None:
        with mock.patch("sys.stdin", io.StringIO(json.dumps(replay()))):
            code, out, _ = self.call("--json", "layout", "--snapshot", "-")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["data"], "vda")

    def test_refused(self) -> None:
        obj = replay()
        obj["disks"][0]["parts"] = obj["disks"][0]["parts"][:1]
        code, out, _ = self.call("--json", "layout",
                                 "--snapshot", self.snap(obj))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["verdict"], "no-data")

    def test_bad_schema(self) -> None:
        code, _, err = self.call("layout", "--snapshot",
                                 self.snap({"schema": 99}))
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("快照不可用", err)

    def test_missing_file(self) -> None:
        path = str(Path(tempfile.mkdtemp()) / "absent.json")
        code, _, err = self.call("layout", "--snapshot", path)
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("快照不可用", err)


class CheckTest(Case):
    """护栏判定：放行返回 0，拒绝返回 3 并给出稳定 token。"""

    def test_ok(self) -> None:
        code, out, _ = self.call("check", "--disk", "sdb")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "ok")

    def test_hosts_system(self) -> None:
        code, out, _ = self.call("check", "--disk", "nvme0n1")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(out.strip(),
                         ops.REFUSAL_TEXT[ops.Refusal.HOSTS_SYSTEM])

    def test_json(self) -> None:
        code, out, _ = self.call("--json", "check", "--disk", "nvme0n1")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["refusal"], "hosts-system")

    def test_busy(self) -> None:
        code, out, _ = self.call("--json", "check", "--disk", "sdc")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["refusal"], "busy")

    def test_missing(self) -> None:
        code, _, err = self.call("check", "--disk", "sdz")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("sdz", err)


class ApplyTest(Case):
    """执行前必须过三道闸：二次确认、护栏、root。"""

    def write(self, value: ops.Plan) -> str:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        with handle:
            json.dump(ops.dump(value), handle)
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_confirm_mismatch(self) -> None:
        path = self.write(plan())
        code, _, err = self.call("apply", "--plan", path,
                                 "--confirm", "sdc")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("不符", err)

    def test_refused(self) -> None:
        path = self.write(plan("nvme0n1"))
        code, _, err = self.call("apply", "--plan", path,
                                 "--confirm", "nvme0n1")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("承载正在运行的系统", err)

    def test_not_root(self) -> None:
        path = self.write(plan())
        code, _, err = self.call("apply", "--plan", path,
                                 "--confirm", "sdb", root=False)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("root", err)

    def test_success(self) -> None:
        path = self.write(plan())
        code, out, _ = self.call("apply", "--plan", path,
                                 "--confirm", "sdb")
        self.assertEqual(code, 0)
        self.assertIn("完成", out)

    def test_failure(self) -> None:
        first = ops.Step(action=ops.Action.WIPE, target="/dev/sdb",
                         argv=("/bin/true",))
        second = ops.Step(action=ops.Action.PARTITION, target="/dev/sdb",
                          argv=("/bin/false",))
        path = self.write(ops.Plan(disk="sdb", steps=(first, second)))
        code, out, _ = self.call("--json", "apply", "--plan", path,
                                 "--confirm", "sdb")
        self.assertEqual(code, cli.EXIT_FAIL)
        obj = json.loads(out)
        self.assertFalse(obj["ok"])
        self.assertTrue(obj["unclean"])
        self.assertEqual(obj["failed"]["rc"], 1)
        self.assertEqual(len(obj["done"]), 1)

    def test_rollback(self) -> None:
        first = ops.Step(action=ops.Action.WIPE, target="/dev/sdb",
                         argv=("/bin/true",), undo=("/bin/true",))
        second = ops.Step(action=ops.Action.PARTITION, target="/dev/sdb",
                          argv=("/bin/false",))
        path = self.write(ops.Plan(disk="sdb", steps=(first, second)))
        code, out, _ = self.call("--json", "apply", "--plan", path,
                                 "--confirm", "sdb")
        self.assertEqual(code, cli.EXIT_FAIL)
        obj = json.loads(out)
        self.assertFalse(obj["unclean"])
        self.assertEqual(len(obj["undone"]), 1)

    def test_dry_run_skips_exec(self) -> None:
        mark = Path(tempfile.mkdtemp()) / "touched"
        step = ops.Step(action=ops.Action.WIPE, target="/dev/sdb",
                        argv=("/usr/bin/touch", str(mark)))
        path = self.write(ops.Plan(disk="sdb", steps=(step,)))
        code, out, _ = self.call("apply", "--plan", path,
                                 "--confirm", "sdb", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("dry-run", out)
        self.assertFalse(mark.exists())

    def test_dry_run_json(self) -> None:
        path = self.write(plan())
        code, out, _ = self.call("--json", "apply", "--plan", path,
                                 "--confirm", "sdb", "--dry-run")
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertTrue(obj["dry_run"])
        self.assertEqual(obj["plan"]["disk"], "sdb")
        self.assertEqual(len(obj["plan"]["steps"]), 1)

    def test_dry_run_refused(self) -> None:
        path = self.write(plan("nvme0n1"))
        code, _, err = self.call("apply", "--plan", path,
                                 "--confirm", "nvme0n1", "--dry-run")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("承载正在运行的系统", err)

    def test_bad_plan(self) -> None:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8")
        with handle:
            handle.write("{\"schema\": 99}")
        self.addCleanup(Path(handle.name).unlink)
        code, _, err = self.call("apply", "--plan", handle.name,
                                 "--confirm", "sdb")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("计划不可用", err)


if __name__ == "__main__":
    unittest.main()
