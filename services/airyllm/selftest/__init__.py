# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
#
# AirymaxOS 模型运行环境离线自检
#
# 自检回答一个问题：这台机器现在能不能在完全断网的情况下把本地模型跑
# 起来。它不新写判定，只把既有各层的结论按依赖顺序串成一条可核对的链：
# 运行根可写、清单可解析、资产与摘要相符、引擎可执行、引擎能跑、后端能
# 选、启动参数能派生、判定能落盘。
#
# 任何一项失败都必须给出归因并落盘，被它阻塞的后续项按名标为 blocked，
# 不允许从输出里消失——静默跳过与静默成功在运维上都同样是「没有信息」。

"""AirymaxOS model runtime self-test package (K-23).

设计依据：docs-linux/140-application-development/06-agent-deployment.md §2
（完全断网可自检；清单与实物不符即硬失败）与
docs-linux/190-distribution/04-update-mechanism.md §5.3
（首启自检的判定与落点）。

本包只做编排：解析、校验、探测、持久化各有唯一实现，自检若另写一份，
两处迟早给出不同结论。

"""

from __future__ import annotations

from .steps import run
from .verdict import CHECKS, Report, Result

__all__ = ["CHECKS", "Report", "Result", "run"]
