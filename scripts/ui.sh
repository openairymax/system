#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# ui.sh — 开机九阶段打印契约的唯一实现
#
# 用法（在脚本内 source 本文件）：
#   ui_begin <阶段名>
#   ui_ok   <阶段名> <关键值...>
#   ui_note <阶段名> <事实...>
#   ui_fail <阶段名> <判据...>
#   ui_beat <阶段名> <已用秒> <上限秒>
#   ui_due  <阶段名>
#   ui_gate <阶段名>
#   ui_id   <阶段名>
#   ui_state
#   ui_total
#
# 输出形态（console 与 journal 逐字相同）：
#   [ 3/9 ] 电源与热治理    ok   landed=51/51 cap=100
#   [ 6/9 ] 运行时带宽      note runtime=950000 group=yes
#   [ 8/9 ] 桌面就绪        FAIL lightdm 未 active（mode=gui）
#
# 为什么要有这一份：开机打印此前是各脚本各自起措辞，失败时只有一句自然
# 语言，现场无法回答「卡在哪一步、这一步等多久算卡」。序号与总数把「卡住」
# 变成可判读的事实，ok/note/FAIL/wait 是稳定 token，CI 与现场脚本用同一
# 字符串判定，不看措辞。
#
# 阶段表是契约本体：序号、名称、显示宽度都登记在这里，调用方只报阶段名，
# 序号与总数一律从表里取。总数不由各脚本自报——自报的总数一旦与表不符，
# 「7/8」与「7/9」就会同时出现在同一台机器的日志里。
#
# 阶段名是一个参数：单元以 ExecStart 传名，而 systemd 按空白切分 ExecStart，
# 名字里带空格会被拆成多个参数，运行期查不到序号、以失败退出，现场只剩一行
# FAILED（run 41802417971 的装机首启）。名称因此不含空白，宽度必须等于字符
# 实际宽度（CJK 记 2 列），两条都由 units_lint 校验。
#
# 阶段行是「一次开机一条结论」，而电源、共存、换页上限三个单元由 timer
# 每 60 秒重跑：不加约束时同一阶段每分钟刷一行，屏幕与 journal 都被淹没。
# ui_gate 是这条约束的唯一实现：本次开机第一次返回 0，之后返回 1，调用方
# 以 `ui_gate <阶段名> || return` 跳过整组输出；ui_due 是同一判定的只读版本，
# 供「先判断再执行」的调用方使用。标记落 tmpfs，重启即清空；
# 目录不可写时返回 0 照常输出——宁可重复一行，也不能在受限环境里把结论
# 吞掉。
#
# 标记目录是全局共享的固定路径，不取 RUNTIME_DIRECTORY：该变量是各单元
# 自己的运行时目录，取它会让六个阶段单元各写一份标记，闸门退化成「每单元
# 每开机一次」，与契约的「每阶段每开机一次」不符；更糟的是 airy-power 的
# 运行时目录名是 airy-power，与其余单元不同，同一阶段在两条路径上互不可见。
#
# 固定路径的副作用是收窄了沙箱：ProtectSystem=strict 会把 /run 一并挂成
# 只读，此时目录已存在则 mkdir -p 返回 0、随后的写标记才以 EROFS 失败，
# 闸门被静默跳过，timer 每 60 秒重印一次阶段行（run 42007477579 的
# `/run/airymax-ui/5: Read-only file system`）。因此凡跑 stage_run.sh 且
# 带 ProtectSystem=strict 的单元必须声明 RuntimeDirectory=airymax-ui，
# 由 systemd 保证该路径可写；这一条由 units_lint 断言，不靠记忆。
#
# 退出码：0 允许输出；1 本次开机已输出过；5 契约不符（序号/总数/阶段名
# 不在表内）；64 用法错误。
# ===========================================================================

[[ -n "${_AIRY_UI_SH:-}" ]] && return 0
readonly _AIRY_UI_SH=1

readonly UI_EINVAL=5
readonly UI_EUSAGE=64
# console 目标可覆盖，供回归在临时文件上验证去重；产品路径恒为 /dev/console。
readonly UI_CONSOLE="${UI_CONSOLE:-/dev/console}"

# 通道选择是共享判据：命令行末位是 tty0 时 /dev/console 不指向串口，只写
# /dev/console 会让串口现场一条阶段行都读不到。判据实现在 con.sh。
# shellcheck source-path=SCRIPTDIR source=con.sh
AIRY_CON_UI="${UI_CONSOLE}" . "${BASH_SOURCE[0]%/*}/con.sh"
con_init

# 序号|名称|显示宽度（CJK 每字 2 列）。顺序即执行顺序，新增阶段必须同时
# 改本表与 spec 的 %%check 总数断言，否则判据与实现分叉。
readonly UI_STAGES=(
    '1|基础挂载|8'
    '2|首启判定|8'
    '3|数据分区|8'
    '4|运行时带宽|10'
    '5|换页上限|8'
    '6|电源与热治理|12'
    '7|桌面RT共存|10'
    '8|桌面就绪|8'
    '9|自检与取证|10'
)
readonly UI_COL=16

_ui_err() {
    local code="$1"
    shift

    printf 'ui.sh: %s\n' "$*" >&2
    # 契约错误的现场只有装机屏幕：stderr 默认只进 journal，而报错时屏幕上
    # 本来就没有任何阶段行，运维只剩 systemd 的一行 FAILED（run 41802417971
    # 的装机首启）。诊断必须与它所解释的内容落在同一条通道上。
    #
    # 去重比的是 stderr 的落点，用 -ef 而不是读 /proc/self/fd/2：命令替换
    # 的子 shell 自带捕获管道，readlink 读到的是管道，去重永远不会命中。
    if [ -w "${UI_CONSOLE}" ] && ! [ /dev/stderr -ef "${UI_CONSOLE}" ]; then
        printf 'airymax-boot: %s\n' "$*" >"${UI_CONSOLE}" 2>/dev/null || :
    fi
    return "${code}"
}

