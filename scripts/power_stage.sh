#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# power_stage.sh — 阶段 6「电源与热治理」的启动入口
#
# airy-power 是 Python 入口，无法 source 打印契约库；本脚本是单元的唯一
# 启动路径：先跑一次 apply，再把工具首行的关键值搬进阶段行。阶段名、序号
# 与列宽仍只登记在 ui.sh 的表里，这里只取值与分档，不复制任何措辞。
#
# 为什么阶段行只在本次开机第一次输出：同一单元由 timer 每 60 秒重跑，
# 而阶段行是「一次开机一条结论」。约束由 ui_gate 统一实现，标记落 tmpfs，
# 重启即清空；周期复核的完整快照照旧逐轮进 journal，只是不再刷屏。
#
# 分档看 airy-power 自己的退出码：0 为落地成功，3 为「已降档但机器仍热」
# 或部分落档——这是机器的真实状态，按 note 上报；1 才是写入失败。
# SuccessExitStatus=3 与这里同源，改一处必须同时改另一处。
#
# 关键值只取工具给出的事实，缺项就不出现：不补默认值，因为「没有这个
# 事实」和「事实是 0」在验收现场是两件不同的事。
#
# 退出码与 airy-power 一致，systemd 沿用同一份成功判据；打印契约库缺失
# 按 6 退出，与其它阶段脚本保持同一约定。
# 环境变量：AIRY_POWER_TOOL 覆盖命令（回归用），AIRY_POWER_UI 覆盖契约库。
# ===========================================================================

set -euo pipefail

readonly UI_LIB="${AIRY_POWER_UI:-${0%/*}/ui.sh}"
if [ ! -r "${UI_LIB}" ]; then
    printf '[电源与热治理] 打印契约库缺失：%s\n' "${UI_LIB}" >&2
    exit 6
fi
# shellcheck source=ui.sh
. "${UI_LIB}"

readonly TOOL="${AIRY_POWER_TOOL:-/usr/bin/airy-power}"
readonly STAGE=电源与热治理
readonly KEYS='landed cap severity charge'

# 首行是工具对外的结论行，键值以空格分隔；只认识这里登记的键，顺序也由
# 这里决定，因此阶段行的列不会随工具内部字段顺序变动而漂移。
facts() {
    awk -v keys="${KEYS}" '
    BEGIN {
        n = split(keys, order, " ")
        for (i = 1; i <= n; i++) want[order[i]] = 1
    }
    NR == 1 {
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
    }' <<<"$1"
}

emit() {
    local rc="$1"
    shift

    ui_gate "${STAGE}" || return 0
    ui_step 6 9 "${STAGE}"
    case "${rc}" in
    0) ui_ok "${STAGE}" "$@" ;;
    3) ui_note "${STAGE}" "$@" ;;
    *) ui_fail "${STAGE}" "$@" "rc=${rc}" ;;
    esac
}

main() {
    local out= rc=0 line

    out=$("${TOOL}" apply "$@") || rc=$?
    if [ -n "${out}" ]; then
        printf '%s\n' "${out}"
    fi
    line=$(facts "${out%%$'\n'*}")
    if [ -n "${line}" ]; then
        emit "${rc}" "${line}"
    else
        emit "${rc}"
    fi
    return "${rc}"
}

main "$@"
