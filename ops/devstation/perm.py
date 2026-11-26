# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""权限判定：唯一的排序表在 model.RANK，本模块只做拒绝与留痕。

拒绝一律写审计后再抛 BadPerm（退出码 4），所以「拒绝」不会只出现在
终端上而不留证据。admin 动作强制 --yes 二次确认，确认缺失同样算拒绝。
"""

from . import audit
from .model import ACTION, BadPerm, need_perm


def authorize(level: str, action: str, yes: bool = False,
              target: str = "") -> bool:
    """放行或拒绝；任何拒绝都先写审计再抛 BadPerm。"""
    try:
        need_perm(level, action)
        if ACTION[action] == "admin" and not yes:
            raise BadPerm("admin 动作需要 --yes 二次确认: " + action)
    except BadPerm as exc:
        audit.record(level, action, target, "denied", str(exc))
        raise
    return True
