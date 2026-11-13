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
# 本脚本不参与启动排序，由 airy-boot-watch.timer 在开机 60 秒拉起，目标
# 达不成也照样执行。定时器与服务的 DefaultDependencies 都必须关掉：两类
# 单元的缺省依赖都自带 Requires/After=sysinit.target，只看显式那一行而
# 留着缺省，看门狗仍然排在目标之后，在唯一需要它的场景里永远拿不到执行
# 机会（285H 2026-11-09 的看门狗 journal 为空，正是这个形状）。
#
# 判定只认观测：取默认目标，在观测窗口内反复采样作业表，窗口结束时目标
# 仍未激活就一次性打印卡点证据——目标与首个未完成作业的单元事实、未完成
# 作业、失败单元数、控制台归属、内核命令行——最后给出下一步命令。目标已
# 达成且登录管理器在跑则静默，健康路径不留一行。
#
# 卡住的单元在阻塞前通常已经写下最后一句（等 D-Bus 名、等设备、重试超时）。
# 报告附 journal 末尾若干行，并整条命令限时：journald 不可用或自身阻塞时
# 跳过该段，观测者不得成为新的卡点。
#
# 九阶段行只覆盖本项目自己的单元，卡住的常常不是它们：桌面停在最后一条
# Starting，九行阶段行里看不出停在第几步之后。报告因此另取一段事实——本次
# 开机 airymax-boot 的最后一条阶段行落在第几步、结论是 ok 还是 wait，以及
# 一共出现过几步。一行都没有同样要报（seen=0 即「还没走到第一阶段」，
# initramfs 或根切换就停了），这比只看 journal 末尾更能钉住卡在哪一层。
#
# 作业表在变不再判为健康：登录管理器反复重启、会话刚拉起就崩溃时，作业
# 表每轮都在变，目标是永远达不到的，而按「在变即推进」处理会一句话都不留
# （run 41839320731 的冷启动就停在这种形态上）。推进与停滞的区分改由
# churn 字段承载：0 表示窗口内作业表逐字未变、1 表示反复变化，两者都是
# 「开机后 100 秒仍未到默认目标」这一事实，都值得报出来。
#
# 目标达成同样不等于有会话：graphical.target 只要求登录管理器的启动作业
# 结束，管理器退出、被禁用、fail 掉之后目标照样激活，tty1 留着 getty 的
# 登录提示。此时作业表是空的、目标是绿的，前两种判据都无从触发，串口只剩
# 一行 login: ——正是「装完盘、能登录、但桌面从没起来」的形态。故目标激活
# 后再验一次登录管理器：graphical 目标下没有任何 display-manager 在跑，
# 就以 reason=nodm 报出同一条卡点事实。
#
# 退出码恒为 0：本单元是观测者，卡住的是别的单元。以非零退出会在
# systemctl --failed 里多出一条由观测者造成的失败，掩盖真正的失败者，
# 也让「装机后失败单元为 0」的交付判据失去意义。
#
# stderr 并入 stdout：诊断行落进 journal 的 err 桶会让
# `journalctl -p err -b` 非空，而它同样是交付判据。
#
# 每条事实同时写 stdout（journal）与串口设备节点：/dev/console 只归内核
# 命令行里最后一个 console=，本项目的命令行是
# console=ttyS0,115200 console=tty0，落点是本地屏，写进去的每一行都停在
# tty0 上，只有串口的现场读不到（run 41925092075 的冷启动就是这样：探针
# 判负并打印了，串口里却一条卡点事实都没有）。通道判据与九阶段打印共用
# con.sh：串口优先、/dev/console 次之，两者同源时只写一份。
#
# 部署：airy-boot-watch.service，由 airy-boot-watch.timer 触发。
# ===========================================================================
set -euo pipefail
# 早于任何输出：本脚本的 stderr 不得进入 journal 的 err 桶。
exec 2>&1

readonly POLL="${AIRY_WATCH_POLL:-10}"
readonly WIN="${AIRY_WATCH_WIN:-40}"
readonly JTOT="${AIRY_WATCH_JOBS:-8}"
readonly TAIL="${AIRY_WATCH_TAIL:-8}"
readonly JTIMEOUT="${AIRY_WATCH_JTIME:-5}"
readonly CONSOLE="${UI_CONSOLE:-/dev/console}"
readonly SERIAL="${AIRY_WATCH_SERIAL:-/dev/ttyS0}"
readonly ACTIVE="${AIRY_WATCH_ACTIVE:-/sys/class/tty/console/active}"
readonly SELF=airy-boot-watch

