#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# SPDX-FileCopyrightText: 2025-2026 SPHARX Ltd.
# ===========================================================================
# health_check.sh — agentrt 启动健康自检
#
# 在 agentrt.target 就绪后校验内核、控制面与内存子系统的真实状态：
#   1. 内核版本可获取；
#   2. agentrt.target 已启用时须处于 active，未启用时记跳过；
#   3. 已启用的 agentrt daemon 处于 active，未启用或无载荷时跳过；
#   4. 具备 PMEM 设备时 memoryrovol/pmem 已挂载，无设备时跳过；
#   5. 内核暴露 lru_gen 时 MGLRU 已启用，未暴露时跳过；
#   6. PSI 三个压力文件可读。
#
# 第 2 项分三档而不是两档：整栈默认由 90-airymax.preset 钉在 disabled，
# 而本单元随安装器清单启用。未启用的 target 必然不是 active，若一律记
# 失败，每一台出厂机器开机都会多一条真实但无意义的失败，本单元自身也
# 会因此退出 1 进入 failed——观测者不该制造它要观测的故障。
#
# 第 6 项是必需能力而非可选特性：airy-power 以 PSI 的 cpu/memory/io 三路压力
# 作降档判据，缺 PSI 时它只能退回 loadavg，而 loadavg 对 IO 等待与内存直接
# 回收不敏感——285H 上本地模型长跑时整机卡死，正是这类压力被判据漏掉的表现。
# 五份 airy 内核配置都以 openeuler_defconfig 为基底，PSI 默认开启；因此
# /proc/pressure 缺失只有两种可能：跑了不符合本产品要求的内核，或内核以
# psi=0 启动。两种都要在启动期暴露，而不是让调度静默降级。
#
# 对外只打一行：本脚本是九阶段契约第 9 阶段「自检与取证」的唯一实现，
# 逐项结论仍写 stdout 供 journal 归因；屏幕上的结论汇总由 ui.sh 落盘，
# pass/fail/skip 三项是真实计数，不是文案。
#
# 第 3 项与第 2 项同构，按「载荷是否存在、单元是否启用」分三档：0.0.3 只
# 交付 unit 接口面，0.0.5 起随包交付 config/logger/macro 三个可执行体，其余
# 九个仍属 0.1.x。载荷缺失记跳过（本版不交付）；载荷在而单元被
# 90-airymax.preset 钉在 disabled 且未启用记跳过（不适用）；只有单元已启用
# 却不在 active 才记失败。若沿用「载荷在而进程不在即失败」，本版出厂镜像
# 会因三个已交付但按策略关闭的守护而整机 degraded，桌面探针在第 8 阶段
# 即判负——这与第 2 项要避免的是同一类误报。
#
# 失败数达到阈值且存在 /var/lib/agentrt/.pending-upgrade 时执行
# 原子回滚；回滚工具缺失按 ENOTSUP 显式降级并保留标记，不伪造
# 成功。设计依据：docs-linux/190-distribution/04-update-mechanism.md。
#
# 退出码：
#   0 通过（允许跳过不适用的能力检查）
#   1 存在失败但未达回滚阈值
#   2 失败达到阈值，无待确认升级
#   3 失败达到阈值，回滚工具不可用（ENOTSUP）
#   4 回滚已执行，下次启动生效
#   5 回滚命令执行失败或标记清除失败
#   6 打印契约库 ui.sh 缺失
#
# 环境变量：
#   AIRY_HEALTH_ROOT     校验根前缀，默认 /（离线镜像校验用）
#   AIRY_HEALTH_OSTREE   回滚命令，默认 rpm-ostree
# ===========================================================================

set -euo pipefail

readonly UI_LIB="${AIRY_HEALTH_UI:-${0%/*}/ui.sh}"
if [ ! -r "${UI_LIB}" ]; then
    printf '[健康检查] 打印契约库缺失：%s\n' "${UI_LIB}" >&2
    exit 6
fi
# shellcheck source=ui.sh
. "${UI_LIB}"

readonly BASE="${AIRY_HEALTH_ROOT:-/}"
readonly ROOT="${BASE%/}"
readonly OSTREE="${AIRY_HEALTH_OSTREE:-rpm-ostree}"
readonly STATE="${ROOT}/var/lib/agentrt"
readonly PENDING="${STATE}/.pending-upgrade"
readonly PMEM_DEV="${ROOT}/dev/pmem0"
readonly PMEM_DIR="${STATE}/memoryrovol/pmem"
readonly MOUNTINFO="${ROOT}/proc/self/mountinfo"
readonly LRU_FILE="${ROOT}/sys/kernel/mm/lru_gen/enabled"
readonly PSI_DIR="${ROOT}/proc/pressure"
readonly MAX_FAILS=3
readonly DAEMONS=(
    gateway cogn dev sched macro-superv audit
    net mem sec logger vfs config
)

npass=0
nfail=0
nskip=0

mark_pass() {
    printf '  [%s] %s: OK\n' "$1" "$2"
    npass=$((npass + 1))
}

mark_fail() {
    printf '  [%s] %s: FAIL (%s)\n' "$1" "$2" "$3" >&2
    nfail=$((nfail + 1))
}

mark_skip() {
    printf '  [%s] %s: SKIP (%s)\n' "$1" "$2" "$3"
    nskip=$((nskip + 1))
}

