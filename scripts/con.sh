#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# con.sh — 开机诊断行的落地通道（ui.sh 与 boot_watch.sh 的唯一实现）
#
# 为什么要有这一份：/dev/console 归内核 console 层，落点由命令行里最后一
# 个 console= 决定。本项目的命令行是
#   console=ttyS0,115200 console=tty0
# 末位是本地屏，于是写进 /dev/console 的每一行都停在 tty0 上，只接串口的
# 现场一行也读不到（run 41925092075 的冷启动就是这样）。串口设备节点不经
# console 层，是控制台层卡死时唯一还能出证据的通道，因此恒为首选；仅当它
# 正是 /dev/console 的落点时才不重复写第二份。
#
# 这条判据此前只长在 boot_watch.sh 里，九阶段打印仍按「只写 /dev/console」
# 处理，同一台机器上于是出现一半行能进串口、一半行不能的分裂。判据收敛到
# 本文件，两个调用方各自只负责措辞。
#
# 环境变量：
#   AIRY_CON_SERIAL  串口设备，缺省 /dev/ttyS0
#   AIRY_CON_ACTIVE  活动控制台列表，缺省 /sys/class/tty/console/active
#   AIRY_CON_UI      本地屏落点，缺省 /dev/console
#   AIRY_CON_WLIM    单次写入的阻塞上限秒数，缺省 2
#
# 用法：
#   con_init              解析通道，结果落在 CON_CH[]
#   con_sink <行>         只写通道（stdout 已由调用方自行处理）
#   con_put  <行...>      先写 stdout，再走 con_sink
# ===========================================================================

[[ -n "${_AIRY_CON_SH:-}" ]] && return 0
readonly _AIRY_CON_SH=1

readonly CON_SER="${AIRY_CON_SERIAL:-/dev/ttyS0}"
readonly CON_ACT="${AIRY_CON_ACTIVE:-/sys/class/tty/console/active}"
readonly CON_UI="${AIRY_CON_UI:-/dev/console}"
# 控制台驱动死锁时写操作不返回，诊断行跟着挂死等于在最需要证据的时候
# 自我灭口；单行写入因此有硬上限。
readonly CON_WLIM="${AIRY_CON_WLIM:-2}"

CON_CH=()

# 可写的串口才纳入通道：设备节点存在但当前身份写不进去（权限、被占用）
# 时写它只会失败或阻塞，落点应退回 /dev/console。判据用可写性而不是设备
# 类型，测试用普通文件顶替串口时也走同一条路径。
con_init() {
    local last=''

    CON_CH=()
    if [ -w "${CON_SER}" ]; then
        CON_CH=("${CON_SER}")
        last="$(tr ' ' '\n' <"${CON_ACT}" 2>/dev/null | sed -n '$p')" || last=''
        if [ "${last}" != "${CON_SER##*/}" ]; then
            CON_CH+=("${CON_UI}")
        fi
    else
        CON_CH=("${CON_UI}")
    fi
}

# stdout 本身就是终端时不再补写：交互执行时那一眼已经看到，补写只会在
# 同一块屏上留下两份。去重按 fd1 的实际指向判定，不看单元声明——声明可以
# 改，fd 不会说谎。
#
# 判定用 /dev/stdout -ef，而不是读 /proc/self/fd/1：readlink 只能放在命令
# 替换里取值，而命令替换的子 shell 自带一条捕获管道，它读到的永远是那条
# 管道，不是本进程的 fd1。这正是旧实现里「去重写了却从未生效」的根因。
con_sink() {
    local line="$*" dev

    if [ -t 1 ]; then
        return 0
    fi
    for dev in "${CON_CH[@]}"; do
        [ -w "${dev}" ] || continue
        if [ /dev/stdout -ef "${dev}" ]; then
            continue
        fi
        printf '%s\n' "${line}" |
            timeout "${CON_WLIM}" tee -a "${dev}" >/dev/null 2>&1 || :
    done
    return 0
}

con_put() {
    printf '%s\n' "$*"
    con_sink "$*"
}
