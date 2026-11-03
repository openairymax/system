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
# 关键值只取工具给出的事实，缺项就不出现：不补默认值，因为「没有这个
# 事实」与「事实是 0」在验收现场是两件不同的事；键的顺序由参数决定，
# 因此工具内部字段顺序变化不会让阶段行的列漂移。
#
# 退出码：透传工具；打印契约库缺失按 6（与其它阶段脚本同一约定）；闸
# 自身出错按闸的退出码（阶段名不在表内为 5）。
# 环境变量：AIRY_STAGE_UI 覆盖契约库路径（回归用）。
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

main() {
    local out= rc=0 gate_rc=0 line

    out=$("$@") || rc=$?
    if [ -n "${out}" ]; then
        printf '%s\n' "${out}"
    fi

    # 闸必须在命令之后：先占标记再执行，命令失败时标记已被消耗，下一次
    # 定时复核就再也不会把这条失败报出来。
    ui_gate "${STAGE}" || gate_rc=$?
    case "${gate_rc}" in
    0) ;;
    1) return "${rc}" ;;
    *) return "${gate_rc}" ;;
    esac

    ui_begin "${STAGE}"
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