check_kernel() {
    local rel
    if ! rel=$(uname -r) || [[ -z $rel ]]; then
        mark_fail 1 kernel "uname unavailable"
        return
    fi
    mark_pass 1 "kernel ${rel}"
}

check_target() {
    if ! systemctl is-enabled --quiet agentrt.target 2>/dev/null; then
        mark_skip 2 "agentrt.target" "not enabled"
    elif systemctl is-active --quiet agentrt.target; then
        mark_pass 2 "agentrt.target"
    else
        mark_fail 2 "agentrt.target" inactive
    fi
}

daemon_binary() {
    local spec
    if ! spec=$(systemctl show --property=ExecStart --value "$1" 2>/dev/null); then
        return 0
    fi
    sed -n 's/.*path=\([^ ;]*\).*/\1/p' <<<"$spec"
}

# 归类时先看载荷再看启用状态：本版不交付的单元没有可判的运行态。
daemon_state() {
    local unit=$1 bin
    bin=$(daemon_binary "$unit")
    if [[ -z $bin || $bin == '(null)' || ! -x $bin ]]; then
        printf 'absent\n'
    elif ! systemctl is-enabled --quiet "$unit" 2>/dev/null; then
        printf 'off\n'
    elif ! systemctl is-active --quiet "$unit"; then
        printf 'down\n'
    else
        printf 'up\n'
    fi
}

check_daemons() {
    local name state down=0 absent=0 off=0
    for name in "${DAEMONS[@]}"; do
        state=$(daemon_state "agentrt-${name}.service")
        case $state in
            absent) absent=$((absent + 1)) ;;
            off) off=$((off + 1)) ;;
            down) down=$((down + 1)) ;;
        esac
    done
    if ((down == 0 && absent == 0 && off == 0)); then
        mark_pass 3 "${#DAEMONS[@]} daemons"
    elif ((down == 0)); then
        mark_skip 3 daemons "${absent} without payload, ${off} disabled"
    else
        mark_fail 3 daemons "${down} down, ${absent} without payload, ${off} disabled"
    fi
}

check_pmem() {
    if [[ ! -e $PMEM_DEV ]]; then
        mark_skip 4 "MemoryRovol PMEM" "no PMEM device"
        return
    fi
    if awk -v path="$PMEM_DIR" '$5 == path { found = 1 }
        END { exit(found ? 0 : 1) }' "$MOUNTINFO" 2>/dev/null; then
        mark_pass 4 "MemoryRovol PMEM"
    else
        mark_fail 4 "MemoryRovol PMEM" "not mounted"
    fi
}

check_mglru() {
    local raw val
    if [[ ! -r $LRU_FILE ]]; then
        mark_skip 5 MGLRU "lru_gen not exposed"
        return
    fi
    if ! raw=$(<"$LRU_FILE"); then
        mark_fail 5 MGLRU "unreadable"
        return
    fi
    if [[ $raw =~ ^0x[0-9a-fA-F]+$ || $raw =~ ^[0-9]+$ ]]; then
        val=$((raw))
    else
        mark_fail 5 MGLRU "unexpected value ${raw}"
        return
    fi
    if ((val > 0)); then
        mark_pass 5 MGLRU
    else
        mark_fail 5 MGLRU disabled
    fi
}

check_psi() {
    local node missing=
    for node in cpu memory io; do
        [ -r "${PSI_DIR}/${node}" ] || missing="${missing} ${node}"
    done
    if [ -n "${missing}" ]; then
        mark_fail 6 PSI "unreadable:${missing}"
        return
    fi
    mark_pass 6 PSI
}

drop_pending() {
    if ! rm -f "$PENDING"; then
        printf '[健康检查] 无法清除待确认升级标记\n' >&2
        return 5
    fi
}

do_rollback() {
    if [[ ! -e $PENDING ]]; then
        printf '[健康检查] 失败 %d 项，无待确认升级\n' "$nfail" >&2
        return 2
    fi
    if ! command -v "$OSTREE" >/dev/null 2>&1; then
        printf '[健康检查] 失败 %d 项，回滚工具 %s 不可用' \
            "$nfail" "$OSTREE" >&2
        printf '（ENOTSUP），保留升级标记\n' >&2
        return 3
    fi
    if ! "$OSTREE" rollback; then
        printf '[健康检查] 回滚命令执行失败，保留升级标记\n' >&2
        return 5
    fi
    drop_pending || return 5
    printf '[健康检查] 已回滚，下次启动进入前一版本\n' >&2
    return 4
}

report() {
    local facts="pass=${npass} fail=${nfail} skip=${nskip}"
    if ((nfail > 0)); then
        ui_fail 自检与取证 "${facts}"
    else
        ui_ok 自检与取证 "${facts}"
    fi
}

main() {
    local rc=0
    ui_begin 自检与取证
    check_kernel
    check_target
    check_daemons
    check_pmem
    check_mglru
    check_psi
    report
    if ((nfail >= MAX_FAILS)); then
        do_rollback || rc=$?
        return "$rc"
    fi
    if ((nfail > 0)); then
        return 1
    fi
    if [[ -e $PENDING ]]; then
        drop_pending || return 5
        printf '[健康检查] 升级观察期通过，固化当前版本\n'
    fi
    return 0
}

main "$@"
