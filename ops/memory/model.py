# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
#
# 记忆运维的领域模型
#
# 职责：定义 L3/L4 层级、单条记忆项、磁盘数据块编解码（魔数+版本+记录表）、
# 擦除方法、配置默认值之外的参数校验，以及可预期失败到退出码的映射。
#
# 边界：只做纯内存的表示与校验，不碰文件系统（store.py 负责）、不读配置
# 文件（config.py 负责）、不产生输出（cli.py 负责）。数据块是 mem_d 交付的
# 落盘格式，这里只描述字节布局，不承担加密与压缩——那属于数据面。
#
# 依赖：airylib.err（need、退出码常量）与标准库 struct/re/datetime。

"""AirymaxOS memory layout, records and validation."""

from __future__ import annotations

import datetime
import re
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, List, Optional, Tuple

from airylib.err import EXIT_ENV, EXIT_USAGE, need

__all__ = [
    "BadConfig",
    "BadInput",
    "BadMemory",
    "BadPerm",
    "BadState",
    "BadStore",
    "BadUnsupported",
    "BLOCK_HEAD",
    "BLOCK_MAGIC",
    "BLOCK_VERSION",
    "EXIT_STATE",
    "Item",
    "Level",
    "Method",
    "NS",
    "Record",
    "SCHEMA",
    "block_name",
    "check_block",
    "check_id",
    "check_item",
    "check_window",
    "decode_block",
    "encode_block",
    "encode_rec",
    "in_window",
    "iso",
    "now_ns",
    "parse_iso",
    "parse_items",
    "parse_level",
    "parse_method",
    "parse_window",
]

NS = 1_000_000_000
SCHEMA = 1
EXIT_STATE = 3
BLOCK_MAGIC = b"AIRYBLK\x00"
BLOCK_VERSION = 1
BLOCK_HEAD = len(BLOCK_MAGIC) + 6
REC_HEAD = 2 + 21

_ID = re.compile(r"^[0-9]{1,10}$")
_ITEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_BLOCK = re.compile(r"^[0-9]{6}\.airyblk$")
_WINDOW = re.compile(
    r"^([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])$"
)


class BadMemory(Exception):
    """记忆运维可预期失败的共同基类；子类决定进程退出码。"""


class BadInput(BadMemory, ValueError):
    """调用参数不合法（缺项、格式错、互相矛盾）。"""

    code = EXIT_USAGE


class BadState(BadMemory, RuntimeError):
    """磁盘状态或存储策略不允许执行该操作，退出码 3。"""

    code = EXIT_STATE


class BadStore(BadState):
    """仓库布局损坏、数据块不完整或索引不可信。"""


class BadConfig(BadState):
    """配置文件取值越界或不合法。"""


class BadUnsupported(BadState):
    """本版数据面尚未提供的能力，必须显式拒绝而不是假装成功。"""


class BadPerm(BadMemory, PermissionError):
    """操作者权限不足（跨属主缺少同意、文件不可写）。"""

    code = EXIT_ENV


class Level(str, Enum):
    """设计 §1.2 的四层模型里由运维面直接管理的两层。"""

    L3 = "L3"
    L4 = "L4"


class Method(str, Enum):
    """设计 §8.2 的四种擦除级别；crypto 在数据面缺位时显式拒绝。"""

    QUICK = "quick"
    OVERWRITE = "overwrite"
    SECURE = "secure"
    CRYPTO = "crypto"


