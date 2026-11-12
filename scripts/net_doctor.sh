#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# ===========================================================================
# net_doctor.sh - 首启网络自检的一句话结论
#
# 为什么要有：B50 与 285H 首次烧录后都停在「装完没网」。现场只能靠猜：
# 网卡没枚举、固件没进镜像、网线没插、扫不到 SSID、DHCP 没回来，这五种
# 原因在桌面上长得一模一样。本脚本只在开机后判一次，把结论写成一行固定
# 字段，落到 /run/airymaxos/net.state 并打进 journal，串口和装机日志里
# 直接可读。
#
# 判据只认运行期事实，不猜：
#   no-device    /sys/class/net 里没有 wlan*/eth*（wl*/en* 是同一类设备的
#                可预测命名，一并认），且没有任何网络设备绑定到驱动。
#                总线侧证据取 PCI class=0x02、USB bInterfaceClass=02 和
#                platform/amba 的设备名；DT 平台取 /sys/firmware/
#                devicetree/base 下的 compatible。
#   no-firmware  内核日志有 "Direct firmware load for <X> failed"，且同一个
#                文件没有后续 direct-loading 成功行。-11 那类回退请求会由
#                udev 重新加载，不能算缺固件。
#   no-carrier   设备在、carrier=0。无线未关联时 carrier 本来就是 0，若把
#                这一情形也算 no-carrier，no-ssid 永远不可达；所以已满足
#                no-ssid 判据（phy 在、rfkill 未阻塞、wpa_supplicant 在、
#                扫描为空）的无线设备留给 no-ssid，其余 carrier=0 的设备
#                落在这里，rfkill 软/硬阻塞计数进 hint。
#   no-ssid      有 phy、有 wpa_supplicant、扫描结果为空。
#   dhcp-timeout carrier=1，但没有非 link-local 的 IPv4 地址。
#   ok           有设备拿到地址；有网也写一行，state=ok 是允许的补充值。
#
# 输出格式（字段恒定、顺序恒定，未知用 -）：
#   AIRYMAX-NET state=<state> dev=<ifname> driver=<mod> \
#       fw=<ok|missing:path|-> rfkill=<n> hint=<...>
#
# 退出码：0 = ok；3 = 上述失败态；1 = 内部错误（sysfs 缺失或状态文件写
# 不进去）。单元用 ExecStart=- 忽略 3：判定失败不能让 systemd 再记一条
# failed，掩盖真正的失败者。
#
# 正常启动不设下面几个变量；它们只用于测试注入可控的伪造根，产品路径上
# 没有任何分支：AIRY_SYSFS_ROOT、AIRY_DMESG_FILE、AIRY_RUN_DIR、
# AIRY_PROC_ROOT。
# ===========================================================================
set -euo pipefail

readonly SYS="${AIRY_SYSFS_ROOT:-/sys}"
readonly PROC="${AIRY_PROC_ROOT:-/proc}"
readonly RUN="${AIRY_RUN_DIR:-/run/airymaxos}"
readonly STATE="${RUN}/net.state"
readonly DMESG="${AIRY_DMESG_FILE:-}"
readonly FW_DIR=/usr/lib/firmware

STATE_NAME=no-device
DEV=-
DRIVER=-
FW=-
RFKILL=0
RF_SOFT=0
RF_HARD=0
HINT=-
KLOG=
TMP=
SCAN_CNT=0
SCAN_ERR=none

klog() {
    if [ -n "${DMESG}" ]; then
        cat "${DMESG}"
        return 0
    fi
    if command -v dmesg >/dev/null 2>&1; then
        dmesg 2>/dev/null && return 0
    fi
    if command -v journalctl >/dev/null 2>&1; then
        journalctl -k -b -o cat 2>/dev/null || :
    fi
}

san() {
    printf '%s' "${1}" | tr ' \t' '__' | tr -d '\n' | cut -c1-96
}

base() {
    printf '%s' "${1##*/}"
}

