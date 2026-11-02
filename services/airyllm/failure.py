# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 大模型运行环境的唯一失败类型
#
# 解析、校验、探测与自检四层都会拒绝，状态层只认 reason 一个字段。
# 若每层各定义一种异常，状态层就要维护一张映射表，而映射表迟早与
# 真实抛出的类型对不上。因此拒绝只有一种类型，归因由抛出点决定。

"""AirymaxOS model runtime failure (K-20-04).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（清单不符即硬失败，不做静默降级）。

"""

from __future__ import annotations

from . import model

__all__ = ["Failure"]


class Failure(ValueError):
    """运行期不可用。

    Attributes:
        reason: 可直接落盘的归因，状态层不再做映射。
        path: 出问题的资产路径；与具体资产无关时为空串。
    """

    def __init__(self, message: str, reason: model.Reason,
                 path: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.path = path
