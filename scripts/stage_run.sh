#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# stage_run.sh — 开机阶段的唯一运行器
#
# 用法：
#   stage_run.sh <阶段名> <宽限码> <取值键> <命令> [参数...]
#
#   <宽限码>  逗号分隔的退出码，按 note 上报；无则写 -
#   <取值键>  逗号分隔，按现场首行的 key=value 取关键值；无则写 -
#
# 为什么只留一份：阶段脚本此前是「每个工具一份」，一旦要改分档、去重、
# 关键值顺序，就得改四五份近似代码，改漏一份的那次开机与别人长得不一样。
# 这里把三件事收敛成一条链路——跑命令、搬关键值、报阶段行；各阶段之间
# 的差异只剩单元文件里的三个参数。
#
# 打印契约（阶段名、序号、列宽）仍只在 ui.sh 的表里；本脚本只负责取值与
# 分档，不复制任何措辞，阶段名写错会由 ui_gate 以 5 报出，不会静默吞掉。
#
# 分档只看工具自己的退出码：0 为落地成功；宽限码（数据分区未启用、实时
# 上限部分落档、机器仍热）是机器的真实状态，按 note 上报；其余一律 FAIL
# 并带上 rc=，让现场一眼看出是判据不满足还是工具崩了。退出码原样透传，
# 单元里的 SuccessExitStatus 与这里的分档必须同源，改一处要同时改另一处。
#
# 阶段行一次开机只有一条：电源、换页上限、桌面共存的单元由 timer 每 60
# 秒重跑，不设闸时同一阶段每分钟刷一行。闸由 ui_gate 统一实现，标记落
# tmpfs，重启即清空。
#
# 闸只压住阶段行，压不住工具自己的快照：工具每轮把 MARK 行与整块明细写到
# stdout，运行器原样转发，同一条结论于是每分钟重进一次 journal。285H 实机
# 2.6 天的 journal 里 AIRYMAX-POWER 与 AIRYMAX-RT-CAP 合计 9886 行，开机
# 头几秒的证据被自己的周期复核埋掉。快照的价值在结论变化，结论不变只说明
# 「还在同一档位运行」，低频心跳即可。
#
# 因此本脚本用「分档 + 关键值」组成结论指纹，记在闸门同一目录；下一轮指纹
# 相同且未过 REPEAT（默认 30 分钟）就不转发快照，指纹变化立即转发，指纹
# 不变但已过 REPEAT 也转发一次当心跳。温度、节流计数、负载这些逐轮都变的
# 明细不进指纹——放进去抑制永远不命中。目录不可写时按「读不到指纹」处理，
# 逐轮照常转发：宁可重复，也不能在受限环境里把证据吞掉。
#
# 打印要能回答「卡在哪一步、卡了多久」：工具的输出要等它退出才取得到（首行
# 就是判据），卡在系统调用里时屏幕上不会再有新行。因此阶段开始先落一行 ...，
# 跑过 BEAT 秒仍未返回就每 BEAT 秒补一行 wait（used/limit）。limit 是预期
# 上限而不是杀进程的手：运行器无权改变服务生命周期，越界与否由单元的
# TimeoutStartSec 收尾，心跳只负责把「慢」与「死」分开。
#
# 没有卡的阶段也要能回答「卡了多久」：结论行在关键值之前带 ms=<毫秒>，是
# 工具从开始执行到退出的实测耗时。开始与结束时间取自 bash 5 的 EPOCHREALTIME
# （内建，不为每一阶段多起两个进程）；取不到时不写 ms=，也不写 0——「没有
# 耗时这个事实」与「耗时是 0 毫秒」在验收现场是两件事。
#
# ms= 不进结论指纹：它是每一轮都变的读数，进指纹等于让抑制永不命中，timer
# 每 60 秒重跑的三个单元又会把同一条结论刷满 journal。指纹仍只取分档与
# 关键值，ms 在打印前才拼上。
#
# 关键值只取工具给出的事实，缺项就不出现：不补默认值，因为「没有这个
# 事实」与「事实是 0」在验收现场是两件不同的事；键的顺序由参数决定，
# 因此工具内部字段顺序变化不会让阶段行的列漂移。
#
# 退出码：透传工具；打印契约库缺失按 6（与其它阶段脚本同一约定）；闸
# 自身出错按闸的退出码（阶段名不在表内为 5）。
# 环境变量：AIRY_STAGE_UI 覆盖契约库路径（回归用）；AIRY_STAGE_BEAT /
# AIRY_STAGE_LIMIT 覆盖心跳周期与预期上限（默认 30s / 60s）；AIRY_STAGE_REPEAT
# 覆盖同一结论的转发间隔（默认 1800s，0 表示每轮都转发）。
# ===========================================================================

