# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.

"""AirymaxOS 备份与恢复（P-15）。

设计口径：docs-linux/100-operations/06-backup-recovery.md。
本包只做四件事：把源目录确定性地打成一个归档、给归档留一份可核验的
清单、按保留策略清理旧件、以及在核验通过后把归档放回目标根。签名与
校验和是恢复前的门，不是事后审计。
"""

__all__ = ["model", "archive", "store", "cli"]