IDLE=0
CHURN=0

# 通道判据的唯一实现见 con.sh；历史变量名在此转名，回归与现场口径不变。
# shellcheck source-path=SCRIPTDIR source=con.sh
AIRY_CON_SERIAL="${SERIAL}" AIRY_CON_ACTIVE="${ACTIVE}" AIRY_CON_UI="${CONSOLE}" \
    . "${BASH_SOURCE[0]%/*}/con.sh"
con_init

put() {
    con_put "$@"
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

# 0 窗口结束时目标仍未达成；1 目标已达成。
# 作业表的变化只记进 CHURN，不再作为「健康」的判据：反复重启的单元同样
# 让作业表每轮不同，按变化提前收工等于对最需要证据的那类卡点失明。
# 作业表为空同样算没在推进：目标未达成却没有任何作业，说明 systemd 已停
# 在别处（救援目标、isolate），这比继续等更值得报出来。
watch() {
    local tgt="$1" last='' cur step=0

    while [ "${step}" -lt "${WIN}" ]; do
        if is_done "${tgt}"; then
            return 1
        fi
        cur="$(pend)"
        if [ "${step}" -gt 0 ] && [ "${cur}" != "${last}" ]; then
            CHURN=1
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
    local tgt="$1" reason="$2" act sub up

    act="$(show "${tgt}" ActiveState)"
    sub="$(show "${tgt}" SubState)"
    up="$(cut -d. -f1 /proc/uptime)" || up=0
    put "AIRYMAX-BOOT-STUCK target=${tgt} active=${act}" \
        "sub=${sub} uptime=${up}s idle=${IDLE}s churn=${CHURN}" \
        "reason=${reason}"
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

# journal 末尾若干行：阻塞前的最后一句往往就是卡点所在。整条命令限时，
# 读不到（无权限、journald 未起、超时）即跳过——证据段可缺省，观测者
# 不能因为取证而变成新的卡点。
rep_last() {
    local out='' line=''

    out="$(timeout "${JTIMEOUT}" journalctl -b -n "${TAIL}" --no-pager \
        -o short-monotonic 2>/dev/null)" || return 0
    while IFS= read -r line; do
        [ -n "${line}" ] || continue
        put "AIRYMAX-BOOT-LAST ${line}"
    done <<<"${out}"
    return 0
}

# 九阶段走到第几步。取中文行（logger -t airymax-boot）而不是各单元的 stdout
# 行：两者的序号、token 与关键值逐字相同，而中文行恰好每阶段每开机一条，
# 不会被单元的 stdout 重复计入。阶段行的形状是 `[ 5/9 ] 名称  token ...`，
# 前三个字段是界符，名称与 token 依次落在第四、第五个字段。
#
# 一行都没有同样是事实：seen=0 说明开机在第一个阶段之前就停了。命令本身
# 失败（无权限、journald 未起）则整段跳过，观测者不得因取证而制造新的失败。
#
# 停在最后一步多久，要与「现在」同源才读得出来：起始行与心跳行自带
# up=<开机秒数>，本段把最后一个 up= 原样带出，并按同一个开机时钟算出
# stall=<离最后一行的秒数>。两个读数都不经过墙钟，NTP 校时不会把停滞时长
# 算成负数或天文数字。结论行（ok/FAIL）不带 up=，此时 stall= 是「结论之后
# 再没有阶段行」的时长，与 wait 行的小 stall 合起来正好区分「还在跑」与
# 「已经不出声」。
#
# 求和与停滞分开：行扫描只认阶段行本身，读数与算术各归一处。两件事混在
# 一段里时判定点叠成十一层，改动任何一侧都要重读整段 awk。
_stage_sum() {
    awk '
    $1 == "[" && $2 ~ /^[0-9]+\/[0-9]+$/ && NF >= 5 {
        split($2, seq, "/")
        seen[seq[1]] = 1
        total = seq[2]
        last = seq[1]
        tok = $5
    }
    END {
        n = 0
        for (id in seen) n++
        if (n == 0) {
            print "seen=0 last=0 token=none"
            exit
        }
        printf "seen=%d/%s last=%s token=%s\n", n, total, last, tok
    }'
}

# 停滞时长：取全部阶段行里最后一个 up=<秒>，原样带出并按开机时钟算出停滞
# 秒数；时钟读不到时只留 up=，早于起始时刻的差值按 0 处理，不拿负数或 0
# 冒充「刚刚还在推进」。
_stall() {
    local hit='' now='' at='' gap=0

    hit="$(printf '%s\n' "$1" | grep -o 'up=[0-9][0-9]*s' | tail -n 1)" ||
        return 0
    [ -n "${hit}" ] || return 0
    now="$(cut -d. -f1 /proc/uptime 2>/dev/null)" || now=''
    printf ' %s' "${hit}"
    [ -n "${now}" ] || return 0
    at="${hit#up=}"
    at="${at%s}"
    gap=$((now - at))
    [ "${gap}" -ge 0 ] || gap=0
    printf ' stall=%ds' "${gap}"
}

rep_stage() {
    local out='' sum='' st=''

    out="$(timeout "${JTIMEOUT}" journalctl -b -t airymax-boot --no-pager \
        -o cat 2>/dev/null)" || return 0
    sum="$(printf '%s\n' "${out}" | _stage_sum)"
    [ -n "${sum}" ] || return 0
    st="$(_stall "${out}")"
    put "AIRYMAX-BOOT-STAGE ${sum}${st}"
    return 0
}

rep_fail() {
    local n=''

    n="$(systemctl list-units --failed --no-legend --plain --no-pager \
        2>/dev/null | awk 'NF { c++ } END { print c + 0 }')" || n=0
    put "AIRYMAX-BOOT-FAILED n=${n:-0}"
}

rep_console() {
    local act=''

    act="$(tr ' ' ',' <"${ACTIVE}" 2>/dev/null)" || act=''
    put "AIRYMAX-BOOT-CONSOLE active=${act:-unknown}"
}

rep_cmd() {
    local cmd=''

    cmd="$(< /proc/cmdline)" || cmd=''
    put "AIRYMAX-BOOT-CMDLINE argv=\"${cmd:-unknown}\""
}

# 登录管理器是否在跑。display-manager.service 是 enable 之后得到的别名，
# 别名缺失（管理器只是被手工拉起过）时退回具体单元名，两者任一在跑即算。
dm_live() {
    local u

    for u in display-manager.service lightdm.service; do
        if is_done "${u}"; then
            return 0
        fi
    done
    return 1
}

# 0.0.4 起的命令行不再带 rhgb，plymouth 单元也不参与启动：阶段行与卡点
# 报告写进 /dev/console 即直落 VT，屏幕上是逐行的「哪一步、等了多久、哪条
# 作业还挂着」。本函数只为仍带着旧参数的升级机器保留：那里启动画面会盖住
# 控制台输出，判定卡点后先收起画面，已写下的事实才看得见。plymouth 缺席
# （无图形剖面、最小安装）或未运行（已被 plymouth.enable=0 关掉）时无事
# 可做。
unveil() {
    command -v plymouth >/dev/null 2>&1 || return 0
    plymouth --ping 2>/dev/null || return 0
    plymouth --hide-splash >/dev/null 2>&1 || :
    return 0
}

main() {
    local tgt first reason

    tgt="$(def_tgt)"
    if watch "${tgt}"; then
        reason=unreached
    elif [ "${tgt}" = graphical.target ] && ! dm_live; then
        reason=nodm
    else
        return 0
    fi
    unveil
    first="$(pend | awk 'NR == 1 { print $2 }')"
    rep_tgt "${tgt}" "${reason}"
    rep_jobs
    if [ -n "${first}" ]; then
        rep_unit "${first}"
    fi
    rep_unit "${tgt}"
    rep_stage
    rep_last
    rep_fail
    rep_console
    rep_cmd
    put "AIRYMAX-BOOT-HINT cmd=\"systemctl list-jobs;" \
        "systemctl status ${first:-${tgt}}\""
    return 0
}

main "$@"