set -euo pipefail

readonly UI_LIB="${AIRY_STAGE_UI:-${0%/*}/ui.sh}"
if [ ! -r "${UI_LIB}" ]; then
    printf '[阶段运行器] 打印契约库缺失：%s\n' "${UI_LIB}" >&2
    exit 6
fi
# shellcheck source=ui.sh
. "${UI_LIB}"

readonly USAGE='用法: stage_run.sh <阶段名> <宽限码> <取值键> <命令> [参数...]'
readonly BEAT="${AIRY_STAGE_BEAT:-30}"
readonly LIMIT="${AIRY_STAGE_LIMIT:-60}"
readonly REPEAT="${AIRY_STAGE_REPEAT:-1800}"

# 心跳进程号与判据行必须走全局：前者要与 kill/wait 同处一个 shell，后者
# 要与正文分开走两个通道，两者都经不起命令替换。
TICK=''
LINE=''

# 首行是工具对外的结论行，键值以空格分隔。只认识参数里登记的键，其它
# 字段一律丢弃：工具的调试字段变化不该改变阶段行的形状。
facts() {
    local line="$1" keys="$2"

    [ "${keys}" = '-' ] && return 0
    awk -v keys="${keys//,/ }" '
    BEGIN {
        n = split(keys, order, " ")
        for (i = 1; i <= n; i++) want[order[i]] = 1
    }
    {
        m = split($0, kv, " ")
        for (i = 1; i <= m; i++) {
            p = index(kv[i], "=")
            if (p > 1 && substr(kv[i], 1, p - 1) in want)
                got[substr(kv[i], 1, p - 1)] = substr(kv[i], p + 1)
        }
    }
    END {
        out = ""
        for (i = 1; i <= n; i++) {
            k = order[i]
            if (!(k in got)) continue
            out = (out == "" ? k "=" got[k] : out " " k "=" got[k])
        }
        print out
    }' <<<"${line}"
}

is_note() {
    [ "${NOTES}" = '-' ] && return 1
    case ",${NOTES}," in *",$1,"*) return 0 ;; esac
    return 1
}

emit() {
    local rc="$1"
    shift

    if is_note "${rc}"; then
        ui_note "${STAGE}" "$@"
    elif [ "$#" -gt 0 ]; then
        ui_fail "${STAGE}" "$@" "rc=${rc}"
    else
        ui_fail "${STAGE}" "rc=${rc}"
    fi
}

# 结论指纹的分档部分，与 ok/note/FAIL 三档同源。
band() {
    if [ "$1" -eq 0 ]; then
        printf 'ok'
    elif is_note "$1"; then
        printf 'note'
    else
        printf 'fail'
    fi
}

# 0 转发本轮快照（首轮、结论变化、心跳到期）；1 重复且在 REPEAT 之内。
# 读不到指纹或时间戳时一律转发：受限环境里宁可重复，不能吞掉证据。
keep() {
    local f="$1" was age

    [ -r "${f}" ] || return 0
    was="$(cat "${f}" 2>/dev/null)" || return 0
    [ "${was}" = "$2" ] || return 0
    age=$(($(date +%s) - $(stat -c %Y "${f}" 2>/dev/null || printf 0)))
    [ "${age}" -lt "${REPEAT}" ] || return 0
    return 1
}

mark() {
    local f="$1"

    mkdir -p "${f%/*}" 2>/dev/null || return 0
    printf '%s\n' "$2" >"${f}" 2>/dev/null || :
}

# 微秒时间戳。bash 5 的 EPOCHREALTIME 是内建，取它不为每一阶段多起两个进程；
# 取不到时返回空串，调用方据此让这一行不带 ms=，而不是崩在算术里或写一个
# 假的 0。
stamp() {
    printf '%s' "${EPOCHREALTIME:-}"
}