list_devs() {
    local d
    for d in "${SYS}"/class/net/*; do
        if [ ! -e "${d}" ]; then
            continue
        fi
        d=$(base "${d}")
        case "${d}" in
            wlan*|eth*|wl*|en*) printf '%s\n' "${d}" ;;
        esac
    done | LC_ALL=C sort -u
}

drv() {
    local p="${SYS}/class/net/$1/device/driver" r
    if [ ! -e "${p}" ]; then
        printf '%s' -
        return 0
    fi
    r=$(readlink -f "${p}" 2>/dev/null) || r=${p}
    base "${r}"
}

car() {
    local c
    c=$(cat "${SYS}/class/net/$1/carrier" 2>/dev/null) || c=-
    case "${c}" in
        0|1) printf '%s' "${c}" ;;
        *) printf '%s' - ;;
    esac
}

is_wl() {
    if [ -e "${SYS}/class/net/$1/phy80211" ]; then
        return 0
    fi
    case "${1}" in
        wl*) return 0 ;;
    esac
    return 1
}

nm_run() {
    if ! command -v nmcli >/dev/null 2>&1; then
        return 1
    fi
    [ "$(nmcli -t -f RUNNING general 2>/dev/null)" = running ]
}

ip4() {
    local a=-
    if nm_run; then
        a=$(nmcli -t -f IP4.ADDRESS device show "${1}" 2>/dev/null |
            sed -n 's/^IP4.ADDRESS\[[0-9]*\]:\([^/]*\).*/\1/p' |
            head -1) || a=-
    elif command -v ip >/dev/null 2>&1; then
        a=$(ip -4 -o addr show dev "${1}" 2>/dev/null |
            awk '$3 == "inet" {split($4, p, "/"); print p[1]; exit}') || a=-
    fi
    case "${a}" in
        ''|169.254.*|127.*) printf '%s' - ;;
        *) printf '%s' "${a}" ;;
    esac
}

nm_state() {
    local v=down
    if nm_run; then
        v=$(nmcli -t -f GENERAL.STATE device show "${1}" 2>/dev/null |
            sed -n '1s/^GENERAL.STATE://p') || v=down
        v=${v:-down}
    fi
    printf '%s' "${v%% *}"
}

boot_sec() {
    local s=0
    if [ -r "${PROC}/uptime" ]; then
        s=$(sed -n '1s/\..*//p' "${PROC}/uptime") || s=0
    fi
    printf '%s' "${s:-0}"
}

supp_up() {
    local d
    for d in "${PROC}"/[0-9]*; do
        if [ ! -r "${d}/comm" ]; then
            continue
        fi
        if [ "$(cat "${d}/comm" 2>/dev/null)" = wpa_supplicant ]; then
            return 0
        fi
    done
    return 1
}

