#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# boot_watch.sh — 启动卡点的自证报告
#
# 卡住时为什么查不出来：九阶段打印只覆盖本项目自己的单元，而它们都排在
# 某个 target 之前或之后。一旦上游的某个启动作业不返回（登录管理器等
# dbus 名称、plymouth 的等待单元、文件系统检查），systemd 就不再写新行，
# 屏幕停在最后一条 Starting，graphical.target 与 multi-user.target 双双
# 达不到；第九阶段挂在目标之后，于是永远不会运行，串口里没有一行可归因
# 的事实，现场只知道「没起来」。
#
# 本脚本不挂在任何 target 上，由 airy-boot-watch.timer 在开机 60 秒拉起，
# 目标达不成也照样执行。判定只认观测：取默认目标，在观测窗口内反复采样
# 作业表，目标始终未激活且作业表逐字不变才算卡住，此时一次性打印卡点
# 证据——目标与首个未完成作业的单元事实、未完成作业、失败单元数、控制台
# 归属、内核命令行——最后给出下一步命令。作业表在变说明还在推进，慢不是
# 卡，静默退出；目标已达成同样静默，健康路径不留一行。
#
# 退出码恒为 0：本单元是观测者，卡住的是别的单元。以非零退出会在
# systemctl --failed 里多出一条由观测者造成的失败，掩盖真正的失败者，
# 也让「装机后失败单元为 0」的交付判据失去意义。
#
# stderr 并入 stdout：诊断行落进 journal 的 err 桶会让
# `journalctl -p err -b` 非空，而它同样是交付判据。
#
# 每条事实同时写 stdout（journal）、/dev/console 与串口（若存在）：带串口
# 的机器上 /dev/console 只归内核命令行里最后一个 console=，本地屏与串口
# 未必同源，只打一条通道等于在另一侧失明。
#
# 部署：airy-boot-watch.service，由 airy-boot-watch.timer 触发。
# ===========================================================================
set -euo pipefail
# 早于任何输出：本脚本的 stderr 不得进入 journal 的 err 桶。
exec 2>&1

readonly POLL="${AIRY_WATCH_POLL:-10}"
readonly WIN="${AIRY_WATCH_WIN:-40}"
readonly JTOT="${AIRY_WATCH_JOBS:-8}"
readonly CONSOLE="${UI_CONSOLE:-/dev/console}"
readonly SERIAL="${AIRY_WATCH_SERIAL:-/dev/ttyS0}"
readonly SELF=airy-boot-watch

IDLE=0
ALT=''

console_has() {
    tr ' ' '\n' </sys/class/tty/console/active 2>/dev/null | grep -qx "$1"
}

# 串口已被 /dev/console 占用时不再单独写一份，否则同一行在串口出现两次。
# 判据取内核对活动控制台的登记，不比较设备节点：/dev/console 与 ttyS0
# 是两个不同的节点，重定向关系只存在于内核里。
if [ -c "${SERIAL}" ] && ! console_has "${SERIAL##*/}"; then
    ALT="${SERIAL}"
fi

put() {
    local line="$*" dev
    local devs=("${CONSOLE}")

    if [ -n "${ALT}" ]; then
        devs+=("${ALT}")
    fi
    printf '%s\n' "${line}"
    for dev in "${devs[@]}"; do
        if [ -w "${dev}" ]; then
            printf '%s\n' "${line}" >>"${dev}" 2>/dev/null || :
        fi
    done
}

show() {
    local v

    v="$(systemctl show -p "$2" --value "$1" 2>/dev/null)" || v=''
    printf '%s' "${v:-unknown}"
}

def_tgt() {
    local t

    t="$(systemctl get-default 2>/dev/null)" || t=''
    printf '%s' "${t:-graphical.target}"
}

is_done() {
    systemctl is-active --quiet "$1" 2>/dev/null
}

# 未完成作业表：本单元自己的那条作业排除在外，它每轮都在，会把「作业表
# 逐字不变」永远否定掉。排序后比较，systemd 的输出顺序不参与判定。
pend() {
    local raw=''

    raw="$(systemctl list-jobs --no-legend --plain --no-pager 2>/dev/null)" ||
        raw=''
    printf '%s\n' "${raw}" |
        awk -v self="${SELF}" 'NF && index($0, self) == 0' |
        LC_ALL=C sort
}

# 0 卡住；1 目标已达成；2 作业表在变（还在推进）。
# 作业表为空同样算「没在推进」：目标未达成却没有任何作业，说明 systemd
# 已停在别处（救援目标、isolate），这比继续等更值得报出来。
watch() {
    local tgt="$1" last='' cur step=0

    while [ "${step}" -lt "${WIN}" ]; do
        if is_done "${tgt}"; then
            return 1
        fi
        cur="$(pend)"
        if [ "${step}" -gt 0 ] && [ "${cur}" != "${last}" ]; then
            return 2
        fi
        last="${cur}"
        sleep "${POLL}"
        step=$((step + POLL))
    done
    if is_done "${tgt}"; then
        return 1
    fi
    IDLE="${WIN}"
    return 0
}

rep_tgt() {
    local tgt="$1" act sub up

    act="$(show "${tgt}" ActiveState)"
    sub="$(show "${tgt}" SubState)"
    up="$(cut -d. -f1 /proc/uptime)" || up=0
    put "AIRYMAX-BOOT-STUCK target=${tgt} active=${act}" \
        "sub=${sub} uptime=${up}s idle=${IDLE}s"
}

rep_jobs() {
    local lines=() i=0 job unit typ st

    mapfile -t lines < <(pend)
    put "AIRYMAX-BOOT-JOBS n=${#lines[@]}"
    while [ "${i}" -lt "${#lines[@]}" ] && [ "${i}" -lt "${JTOT}" ]; do
        read -r job unit typ st <<<"${lines[i]}"
        put "AIRYMAX-BOOT-JOB unit=${unit} type=${typ} state=${st}" \
            "job=${job}"
        i=$((i + 1))
    done
}

rep_unit() {
    local u="$1" act sub pid res

    act="$(show "${u}" ActiveState)"
    sub="$(show "${u}" SubState)"
    pid="$(show "${u}" MainPID)"
    res="$(show "${u}" Result)"
    put "AIRYMAX-BOOT-UNIT unit=${u} active=${act}" \
        "sub=${sub} pid=${pid} result=${res}"
}

rep_fail() {
    local n=''

    n="$(systemctl list-units --failed --no-legend --plain --no-pager \
        2>/dev/null | awk 'NF { c++ } END { print c + 0 }')" || n=0
    put "AIRYMAX-BOOT-FAILED n=${n:-0}"
}

rep_console() {
    local act=''

    act="$(tr ' ' ',' </sys/class/tty/console/active 2>/dev/null)" || act=''
    put "AIRYMAX-BOOT-CONSOLE active=${act:-unknown}"
}

rep_cmd() {
    local cmd=''

    cmd="$(< /proc/cmdline)" || cmd=''
    put "AIRYMAX-BOOT-CMDLINE argv=\"${cmd:-unknown}\""
}

main() {
    local tgt first

    tgt="$(def_tgt)"
    if ! watch "${tgt}"; then
        return 0
    fi
    first="$(pend | awk 'NR == 1 { print $2 }')"
    rep_tgt "${tgt}"
    rep_jobs
    if [ -n "${first}" ]; then
        rep_unit "${first}"
    fi
    rep_unit "${tgt}"
    rep_fail
    rep_console
    rep_cmd
    put "AIRYMAX-BOOT-HINT cmd=\"systemctl list-jobs;" \
        "systemctl status ${first:-${tgt}}\""
    return 0
}

main "$@"