@dataclass
class Item:
    """一条记忆项：标识、负载、敏感标记与两个时间戳（纳秒）。"""

    id: str
    data: bytes
    sensitive: bool = False
    created_at: int = field(default_factory=lambda: time.time_ns())
    accessed_at: int = field(default_factory=lambda: time.time_ns())

    def __post_init__(self) -> None:
        check_item(self.id)
        need(isinstance(self.data, (bytes, bytearray)),
             f"记忆项负载必须是 bytes: {self.id}", BadInput)
        if not isinstance(self.data, bytes):
            self.data = bytes(self.data)
        for label in ("created_at", "accessed_at"):
            stamp = getattr(self, label)
            need(isinstance(stamp, int) and stamp >= 0,
                 f"{label} 必须是非负整数: {stamp!r}", BadInput)
        self.sensitive = bool(self.sensitive)

    def text(self) -> str:
        """按 UTF-8 尽力解码，供 search 做子串匹配。"""
        return self.data.decode("utf-8", "replace")

    def to_json(self, level: Optional[str] = None) -> dict:
        """命令行 --json 的最小事实集；不包含任何运行时计数器。"""
        body = {
            "id": self.id,
            "bytes": len(self.data),
            "sensitive": self.sensitive,
            "created_at": iso(self.created_at),
            "accessed_at": iso(self.accessed_at),
        }
        if level:
            body["level"] = level
        return body


@dataclass(frozen=True)
class Record:
    """数据块内的一条记录；deleted 为真表示已删除但尚未被 defrag 回收。"""

    item: Item
    deleted: bool = False

    def encoded(self) -> bytes:
        return encode_rec(self)

    def size(self) -> int:
        return len(encode_rec(self))


def now_ns() -> int:
    return time.time_ns()


def iso(stamp: int) -> str:
    seconds, fraction = divmod(int(stamp), NS)
    moment = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{fraction:09d}Z"


def parse_iso(text: Any) -> int:
    need(isinstance(text, str) and text.strip().endswith("Z"),
         f"时间戳格式不合法: {text!r}", BadInput)
    head, _, tail = text.strip()[:-1].partition(".")
    try:
        moment = datetime.datetime.strptime(head, "%Y-%m-%dT%H:%M:%S")
        fraction = int((tail or "0").ljust(9, "0")[:9])
    except ValueError as exc:
        raise BadInput(f"时间戳格式不合法: {text!r}") from exc
    base = int(moment.replace(tzinfo=datetime.timezone.utc).timestamp())
    return base * NS + fraction