rf_scan() {
    local f s h
    for f in "${SYS}"/class/rfkill/*; do
        if [ ! -r "${f}/soft" ]; then
            continue
        fi
        s=$(cat "${f}/soft" 2>/dev/null) || s=0
        h=$(cat "${f}/hard" 2>/dev/null) || h=0
        if [ "${s}" = 1 ]; then
            RF_SOFT=$((RF_SOFT + 1))
        fi
        if [ "${h}" = 1 ]; then
            RF_HARD=$((RF_HARD + 1))
        fi
        if [ "${s}" = 1 ] || [ "${h}" = 1 ]; then
            RFKILL=$((RFKILL + 1))
        fi
    done
}

scan_one() {
    local out
    SCAN_CNT=0
    SCAN_ERR=none
    if command -v iw >/dev/null 2>&1 && [ -n "${TMP}" ]; then
        out=$(iw dev "${1}" scan 2>"${TMP}/scan.err") || out=
        SCAN_CNT=$(printf '%s\n' "${out}" | grep -c '^BSS' || :)
        SCAN_ERR=$(head -1 "${TMP}/scan.err" | tr -s ' \t' '_') || :
        SCAN_ERR=${SCAN_ERR:-none}
    elif nm_run; then
        out=$(nmcli -t -f SSID device wifi list 2>/dev/null) || out=
        SCAN_CNT=$(printf '%s\n' "${out}" | sed '/^$/d' | sort -u | wc -l)
    else
        SCAN_ERR=no-tool
    fi
}

ssid_rdy() {
    is_wl "${1}" || return 1
    [ "${RFKILL}" -eq 0 ] || return 1
    supp_up || return 1
    scan_one "${1}"
    [ "${SCAN_CNT}" -eq 0 ] || return 1
    [ "${SCAN_ERR}" = none ] || return 1
    return 0
}

fw_fail() {
    local line file
    if [ -z "${KLOG}" ]; then
        return 1
    fi
    while IFS= read -r line; do
        case "${line}" in
            *"Direct firmware load for "*" failed"*) ;;
            *) continue ;;
        esac
        if [ "${1}" != - ]; then
            case "${line}" in
                *"${1}"*) ;;
                *) continue ;;
            esac
        fi
        file=${line##*Direct firmware load for }
        file=${file%% failed*}
        if [ -z "${file}" ]; then
            continue
        fi
        if printf '%s\n' "${KLOG}" |
                grep -qF "direct-loading firmware ${file}"; then
            continue
        fi
        printf '%s' "${file}"
        return 0
    done <<< "${KLOG}"
    return 1
}

bus_net() {
    local p s c
    for p in "${SYS}"/bus/*/devices/*; do
        if [ ! -d "${p}" ]; then
            continue
        fi
        s=${p#*/bus/}
        s=${s%%/*}
        if [ -r "${p}/class" ]; then
            c=$(cat "${p}/class" 2>/dev/null) || c=
            case "${c}" in
                0x02*) ;;
                *) continue ;;
            esac
        elif [ -r "${p}/bInterfaceClass" ]; then
            c=$(cat "${p}/bInterfaceClass" 2>/dev/null) || c=
            case "${c}" in
                02|0x02) ;;
                *) continue ;;
            esac
        else
            case "${s}:${p##*/}" in
                platform:*eth*|platform:*wlan*|platform:*wifi*) ;;
                platform:*mdio*|platform:*dwmac*|platform:*stmmac*) ;;
                amba:*eth*|amba:*wlan*|amba:*wifi*|amba:*mdio*) ;;
                soc:*eth*|soc:*wlan*|soc:*wifi*) ;;
                *) continue ;;
            esac
        fi
        if [ -e "${p}/driver" ]; then
            printf 'bound %s/%s\n' "${s}" "${p##*/}"
        else
            printf 'unbound %s/%s\n' "${s}" "${p##*/}"
        fi
    done
}

first_bound() {
    local line
    while IFS= read -r line; do
        case "${line}" in
            bound\ *) printf '%s' "${line#bound }"; return 0 ;;
        esac
    done < <(bus_net)
    return 1
}

unbound_hint() {
    local line n=0 out=
    while IFS= read -r line; do
        case "${line}" in
            unbound\ *) ;;
            *) continue ;;
        esac
        if [ "${n}" -ge 2 ]; then
            break
        fi
        out="${out}${out:+,}${line#unbound }"
        n=$((n + 1))
    done < <(bus_net)
    printf '%s' "${out}"
}

bus_drv() {
    local p="${SYS}/bus/$1/driver" r
    if [ ! -e "${p}" ]; then
        printf '%s' -
        return 0
    fi
    r=$(readlink -f "${p}" 2>/dev/null) || r=${p}
    base "${r}"
}

dt_net() {
    local d="${SYS}/firmware/devicetree/base" hit=
    if [ ! -d "${d}" ]; then
        printf '%s' -
        return 0
    fi
    hit=$(grep -ral -E 'ethernet|wifi|wlan' --include=compatible "${d}" \
        2>/dev/null | head -2 | sed "s#^${d}/##") || hit=
    printf '%s' "${hit:--}"
}

focus() {
    DEV=${1}
    DRIVER=$(drv "${1}")
    FW=-
}

no_dev() {
    local b drv file
    if b=$(first_bound); then
        drv=$(bus_drv "${b}")
        if file=$(fw_fail "${drv}"); then
            STATE_NAME=no-firmware
            DRIVER=${drv}
            FW="missing:${file}"
            HINT="path=${FW_DIR}/${file}"
            return 0
        fi
        STATE_NAME=no-device
        HINT="netdev=0,bound=${b},driver=${drv}"
        return 0
    fi
    STATE_NAME=no-device
    HINT="netdev=0,unbound=$(unbound_hint),dt=$(dt_net)"
}

