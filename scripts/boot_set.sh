#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# boot_set.sh — 按启用清单启用开机单元
#
# 读 /usr/lib/airymaxos/boot-set（随 airymaxos-system 落盘），逐项启用。
# 三个消费者共用这一份实现：装机（kickstart %post，离线 --root 语义）、
# 升级（本包 %posttrans，活体总线）、以及人工补启用。清单只有一份，动作
# 也只有一份——两处各写一份循环时，新增一项治理只在其中一处落地是必然
# 发生的漂移，症状是某台机器治理静默缺席，从任何服务状态都看不出来。
#
# 用法：
#   boot_set.sh --root=/        装机：systemctl --root 离线启用并回读状态
#   boot_set.sh                 升级：enable --now --no-block，立即生效
#   boot_set.sh --no-start      只下启用位，不启动
#   boot_set.sh --file=PATH     换一份清单（测试与排查用）
#
# 用法要点：
#   - 单元不在位上不算失败，只记 state=absent。清单跨剖面共用，最小安装
#     没有 lightdm，把它当成缺陷会让每个剖面各自维护一份排除表。
#   - 状态不是 enabled 即失败（masked 的单元 enable 无效，管理员有意关闭
#     时应当 mask，此时这里的失败是正确结论，不是误报）。
#   - 每项一行 unit=<名> state=<态>，装机段把整行打到串口，卡住即可归因。
#
# 退出码：0 全部落位；1 有单元未落位；2 用法错误。
# ===========================================================================

set -euo pipefail

readonly DEFAULT_LIST=/usr/lib/airymaxos/boot-set

file="${DEFAULT_LIST}"
root=
start=1

usage() {
    printf 'usage: boot_set.sh [--file=PATH] [--root=PATH] [--no-start]\n' >&2
    exit 2
}

while [ "$#" -gt 0 ]; do
    case "$1" in
    --file=*) file="${1#*=}" ;;
    --root=*) root="${1#*=}" ;;
    --no-start) start=0 ;;
    *) usage ;;
    esac
    shift
done

[ -s "${file}" ] || {
    printf 'boot_set: no list at %s\n' "${file}" >&2
    exit 1
}

sysctl_cmd() {
    if [ -n "${root}" ]; then
        systemctl --root="${root}" "$@"
    else
        systemctl "$@"
    fi
}

enable_one() {
    local unit="$1" state=

    if [ -n "${root}" ] || [ "${start}" -eq 0 ]; then
        sysctl_cmd enable "${unit}" >/dev/null 2>&1 || :
    else
        sysctl_cmd enable --now --no-block "${unit}" >/dev/null 2>&1 || :
    fi
    state=$(sysctl_cmd is-enabled "${unit}" 2>/dev/null || true)
    printf 'unit=%s state=%s\n' "${unit}" "${state:-absent}"
    [ -z "${state}" ] && return 0
    [ "${state}" = enabled ]
}

rc=0
while read -r unit _ || [ -n "${unit}" ]; do
    case "${unit}" in ''|'#'*) continue ;; esac
    enable_one "${unit}" || rc=1
done <"${file}"

exit "${rc}"