def check_id(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    need(bool(text) and text.isdigit() and int(text) > 0,
         f"Agent ID 必须是正整数: {value!r}", BadInput)
    return str(int(text))


def check_item(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    need(_ITEM.match(text) is not None,
         f"记忆项 ID 必须是 1-64 位字母数字或 ._:-: {value!r}", BadInput)
    return text


def parse_level(value: Any) -> Level:
    if isinstance(value, Level):
        return value
    text = "" if value is None else str(value).strip().upper()
    need(text in (Level.L3.value, Level.L4.value),
         f"记忆层级只能是 L3/L4: {value!r}", BadInput)
    return Level(text)


def parse_items(value: Any) -> List[str]:
    if isinstance(value, (list, tuple)):
        raw = [str(part) for part in value]
    else:
        raw = str(value or "").split(",")
    out: List[str] = []
    for part in raw:
        text = check_item(part.strip()) if part.strip() else ""
        if text and text not in out:
            out.append(text)
    need(bool(out), "至少需要一个记忆项 ID", BadInput)
    return out


def parse_method(value: Any) -> Method:
    if isinstance(value, Method):
        return value
    text = "" if value is None else str(value).strip().lower()
    need(text in {member.value for member in Method},
         f"擦除级别只能是 quick/overwrite/secure/crypto: {value!r}", BadInput)
    return Method(text)


def _minutes(text: Any) -> Optional[Tuple[int, int]]:
    match = _WINDOW.match(text.strip()) if isinstance(text, str) else None
    if match is None:
        return None
    start, sminute, end, eminute = (int(part) for part in match.groups())
    return start * 60 + sminute, end * 60 + eminute


def parse_window(value: Any) -> Tuple[int, int]:
    span = _minutes(value)
    need(span is not None, f"时间窗口必须形如 HH:MM-HH:MM: {value!r}",
         BadInput)
    return span


def check_window(value: Any) -> str:
    need(_minutes(value) is not None,
         f"时间窗口必须形如 HH:MM-HH:MM: {value!r}", BadConfig)
    return str(value).strip()


def in_window(window: str, stamp: int) -> bool:
    start, end = parse_window(window)
    moment = time.localtime(stamp / NS)
    minute = moment.tm_hour * 60 + moment.tm_min
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def block_name(number: int) -> str:
    need(number > 0, f"块序号必须为正: {number!r}", BadInput)
    return f"{number:06d}.airyblk"


def check_block(name: Any) -> str:
    text = "" if name is None else str(name)
    need(_BLOCK.match(text) is not None and not text.startswith("000000"),
         f"数据块名不合法: {name!r}", BadStore)
    return text


def encode_rec(record: Record) -> bytes:
    item = record.item
    ident = item.id.encode("utf-8")
    flags = (1 if record.deleted else 0) | (2 if item.sensitive else 0)
    head = struct.pack(">H", len(ident)) + ident
    body = struct.pack(">BQQI", flags, item.created_at,
                       item.accessed_at, len(item.data))
    return head + body + item.data


def encode_block(records: Iterable[Record]) -> bytes:
    rows = list(records)
    body = b"".join(encode_rec(row) for row in rows)
    return BLOCK_MAGIC + struct.pack(">HI", BLOCK_VERSION, len(rows)) + body


def decode_block(blob: bytes) -> List[Record]:
    need(isinstance(blob, (bytes, bytearray)) and len(blob) >= BLOCK_HEAD,
         "数据块头部不完整", BadStore)
    need(bytes(blob[:len(BLOCK_MAGIC)]) == BLOCK_MAGIC,
         "数据块魔数不匹配", BadStore)
    version, count = struct.unpack(">HI", blob[len(BLOCK_MAGIC):BLOCK_HEAD])
    need(version == BLOCK_VERSION, f"数据块版本不受支持: {version}", BadStore)
    rows: List[Record] = []
    pos = BLOCK_HEAD
    try:
        for _ in range(count):
            need(pos + 2 <= len(blob), "数据块记录头截断", BadStore)
            (width,) = struct.unpack(">H", blob[pos:pos + 2])
            pos += 2
            need(0 < width <= 1024 and pos + width + 21 <= len(blob),
                 "数据块记录标识越界", BadStore)
            ident = bytes(blob[pos:pos + width]).decode("utf-8")
            pos += width
            flags, created, accessed, size = struct.unpack(
                ">BQQI", blob[pos:pos + 21])
            pos += 21
            need(pos + size <= len(blob), "数据块记录负载截断", BadStore)
            payload = bytes(blob[pos:pos + size])
            pos += size
            rows.append(Record(Item(ident, payload, bool(flags & 2),
                                    created, accessed), bool(flags & 1)))
    except BadMemory:
        raise
    except (struct.error, UnicodeDecodeError) as exc:
        raise BadStore(f"数据块解析失败: {exc}") from exc
    need(pos == len(blob), "数据块尾部存在多余字节", BadStore)
    need(len({row.item.id for row in rows}) == len(rows),
         "数据块内记忆项 ID 重复", BadStore)
    return rows


def exit_code(error: BaseException) -> int:
    """异常到退出码的唯一映射：参数 2、状态 3、权限 4，其余按环境错。"""
    if isinstance(error, BadMemory):
        return getattr(error, "code", EXIT_STATE)
    if isinstance(error, (ValueError, TypeError)):
        return EXIT_USAGE
    return EXIT_ENV


def unsupported(method: Method) -> None:
    need(method is not Method.CRYPTO,
         "加密擦除（crypto）依赖 L4 加密存储与 sec_d 密钥托管，"
         "属于数据面能力，本版不提供；请改用 secure 或等待数据面交付",
         BadUnsupported)


__all__.extend(["exit_code", "unsupported"])
