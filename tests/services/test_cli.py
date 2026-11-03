# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 磁盘命令行回归测试
#
# 探测结果以固定拓扑注入，命令执行只用 /bin/true 与 /bin/false，
# 不触任何块设备。覆盖八个子命令的退出码与 JSON 形态、--snapshot 重放，
# persist 不依赖拓扑，以及二次确认边界：确认名与计划盘名不一致、目标承载
# 运行系统、非 root 都必须在执行前被挡住。mount 与 health 的判定依赖本机
# 挂载事实与 statvfs，测试注入替身，不读运行测试的机器。

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from os import ST_RDONLY
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airydisk import cli, model, ops, record, runner  # noqa: E402


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


def vfs(used: int, avail: int, flag: int = 0) -> SimpleNamespace:
    """statvfs_result 的最小替身，只含健康判定读取的字段。"""
    return SimpleNamespace(
        f_blocks=used + avail,
        f_bfree=avail,
        f_bavail=avail,
        f_frsize=1,
        f_flag=flag,
    )


def ok_run(step: ops.Step, timeout: float = 0) -> runner.Result:
    """假执行器：不触设备，直接回成功。"""
    return runner.Result(step=step, rc=0)


def fail_run(step: ops.Step, timeout: float = 0) -> runner.Result:
    """假执行器：回失败，用于验证退出码与建议。"""
    return runner.Result(step=step, rc=1, err="boom")


def not_last() -> model.Topology:
    """数据分区之后还有分区：原地扩容会搬动系统分区。"""
    disk = model.Disk(
        name="sdc", size=2 * 1024**3, bus=model.Bus.SATA,
        table=model.TableKind.GPT,
        parts=(part("sdc1", model.DATA_MOUNT, model.FsKind.XFS,
                    model.Role.DATA),
               part("sdc2")),
    )
    return model.Topology(kind=model.TopoKind.SINGLE, disks=(disk,))


