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
# tmpfs，重启即清空；周期复核的完整快照照旧逐轮进 journal。
#
# 打印要能回答「卡在哪一步、卡了多久」：工具的输出要等它退出才取得到（首行
# 就是判据），卡在系统调用里时屏幕上不会再有新行。因此阶段开始先落一行 ...，
# 跑过 BEAT 秒仍未返回就每 BEAT 秒补一行 wait（used/limit）。limit 是预期
# 上限而不是杀进程的手：运行器无权改变服务生命周期，越界与否由单元的
# TimeoutStartSec 收尾，心跳只负责把「慢」与「死」分开。
#
# 关键值只取工具给出的事实，缺项就不出现：不补默认值，因为「没有这个
# 事实」与「事实是 0」在验收现场是两件不同的事；键的顺序由参数决定，
# 因此工具内部字段顺序变化不会让阶段行的列漂移。
#
# 退出码：透传工具；打印契约库缺失按 6（与其它阶段脚本同一约定）；闸
# 自身出错按闸的退出码（阶段名不在表内为 5）。
# 环境变量：AIRY_STAGE_UI 覆盖契约库路径（回归用）；AIRY_STAGE_BEAT /
# AIRY_STAGE_LIMIT 覆盖心跳周期与预期上限（默认 30s / 60s）。
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

main() {
    local out= rc=0 due=0 line ticker=''

    ui_due "${STAGE}" || due=$?
    case "${due}" in
    0)
        ui_begin "${STAGE}"
        beat &
        ticker=$!
        ;;
    1) : ;;
    *) return "${due}" ;;
    esac

    out=$("$@") || rc=$?

    if [ -n "${ticker}" ]; then
        kill "${ticker}" 2>/dev/null || :
        wait "${ticker}" 2>/dev/null || :
    fi
    if [ -n "${out}" ]; then
        printf '%s\n' "${out}"
    fi

    # 闸在命令之后：先占标记再执行，命令中途崩掉时标记已被消耗，这条
    # 结论就再也不会出现。阶段名已由 ui_due 校验，这里只剩「本次开机
    # 已出过结论」一种拒绝。
    ui_gate "${STAGE}" || return "${rc}"

    line=$(facts "${out%%$'\n'*}" "${KEYS}")
    if [ "${rc}" -eq 0 ]; then
        if [ -n "${line}" ]; then
            ui_ok "${STAGE}" "${line}"
        else
            ui_ok "${STAGE}"
        fi
        return 0
    fi

    if [ -n "${line}" ]; then
        emit "${rc}" "${line}"
    else
        emit "${rc}"
    fi
    return "${rc}"
}

if [ "$#" -lt 4 ]; then
    printf '%s\n' "${USAGE}" >&2
    exit 64
fi
readonly STAGE="$1" NOTES="$2" KEYS="$3"
shift 3

main "$@"
