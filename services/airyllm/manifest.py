# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型清单解析
#
# 清单是运行期唯一的事实来源：引擎、量化规格与资产摘要都由它给出。
# 解析只回答「这份清单是否自洽」，不回答「资产是否与清单相符」——
# 后者要读磁盘，属于校验层。字段按白名单收，多一个键即拒绝：清单是
# 外部交付物，宽松解析会让拼错的可选字段静默变成默认值。

"""AirymaxOS model manifest parsing (K-20-03).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（清单字段、资产以 sha256 绑定）。

"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import model
from .failure import Failure

__all__ = ["load", "parse"]

HEX = re.compile(r"\A[0-9a-f]{64}\Z")


def parse(payload: str) -> model.Manifest:
    """把清单文本解析为契约对象；任何偏离都抛 :class:`Failure`。"""
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise Failure(f"清单不是合法 JSON: {exc.msg}",
                      model.Reason.NO_MANIFEST) from exc
    if not isinstance(obj, dict):
        raise Failure("清单顶层必须是对象", model.Reason.NO_MANIFEST)
    _keys(obj, model.MANIFEST_FIELDS, model.Reason.NO_MANIFEST)
    version = obj["schema"]
    if not isinstance(version, int) or isinstance(version, bool) \
            or version != model.SCHEMA_VERSION:
        raise Failure(f"清单版本 {version!r} 不受支持",
                      model.Reason.NO_MANIFEST)
    return model.Manifest(
        schema=model.SCHEMA_VERSION,
        name=_text(obj, "name"),
        version=_text(obj, "version"),
        quant=_text(obj, "quant"),
        runtime=_runtime(obj["runtime"]),
        assets=_assets(obj["assets"]),
    )


def load(path: Path) -> model.Manifest:
    """读文件并解析；读取失败与格式错误同样归为清单不可用。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Failure(f"清单不可读: {exc.strerror}",
                      model.Reason.NO_MANIFEST) from exc
    return parse(text)


def _keys(obj: dict, fields: frozenset, reason: model.Reason) -> None:
    extra = set(obj) - fields
    missing = fields - set(obj)
    if extra:
        raise Failure(f"清单含未知字段: {sorted(extra)}", reason)
    if missing:
        raise Failure(f"清单缺字段: {sorted(missing)}", reason)


def _text(obj: dict, key: str) -> str:
    value = obj[key]
    if not isinstance(value, str) or not value:
        raise Failure(f"字段 {key} 必须是非空字符串",
                      model.Reason.NO_MANIFEST)
    return value


def _runtime(obj) -> model.Runtime:
    if not isinstance(obj, dict):
        raise Failure("runtime 必须是对象", model.Reason.NO_RUNTIME)
    _keys(obj, model.RUNTIME_FIELDS, model.Reason.NO_RUNTIME)
    backends = obj["backends"]
    if not isinstance(backends, list) or not backends:
        raise Failure("runtime.backends 必须是非空列表",
                      model.Reason.NO_RUNTIME)
    return model.Runtime(
        engine=_text(obj, "engine"),
        version=_text(obj, "version"),
        path=_text(obj, "path"),
        backends=tuple(_backend(token) for token in backends),
    )


def _backend(token) -> model.Backend:
    try:
        return model.Backend(token)
    except ValueError as exc:
        raise Failure(f"未知后端: {token!r}", model.Reason.NO_ACCEL) from exc


def _assets(obj) -> tuple[model.Asset, ...]:
    if not isinstance(obj, list) or not obj:
        raise Failure("assets 必须是非空列表", model.Reason.NO_MODEL)
    assets = tuple(_asset(item) for item in obj)
    paths = [asset.path for asset in assets]
    if len(set(paths)) != len(paths):
        raise Failure("assets 含重复路径", model.Reason.ASSET_CORRUPT)
    return assets


def _asset(obj) -> model.Asset:
    if not isinstance(obj, dict):
        raise Failure("资产项必须是对象", model.Reason.ASSET_CORRUPT)
    _keys(obj, model.ASSET_FIELDS, model.Reason.ASSET_CORRUPT)
    path = _text(obj, "path")
    _safe(path)
    size = obj["size"]
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise Failure(f"资产 {path} 的 size 非法", model.Reason.ASSET_CORRUPT)
    digest = obj["sha256"]
    if not isinstance(digest, str) or not HEX.match(digest):
        raise Failure(f"资产 {path} 的 sha256 非法",
                      model.Reason.DIGEST_MISMATCH)
    return model.Asset(path=path, size=size, sha256=digest)


def _safe(path: str) -> None:
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or "" in parts:
        raise Failure(f"资产路径越界: {path!r}", model.Reason.ASSET_CORRUPT)