# fw_pass DEV... —— 内核日志点名的缺固件设备，命中即出结论。
#
# 这一遍必须走完全部设备：能拿到地址的未必是驱动真正要用的那台，而
# "Direct firmware load for X failed" 是内核给出的唯一直接证据。
fw_pass() {
    local d file
    for d in "$@"; do
        if file=$(fw_fail "$(drv "${d}")"); then
            focus "${d}"
            STATE_NAME=no-firmware
            FW="missing:${file}"
            HINT="path=${FW_DIR}/${file},dev=${d}"
            return 0
        fi
    done
    return 1
}

# rank_dev CAR SSID CNT ERR DHCP —— 没拿到地址时的降级链。
#
# 次序是判据的一部分，不能调换：无线未关联时 carrier 本来就是 0，先判
# no-carrier 会让 no-ssid 永远不可达（见文件头）。前四档取第一个非空，
# 全空时给「载波不成立」的兜底结论。
rank_dev() {
    if [ -n "${1}" ]; then
        focus "${1}"
        STATE_NAME=no-carrier
        HINT="carrier=0,soft=${RF_SOFT},hard=${RF_HARD}"
        return 0
    fi
    if [ -n "${2}" ]; then
        focus "${2}"
        STATE_NAME=no-ssid
        HINT="scans=1,ap=${3},err=${4}"
        return 0
    fi
    if [ -n "${5}" ]; then
        focus "${5}"
        STATE_NAME=dhcp-timeout
        HINT="wait=$(boot_sec),nmcli=$(nm_state "${5}")"
        return 0
    fi
    STATE_NAME=no-carrier
    HINT="carrier=-,soft=${RF_SOFT},hard=${RF_HARD}"
}

judge() {
    local -a devs=()
    local d c a car_dev='' ssid_dev='' dhcp_dev=''
    local ssid_cnt=0 ssid_err=none

    rf_scan
    mapfile -t devs < <(list_devs)
    if [ "${#devs[@]}" -eq 0 ]; then
        no_dev
        return 0
    fi

    # 一台台归类，边归边认领候选。拿到地址即出结论，不再看后面的设备：
    # 现场要的是「哪台能用」，不是全部设备的明细。
    for d in "${devs[@]}"; do
        c=$(car "${d}")
        if [ "${c}" = 1 ]; then
            a=$(ip4 "${d}")
            if [ "${a}" != - ]; then
                focus "${d}"
                STATE_NAME=ok
                HINT="addr=${a}"
                return 0
            fi
            dhcp_dev=${dhcp_dev:-${d}}
            continue
        fi
        if ssid_rdy "${d}"; then
            if [ -z "${ssid_dev}" ]; then
                ssid_dev=${d}
                ssid_cnt=${SCAN_CNT}
                ssid_err=${SCAN_ERR}
            fi
            continue
        fi
        car_dev=${car_dev:-${d}}
    done

    if fw_pass "${devs[@]}"; then
        return 0
    fi
    rank_dev "${car_dev}" "${ssid_dev}" "${ssid_cnt}" "${ssid_err}" \
        "${dhcp_dev}"
}

emit() {
    local line
    HINT=$(san "${HINT}")
    line="AIRYMAX-NET state=${STATE_NAME} dev=${DEV} driver=${DRIVER}"
    line="${line} fw=${FW} rfkill=${RFKILL} hint=${HINT}"
    printf '%s\n' "${line}"
    mkdir -p "${RUN}"
    chmod 0755 "${RUN}"
    printf '%s\n' "${line}" > "${STATE}"
    chmod 0644 "${STATE}"
}

main() {
    if [ ! -d "${SYS}/class/net" ]; then
        STATE_NAME=no-device
        HINT="sysfs=absent"
        emit || :
        return 1
    fi
    KLOG=$(klog) || KLOG=
    TMP=$(mktemp -d "${TMPDIR:-/tmp}/airy-net.XXXXXX")
    trap 'rm -rf "${TMP}"' EXIT
    judge
    emit
    case "${STATE_NAME}" in
        ok) return 0 ;;
        *) return 3 ;;
    esac
}

main "$@"