class Case(unittest.TestCase):
    """统一注入拓扑、捕获输出，并让 root 判定可控。"""

    def fstab_file(self, text: str) -> str:
        """落一份 fstab 到临时文件，返回路径。"""
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".fstab", delete=False, encoding="utf-8")
        with handle:
            handle.write(text)
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def call(self, *argv: str, topo: model.Topology | None = None,
             root: bool = True,
             record: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch("airydisk.probe.probe",
                                           return_value=topo or sample()))
            stack.enter_context(
                mock.patch("airydisk.cli.os.geteuid",
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


class FstabTest(Case):
    """--fstab：布局成立还要看重启后挂得上，二者任一不成立即拒绝。"""

    def test_ok(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs defaults 0 2\n")
        code, out, _ = self.call("--json", "layout", "--fstab", path)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["persist"], "ok")

    def test_no_data(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n")
        code, out, _ = self.call("--json", "layout", "--fstab", path)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["persist"], "no-data")

    def test_unstable(self) -> None:
        path = self.fstab_file("/dev/sda2 / ext4 defaults 0 1\n"
                               "UUID=b /data xfs defaults 0 2\n")
        code, out, _ = self.call("--json", "layout", "--fstab", path)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["persist"], "unstable")

    def test_human(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs defaults 0 2\n")
        code, out, _ = self.call("layout", "--fstab", path)
        self.assertEqual(code, 0)
        self.assertIn("persist=ok", out)

    def test_missing(self) -> None:
        path = str(Path(tempfile.mkdtemp()) / "absent.fstab")
        code, _, err = self.call("layout", "--fstab", path)
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("fstab 不可用", err)

    def test_layout_refused(self) -> None:
        """fstab 合规但拓扑缺 /data 时仍拒绝，二者都要成立。"""
        bare = model.Topology(
            kind=model.TopoKind.SINGLE,
            disks=(model.Disk(name="nvme0n1", size=1024**3,
                              bus=model.Bus.NVME,
                              table=model.TableKind.GPT,
                              parts=(part("nvme0n1p1", "/"),)),),
        )
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs defaults 0 2\n")
        code, out, _ = self.call("--json", "layout", "--fstab", path,
                                 topo=bare)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["verdict"], "no-data")


class PersistTest(Case):
    """persist：只判 fstab，装机 chroot 内不做拓扑判定。"""

    def test_ok(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs defaults 0 2\n")
        code, out, _ = self.call("persist", "--file", path)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "persist=ok")

    def test_json(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n")
        code, out, _ = self.call("--json", "persist", "--file", path)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["persist"], "no-data")

    def test_noauto(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs noauto 0 2\n")
        code, out, _ = self.call("persist", "--file", path)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("noauto", out)

    def test_missing(self) -> None:
        path = str(Path(tempfile.mkdtemp()) / "absent.fstab")
        code, _, err = self.call("persist", "--file", path)
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("fstab 不可用", err)

    def test_no_probe(self) -> None:
        """判定只看 fstab 文本：装机 chroot 里没有可信拓扑。"""
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs defaults 0 2\n")
        out = io.StringIO()
        with mock.patch("airydisk.probe.probe",
                        side_effect=AssertionError("probe")), \
                contextlib.redirect_stdout(out):
            code = cli.main(["persist", "--file", path])
        self.assertEqual(code, 0)
        self.assertIn("ok", out.getvalue())


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


class MountTest(Case):
    """mount：已挂载不动，待挂载执行一次，拒绝场景不执行。"""

    def fstab(self) -> str:
        return self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs defaults 0 2\n")

    def test_mounted(self) -> None:
        with mock.patch("airydisk.mount.ismount", return_value=True):
            code, out, _ = self.call("mount", "--file", self.fstab())
        self.assertEqual(code, 0)
        self.assertIn("mounted", out)

    def test_dry_run_ready(self) -> None:
        with mock.patch("airydisk.mount.ismount", return_value=False):
            code, out, _ = self.call("mount", "--file", self.fstab(),
                                     "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("ready", out)
        self.assertIn("/usr/bin/mount /data", out)

    def test_dry_run_json(self) -> None:
        with mock.patch("airydisk.mount.ismount", return_value=False):
            code, out, _ = self.call("--json", "mount", "--file",
                                     self.fstab(), "--dry-run")
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertTrue(obj["dry_run"])
        self.assertEqual(obj["verdict"], "ready")

    def test_restored(self) -> None:
        with mock.patch("airydisk.mount.ismount", return_value=False), \
                mock.patch("airydisk.runner.run", side_effect=ok_run):
            code, out, _ = self.call("mount", "--file", self.fstab())
        self.assertEqual(code, 0)
        self.assertIn("restored", out)

    def test_failed(self) -> None:
        with mock.patch("airydisk.mount.ismount", return_value=False), \
                mock.patch("airydisk.runner.run", side_effect=fail_run):
            code, out, _ = self.call("mount", "--file", self.fstab())
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("failed", out)
        self.assertIn("退出码 1", out)

    def test_not_root(self) -> None:
        with mock.patch("airydisk.mount.ismount", return_value=False):
            code, _, err = self.call("mount", "--file", self.fstab(),
                                     root=False)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("root", err)

    def test_no_entry(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n")
        known = record.Install(state="ok", disk="nvme0n1")
        with mock.patch("airydisk.mount.ismount", return_value=False), \
                mock.patch("airydisk.cli.record.load", return_value=known):
            code, out, _ = self.call("mount", "--file", path)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("no-entry", out)

    def test_unset(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n")
        absent = record.Install(state="absent")
        with mock.patch("airydisk.mount.ismount", return_value=False), \
                mock.patch("airydisk.cli.record.load", return_value=absent):
            code, out, _ = self.call("mount", "--file", path)
        self.assertEqual(code, cli.EXIT_NA)
        self.assertIn("unset", out)

    def test_unstable(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "/dev/sda2 /data xfs defaults 0 2\n")
        with mock.patch("airydisk.mount.ismount", return_value=False):
            code, out, _ = self.call("mount", "--file", path)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("unstable", out)

    def test_noauto(self) -> None:
        path = self.fstab_file("UUID=a / ext4 defaults 0 1\n"
                               "UUID=b /data xfs noauto 0 2\n")
        with mock.patch("airydisk.mount.ismount", return_value=False):
            code, out, _ = self.call("mount", "--file", path)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("noauto", out)

    def test_missing(self) -> None:
        path = str(Path(tempfile.mkdtemp()) / "absent.fstab")
        code, _, err = self.call("mount", "--file", path)
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("fstab 不可用", err)


class GrowTest(Case):
    """grow：dry-run 出计划，执行要二次确认与 root。"""

    def test_dry_run(self) -> None:
        code, out, _ = self.call("grow", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("growpart", out)
        self.assertIn("/data", out)

    def test_json(self) -> None:
        code, out, _ = self.call("--json", "grow", "--dry-run")
        self.assertEqual(code, 0)
        obj = json.loads(out)
        self.assertTrue(obj["dry_run"])
        self.assertEqual(obj["verdict"], "ok")
        self.assertEqual(obj["part"], "sdc1")
        self.assertEqual(obj["plan"]["disk"], "sdc")
        self.assertEqual(len(obj["plan"]["steps"]), 2)

    def test_no_data(self) -> None:
        bare = model.Topology(
            kind=model.TopoKind.SINGLE,
            disks=(model.Disk(name="vda", size=1024**3,
                              bus=model.Bus.VIRTIO,
                              table=model.TableKind.GPT,
                              parts=(part("vda1", "/"),)),),
        )
        code, out, _ = self.call("--json", "grow", topo=bare)
        self.assertEqual(code, cli.EXIT_REFUSED)
        obj = json.loads(out)
        self.assertEqual(obj["verdict"], "no-data")
        self.assertTrue(obj["advice"])
        self.assertIsNone(obj["plan"])

    def test_not_last(self) -> None:
        code, out, _ = self.call("--json", "grow", topo=not_last())
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(json.loads(out)["verdict"], "not-last")

    def test_confirm_mismatch(self) -> None:
        code, _, err = self.call("grow", "--confirm", "sdb")
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("不符", err)

    def test_not_root(self) -> None:
        code, _, err = self.call("grow", "--confirm", "sdc", root=False)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("root", err)

    def test_execute(self) -> None:
        def apply(item: ops.Plan) -> runner.Outcome:
            return runner.Outcome(plan=item, done=item.steps)
        with mock.patch("airydisk.runner.apply", side_effect=apply):
            code, out, _ = self.call("grow", "--confirm", "sdc")
        self.assertEqual(code, 0)
        self.assertIn("完成", out)

    def test_failure(self) -> None:
        def apply(item: ops.Plan) -> runner.Outcome:
            failed = runner.Result(step=item.steps[0], rc=1, err="boom")
            return runner.Outcome(plan=item, failed=failed)
        with mock.patch("airydisk.runner.apply", side_effect=apply):
            code, out, err = self.call("grow", "--confirm", "sdc")
        self.assertEqual(code, cli.EXIT_FAIL)
        self.assertIn("未完成", out)
        self.assertIn("退出码 1", err)


class HealthTest(Case):
    """health：容量取本机 statvfs，拓扑只给分区命名。"""

    def test_ok(self) -> None:
        with mock.patch("airydisk.health.ismount", return_value=True), \
                mock.patch("airydisk.health.statvfs",
                           return_value=vfs(50, 50)):
            code, out, _ = self.call("health")
        self.assertEqual(code, 0)
        self.assertIn("ok", out)

    def test_low_space(self) -> None:
        with mock.patch("airydisk.health.ismount", return_value=True), \
                mock.patch("airydisk.health.statvfs",
                           return_value=vfs(95, 5)):
            code, out, _ = self.call("--json", "health")
        self.assertEqual(code, cli.EXIT_REFUSED)
        obj = json.loads(out)
        self.assertEqual(obj["verdict"], "low-space")
        self.assertEqual(obj["used"], 95)

    def test_read_only(self) -> None:
        with mock.patch("airydisk.health.ismount", return_value=True), \
                mock.patch("airydisk.health.statvfs",
                           return_value=vfs(10, 90, ST_RDONLY)):
            code, out, _ = self.call("health")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("read-only", out)

    def test_unavailable(self) -> None:
        with mock.patch("airydisk.health.ismount", return_value=False):
            code, out, _ = self.call("health")
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("unavailable", out)

    def test_no_data(self) -> None:
        bare = model.Topology(
            kind=model.TopoKind.SINGLE,
            disks=(model.Disk(name="vda", size=1024**3,
                              bus=model.Bus.VIRTIO,
                              table=model.TableKind.GPT,
                              parts=(part("vda1", "/"),)),),
        )
        with mock.patch("airydisk.health.ismount",
                        side_effect=AssertionError("ismount")), \
                mock.patch("airydisk.health.statvfs",
                           side_effect=AssertionError("statvfs")):
            code, out, _ = self.call("health", topo=bare)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("no-data", out)


if __name__ == "__main__":
    unittest.main()