# 行同时落 console 与 journal：服务的 stdout 进 journal，装机现场看的是
# console；两条通道都要，缺一条就会在「只能看屏幕」或「只能抓日志」的一侧
# 失明。logger 缺席（chroot、最小 rootfs）时不影响调用方。
#
# 调用方把 stdout 直接接到 console 时（装机现场手工执行、脚本里
# exec >/dev/console），再写一遍会让每行在屏幕上出现两次；去重与串口优先
# 的判据都在 con_sink 里，两个调用方共用同一份实现。
_ui_emit() {
    local line="$1"

    printf '%s\n' "${line}"
    con_sink "${line}"
    if command -v logger >/dev/null 2>&1; then
        logger -t airymax-boot -- "${line}" 2>/dev/null || :
    fi
    return 0
}

# 阶段名 → 序号；不在表内返回 1。宽度取自表，运行期不做字符宽度推断，
# 避免在非 UTF-8 locale 下把宽度算成字节数而整列错位。
_ui_row() {
    local want="$1" row

    for row in "${UI_STAGES[@]}"; do
        if [ "${row#*|}" = "${want}|${row##*|}" ]; then
            printf '%s' "${row}"
            return 0
        fi
    done
    return 1
}

# 组装一行。[ n/N ] 名称 + 空格补到 UI_COL + token + 关键值。
# 只有带关键值时 token 才补到 5 列：没有事实的行不该以空白结尾，
# 否则 journal 里 grep 'ok$' 会匹配不到。
_ui_line() {
    local name="$1" token="$2" row id tok tail=''
    shift 2

    row="$(_ui_row "${name}")" ||
        { _ui_err "${UI_EINVAL}" "阶段名不在契约表: ${name}"; return "${UI_EINVAL}"; }
    id="${row%%|*}"
    tok="${token}"
    if [ "$#" -gt 0 ]; then
        tok="$(printf '%-5s' "${token}")"
        tail="$*"
    fi

    printf '[ %s/%s ] %s%*s%s%s\n' \
        "${id}" "${#UI_STAGES[@]}" "${name}" \
        "$((UI_COL - ${row##*|}))" '' "${tok}" "${tail}"
}

_ui_put() {
    local line rc

    line="$(_ui_line "$@")" || { rc=$?; return "${rc}"; }
    _ui_emit "${line}"
}

ui_total() {
    printf '%s\n' "${#UI_STAGES[@]}"
}

# 阶段行前导。序号与总数只来自契约表，调用方不重复它们：一旦允许自报，
# 同一阶段在不同脚本里就会报出不同的序号。
ui_begin() {
    if [ "$#" -ne 1 ]; then
        _ui_err "${UI_EUSAGE}" "用法: ui_begin <阶段名>"
        return
    fi

    _ui_put "$1" '...'
}

ui_ok() {
    _ui_put "$1" 'ok' "${@:2}"
}

ui_note() {
    _ui_put "$1" 'note' "${@:2}"
}

ui_fail() {
    _ui_put "$1" 'FAIL' "${@:2}"
}

# 长等待的周期心跳：阶段运行器按 AIRY_STAGE_BEAT（默认 30 秒）调用，行里
# 同时给出已用与上限，「还在跑」与「已越界」因此不必靠猜。
ui_beat() {
    _ui_put "$1" 'wait' "used=${2:-0}s limit=${3:-60}s"
}

# 去重标记目录：全局共享的固定路径，理由见文件头。UI_STATE 只供回归把标记
# 落到临时目录，产品路径恒为 /run/airymax-ui。
_ui_dir() {
    printf '%s' "${UI_STATE:-/run/airymax-ui}"
}

# 阶段序号的公开读取口（与闸门标记同名）。需要在同一目录里记本阶段状态的
# 调用方用它，避免自己按契约表推算一遍序号。
ui_id() {
    local row

    row="$(_ui_row "$1")" ||
        { _ui_err "${UI_EINVAL}" "阶段名不在契约表: $1"; return "${UI_EINVAL}"; }
    printf '%s' "${row%%|*}"
}

# 标记目录的公开读取口：阶段运行器要在同一目录里记「上一轮结论」，路径必须
# 与闸门同源，否则抑制与去重会落在两处、互不可见。
ui_state() {
    _ui_dir
}

# 本次开机该阶段是否还没输出过（只读，不占标记）。需要在执行命令之前就知道
# 「这一阶段还会不会出结论」的调用方用它：阶段运行器据此决定要不要起心跳，
# 否则 timer 重跑的每一轮都会冒出一串没有结论的 wait 行。
ui_due() {
    local row

    row="$(_ui_row "$1")" ||
        { _ui_err "${UI_EINVAL}" "阶段名不在契约表: $1"; return "${UI_EINVAL}"; }
    [ -e "$(_ui_dir)/${row%%|*}" ] && return 1
    return 0
}

# 占用输出资格。标记按阶段序号命名，与契约表同源，调用方写错的阶段名在这里
# 就暴露，不必等到 ui_ok 那一行。
ui_gate() {
    local row dir

    ui_due "$1" || return $?
    row="$(_ui_row "$1")"
    dir="$(_ui_dir)"
    mkdir -p "${dir}" 2>/dev/null || return 0
    : >"${dir}/${row%%|*}" 2>/dev/null || return 0
    return 0
}
