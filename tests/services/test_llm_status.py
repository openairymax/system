# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行状态持久化测试
#
# 状态文件是缓存而非事实来源，因此「读不动就当作没有」是本层的契约，
# 用例一半以上在破坏磁盘内容：半截 JSON、未知 token、字段多余、状态
# 与原因不自洽。正向用例只保证 encode/store/load 三者闭环。

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services"))

from airyllm import model, status  # noqa: E402

DIGEST = "a" * 64


def ready(digest: str = DIGEST) -> model.Status:
    return model.Status(state=model.State.READY, backend=model.Backend.CPU,
                        digest=digest)


class StoreTest(unittest.TestCase):
    """写入必须原子、可覆写，且不留下临时文件。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "llm"

    def test_roundtrip(self) -> None:
        status.store(self.dir, ready())
        self.assertEqual(status.load(self.dir), ready())

    def test_degraded(self) -> None:
        entry = model.Status(state=model.State.DEGRADED,
                             backend=model.Backend.CPU,
                             reason=model.Reason.NO_ACCEL, digest=DIGEST)
        status.store(self.dir, entry)
        self.assertIs(status.load(self.dir).reason, model.Reason.NO_ACCEL)

    def test_shape(self) -> None:
        path = status.store(self.dir, ready())
        obj = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(obj), status.STATUS_FIELDS)
        self.assertEqual(obj["reason"], "")

    def test_overwrite(self) -> None:
        status.store(self.dir, ready())
        failed = model.Status(state=model.State.FAILED,
                              backend=model.Backend.CPU,
                              reason=model.Reason.NO_MODEL)
        status.store(self.dir, failed)
        self.assertIs(status.load(self.dir).state, model.State.FAILED)

    def test_no_leftover(self) -> None:
        status.store(self.dir, ready())
        leftovers = [p.name for p in self.dir.iterdir()
                     if p.name.startswith(".status-")]
        self.assertEqual(leftovers, [])

    def test_unwritable_is_noop(self) -> None:
        """缓存写不进去只失去复用，不得把 OSError 抛给只读查询。

        普通用户查 status 时运行根属于 root，这是常态而非异常；若此处
        抛出，用户看到的是一次 Python traceback 而不是手上的判定。
        """
        if os.geteuid() == 0:
            self.skipTest("root 不受目录权限约束")
        locked = Path(self.tmp.name) / "locked"
        locked.mkdir(mode=0o500)
        self.addCleanup(locked.chmod, 0o700)
        self.assertIsNone(status.store(locked / "llm", ready()))


class LoadTest(unittest.TestCase):
    """任何不可信内容都必须退化为「没有状态」，而不是抛错或半读。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / model.STATUS_FILE

    def write(self, obj) -> None:
        self.path.write_text(json.dumps(obj), encoding="utf-8")

    def valid(self) -> dict:
        return status.encode(ready())

    def test_missing(self) -> None:
        self.assertIsNone(status.load(self.dir))

    def test_not_json(self) -> None:
        self.path.write_text("{", encoding="utf-8")
        self.assertIsNone(status.load(self.dir))

    def test_not_object(self) -> None:
        self.path.write_text("[]", encoding="utf-8")
        self.assertIsNone(status.load(self.dir))

    def test_schema(self) -> None:
        obj = self.valid()
        obj["schema"] = model.SCHEMA_VERSION + 1
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_extra_field(self) -> None:
        obj = self.valid()
        obj["note"] = "stale"
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_missing_field(self) -> None:
        obj = self.valid()
        del obj["digest"]
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_unknown_state(self) -> None:
        obj = self.valid()
        obj["state"] = "running"
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_unknown_backend(self) -> None:
        obj = self.valid()
        obj["backend"] = "tpu"
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_unknown_reason(self) -> None:
        obj = self.valid()
        obj.update(state="degraded", reason="busy")
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_ready_with_reason(self) -> None:
        obj = self.valid()
        obj["reason"] = model.Reason.NO_ACCEL.value
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_degraded_without_reason(self) -> None:
        obj = self.valid()
        obj["state"] = "degraded"
        self.write(obj)
        self.assertIsNone(status.load(self.dir))

    def test_digest_not_text(self) -> None:
        obj = self.valid()
        obj["digest"] = 7
        self.write(obj)
        self.assertIsNone(status.load(self.dir))


if __name__ == "__main__":
    unittest.main()
