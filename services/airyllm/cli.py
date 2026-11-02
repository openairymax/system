# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行环境命令行入口
#
# 四个子命令对应四件事：status 读已落盘的判定，供监控轮询；verify 强制
# 重算，供排障与门禁；install 把离线包收进运行根；serve 供 systemd 的
# ExecStart 直接拉起引擎。status 与 verify 因此不共用一个实现——一个允许
# 复用缓存，另一个不允许，混成一条命令就只能靠调用方记住该不该加参数。
#
# 退出码 0 就绪、10 降级、1 失败：降级不是故障，服务仍在提供推理能力，
# 只是慢一些；把两者并成一个非零码，监控就无法区分「该扩容显卡」与
# 「该重装资产」。

"""AirymaxOS model command line (K-21-02).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（``degraded:<原因>`` 是稳定的对外 token）与
docs-linux/100-operations/07-systemd-integration.md §3
（ExecStart 只走 ``serve``，判定与降级留痕在启动层完成）。

"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import channel, engine, launch, model, report
from .failure import Failure

__all__ = ["EXIT_DEGRADED", "EXIT_FAIL", "EXIT_OK", "main"]

EXIT_OK = 0

EXIT_FAIL = 1

EXIT_DEGRADED = 10

_CODES = {
    model.State.READY: EXIT_OK,
    model.State.DEGRADED: EXIT_DEGRADED,
    model.State.FAILED: EXIT_FAIL,
}


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行并分派；返回值即进程退出码。"""
    args = _args(argv)
    return args.func(args)


def _args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="airy-model",
        description="AirymaxOS 本地模型运行环境",
    )
    parser.add_argument(
        "--root",
        metavar="DIR",
        help=f"运行根，缺省 {model.ROOT}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    status = sub.add_parser("status", help="显示最近一次运行判定（可复用缓存）")
    status.add_argument("--json", action="store_true", help="以 JSON 输出")
    status.set_defaults(func=_status)
    verify = sub.add_parser("verify", help="重新校验资产并给出判定")
    verify.add_argument("--json", action="store_true", help="以 JSON 输出")
    verify.set_defaults(func=_verify)
    install = sub.add_parser("install", help="把离线模型包收进运行根")
    install.add_argument(
        "--source",
        required=True,
        metavar="DIR",
        help="含 manifest.json 与资产的离线包目录",
    )
    install.set_defaults(func=_install)
    serve = sub.add_parser("serve", help="按判定拉起推理引擎（systemd 用）")
    serve.set_defaults(func=_serve)
    return parser.parse_args(argv)


def _status(args: argparse.Namespace) -> int:
    return _show(engine.cached(args.root), args.json)


def _verify(args: argparse.Namespace) -> int:
    return _show(engine.resolve(args.root), args.json)


def _show(result: model.Status, as_json: bool) -> int:
    """输出判定并按状态取退出码；两种渲染共用同一份字段映射。"""
    if as_json:
        print(json.dumps(report.fields(result), ensure_ascii=False))
    else:
        print(report.line(result))
    return _CODES[result.state]


def _install(args: argparse.Namespace) -> int:
    try:
        entry, copied = channel.apply(args.source, args.root)
    except Failure as exc:
        print(f"airy-model: install: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except OSError as exc:
        print(f"airy-model: install: {exc}", file=sys.stderr)
        return EXIT_FAIL
    print(f"installed: {entry.name} {entry.version}, "
          f"{copied} file(s) written")
    return EXIT_OK


def _serve(args: argparse.Namespace) -> int:
    return launch.run(args.root)