# 毫秒差。非 C locale 下 EPOCHREALTIME 的小数点可能是逗号，两种分隔符都去掉
# 再按整数相减，结果仍是微秒，最后除 1000 得毫秒（先减后除，避免各自取整
# 丢掉不足 1 毫秒的进位）。任一端缺失就不给结论。
elapsed() {
    local began="$1" now="${EPOCHREALTIME:-}"

    [ -n "${began}" ] && [ -n "${now}" ] || return 0
    printf '%s' $(((${now//[.,]/} - ${began//[.,]/}) / 1000))
}

# 心跳：每 BEAT 秒一行 used/limit。sleep 的 stdout 必须丢掉——它比子壳活得
# 久，若继承调用方的管道，管道的 EOF 要等到 BEAT 秒之后，把测试与采集器
# 一起挂住。正常阶段的耗时远小于 BEAT，开机日志里通常一行心跳都没有。
beat() {
    local used=0

    while sleep "${BEAT}" >/dev/null 2>&1; do
        used=$((used + BEAT))
        ui_beat "${STAGE}" "${used}" "${LIMIT}"
    done
}

# 开跑之前先落阶段行再挂心跳。ui_due 的三种答复各有去处：0 要落结论并
# 心跳，1 是 timer 重跑的静默档，其余是它自己的拒绝码（阶段名不在表内）。
beat_on()
{
    local due=0

    ui_due "${STAGE}" || due=$?
    case "${due}" in
    0)
        ui_begin "${STAGE}"
        beat &
        TICK=$!
        ;;
    1) : ;;
    *) return "${due}" ;;
    esac
    return 0
}

# 收心跳。kill 与 wait 都要留在同一个 shell 里：wait 单独用会一直挂到
# BEAT 秒之后，把本该立刻出结论的阶段拖成慢阶段。
beat_off()
{
    [ -n "${TICK}" ] || return 0
    kill "${TICK}" 2>/dev/null || :
    wait "${TICK}" 2>/dev/null || :
    TICK=''
}

# 判据行与快照去重。指纹只取分档与关键值，ms= 在指纹定下来之后才拼上；
# 顺序反了每轮都会得到新指纹，抑制永不命中。
brief()
{
    local rc="$1" out="$2" spent="$3" snap="$4" vrf

    LINE=$(facts "${out%%$'\n'*}" "${KEYS}")
    vrf="$(band "${rc}")|${LINE}"
    # 指纹已定，此后才拼 ms=：它每轮都变，进了指纹抑制就永不命中。
    if [ -n "${spent}" ]; then
        LINE="ms=${spent}${LINE:+ ${LINE}}"
    fi
    if [ -n "${out}" ] && keep "${snap}" "${vrf}"; then
        printf '%s\n' "${out}"
        mark "${snap}" "${vrf}"
    fi
}

# 出结论行。闸在命令之后：先占标记再执行，命令中途崩掉时标记已被消耗，
# 这条结论就再也不会出现。阶段名已由 ui_due 校验，这里只剩「本次开机
# 已出过结论」一种拒绝。
report()
{
    local rc="$1" line="$2"

    ui_gate "${STAGE}" || return "${rc}"
    if [ "${rc}" -eq 0 ]; then
        if [ -n "${line}" ]; then ui_ok "${STAGE}" "${line}"
        else ui_ok "${STAGE}"; fi
        return 0
    fi
    if [ -n "${line}" ]; then emit "${rc}" "${line}"
    else emit "${rc}"; fi
    return "${rc}"
}

main()
{
    local out='' rc=0 id='' snap='' began='' spent=''

    beat_on || return $?
    id="$(ui_id "${STAGE}")" || return $?
    snap="$(ui_state)/${id}.snap"

    began="$(stamp)"
    out=$("$@") || rc=$?
    spent="$(elapsed "${began}")"
    beat_off

    brief "${rc}" "${out}" "${spent}" "${snap}"
    report "${rc}" "${LINE}"
}

if [ "$#" -lt 4 ]; then
    printf '%s\n' "${USAGE}" >&2
    exit 64
fi
readonly STAGE="$1" NOTES="$2" KEYS="$3"
shift 3

main "$@"
