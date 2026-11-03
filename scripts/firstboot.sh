#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# firstboot.sh — AirymaxOS 首启初始化
#
# 依据 docs-linux/100-operations/01-deployment.md §7.1，在 default.target
# 就绪前完成不可绕过的初始化关卡：
#   1. 生成并校验 /etc/machine-id；
#   2. 应用 /etc/sysctl.d 中的内核参数；
#   3. 确认 Airy LSM 与 capability 子系统就绪；
#   4. 触发 agentrt.target 拉起 12 个 daemon；
#   5. 写入完成标记，后续启动立即返回。
#
# 对外只打一行：本脚本是九阶段契约第 2 阶段「首启判定」的唯一实现，
# 每台机器每次启动都出现该行（已完成时是 done=1 的 note），屏幕与
# journal 由 ui.sh 同步落盘。分步结果落在同一行的键值里，不另起措辞。
#
# 幂等语义：完成标记存在即视为已完成，重复执行不产生副作用。
# capability 令牌由内核 airy_cap_derive.c 在 LSM 内部派生，无用户态
# 加载文件，因此第 3 步以 /proc/sys/agentrt/present 作为就绪判据
# （OS-IFACE-007）。DevStation 注册与 airymaxos-config-check 在 0.0.3
# 尚无协议与二进制交付，按契约边界显式推迟，不以桩实现冒充完成。
#
# 退出码：
#   0 初始化完成或此前已完成
#   1 machine-id 生成或校验失败
#   2 sysctl 参数应用失败
#   3 Airy LSM 未就绪（ENOTSUP）
#   4 agentrt.target 无法拉起
#   5 完成标记写入失败
#   6 打印契约库 ui.sh 缺失
#
# 环境变量：
#   AIRY_FIRSTBOOT_ROOT       根前缀，默认 /（离线镜像校验用）
#   AIRY_FIRSTBOOT_SYSCTL     sysctl 命令，默认 sysctl
#   AIRY_FIRSTBOOT_MODPROBE   modprobe 命令，默认 modprobe
#   AIRY_FIRSTBOOT_SYSTEMCTL  systemctl 命令，默认 systemctl
#   AIRY_FIRSTBOOT_MIDSETUP   systemd-machine-id-setup 命令
#   AIRY_FIRSTBOOT_UUID       UUID 源文件，默认 /proc/sys/kernel/random/uuid
# ===========================================================================

set -euo pipefail

readonly UI_LIB="${AIRY_FIRSTBOOT_UI:-${0%/*}/ui.sh}"
if [ ! -r "${UI_LIB}" ]; then
    printf '[首启] 打印契约库缺失：%s\n' "${UI_LIB}" >&2
    exit 6
fi
# shellcheck source=ui.sh
. "${UI_LIB}"

readonly BASE="${AIRY_FIRSTBOOT_ROOT:-/}"
readonly ROOT="${BASE%/}"
readonly SYSCTL="${AIRY_FIRSTBOOT_SYSCTL:-sysctl}"
readonly MODPROBE="${AIRY_FIRSTBOOT_MODPROBE:-modprobe}"
readonly SYSTEMCTL="${AIRY_FIRSTBOOT_SYSTEMCTL:-systemctl}"
readonly MIDSETUP="${AIRY_FIRSTBOOT_MIDSETUP:-systemd-machine-id-setup}"
readonly UUID="${AIRY_FIRSTBOOT_UUID:-${ROOT}/proc/sys/kernel/random/uuid}"
readonly STATE="${ROOT}/var/lib/airymaxos"
readonly MARKER="${STATE}/.firstboot-done"
readonly MID="${ROOT}/etc/machine-id"
readonly PROBE="${ROOT}/proc/sys/agentrt/present"
readonly MID_RE='^[0-9a-f]{32}$'

MID_SRC=""
LSM_SRC=""

valid_mid()
{
    local id
    [ -f "$MID" ] || return 1
    read -r id <"$MID" || return 1
    [[ $id =~ $MID_RE ]]
}

write_mid()
{
    local id="$1" tmp="${MID}.tmp.$$"
    if ! printf '%s\n' "$id" >"$tmp"; then
        rm -f "$tmp"
        return 1
    fi
    if ! chmod 0444 "$tmp" || ! mv -f "$tmp" "$MID"; then
        rm -f "$tmp"
        return 1
    fi
}

ensure_mid()
{
    local raw id=""
    if valid_mid; then
        MID_SRC="present"
        return 0
    fi
    if "$MIDSETUP" --root="$ROOT" >/dev/null 2>&1 && valid_mid; then
        MID_SRC="setup"
        return 0
    fi
    if [ ! -r "$UUID" ]; then
        printf '[首启] machine-id 生成失败：%s 不可读\n' "$UUID" >&2
        return 1
    fi
    read -r raw <"$UUID" || return 1
    id="${raw//-/}"
    if [[ ! $id =~ $MID_RE ]] || ! write_mid "$id"; then
        printf '[首启] machine-id 生成失败：%s 不是有效 UUID\n' "$UUID" >&2
        return 1
    fi
    MID_SRC="uuid"
}

apply_sysctl()
{
    if ! "$SYSCTL" --system --quiet; then
        printf '[首启] sysctl 参数应用失败\n' >&2
        return 2
    fi
}

read_probe()
{
    local raw=""
    [ -r "$PROBE" ] || return 1
    read -r raw <"$PROBE" || return 1
    [ "$raw" = "1" ]
}

load_lsm()
{
    if read_probe; then
        LSM_SRC="present"
        return 0
    fi
    "$MODPROBE" airy_lsm >/dev/null 2>&1 || true
    if read_probe; then
        LSM_SRC="modprobe"
        return 0
    fi
    printf '[首启] Airy LSM 未就绪：%s 不为 1（ENOTSUP）\n' "$PROBE" >&2
    return 3
}

start_stack()
{
    if ! "$SYSTEMCTL" start --no-block agentrt.target; then
        printf '[首启] agentrt.target 拉起失败\n' >&2
        return 4
    fi
}

mark_done()
{
    local tmp="${MARKER}.tmp.$$"
    mkdir -p "$STATE" || return 5
    if ! printf 'firstboot %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$tmp"; then
        rm -f "$tmp"
        return 5
    fi
    if ! mv -f "$tmp" "$MARKER"; then
        rm -f "$tmp"
        return 5
    fi
}

# 每一步失败都把「哪一步、退出码」写进同一行，屏幕停住时现场不必再读
# journal 才能归因。成功的键值只填真实分支的结果，不写固定文案。
#
# 失败分支用 `cmd || { rc=$?; ... }`：`if ! cmd` 的 $? 是取反后的状态，
# 会把 1 记成 0，归因行里的 rc 就不是命令真实退出码。
fail_step()
{
    ui_fail 首启判定 "step=$1 rc=$2"
}

main()
{
    local rc=0

    if [ -e "$MARKER" ]; then
        ui_note 首启判定 done=1
        return 0
    fi
    ui_begin 首启判定
    ensure_mid || { rc=$?; fail_step machine-id "${rc}"; return "${rc}"; }
    apply_sysctl || { rc=$?; fail_step sysctl "${rc}"; return "${rc}"; }
    load_lsm || { rc=$?; fail_step lsm "${rc}"; return "${rc}"; }
    start_stack || { rc=$?; fail_step target "${rc}"; return "${rc}"; }
    mark_done || { fail_step mark 5; return 5; }
    ui_ok 首启判定 "mid=${MID_SRC} sysctl=ok lsm=${LSM_SRC} target=queued"
    return 0
}

main "$@"
