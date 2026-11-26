# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
"""python3 -m devstation 入口。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
