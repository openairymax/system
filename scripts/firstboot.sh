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
        printf '[首启] machine-id 已存在\n'
        return 0
    fi
    if "$MIDSETUP" --root="$ROOT" >/dev/null 2>&1 && valid_mid; then
        printf '[首启] machine-id 已由 systemd-machine-id-setup 生成\n'
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
    printf '[首启] machine-id 已由随机 UUID 生成\n'
}

apply_sysctl()
{
    if ! "$SYSCTL" --system --quiet; then
        printf '[首启] sysctl 参数应用失败\n' >&2
        return 2
    fi
    printf '[首启] sysctl 参数已应用\n'
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
        printf '[首启] Airy LSM 已就绪\n'
        return 0
    fi
    "$MODPROBE" airy_lsm >/dev/null 2>&1 || true
    if read_probe; then
        printf '[首启] Airy LSM 模块已加载\n'
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
    printf '[首启] agentrt.target 已加入启动队列\n'
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

main()
{
    if [ -e "$MARKER" ]; then
        printf '[首启] 已完成，跳过\n'
        return 0
    fi
    printf '[首启] 开始\n'
    ensure_mid || return $?
    apply_sysctl || return $?
    load_lsm || return $?
    start_stack || return $?
    mark_done || return 5
    printf '[首启] 完成\n'
    return 0
}

main "$@"
