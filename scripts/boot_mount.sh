#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# boot_mount.sh — 阶段 1「基础挂载」的判定
#
# 事实来源只有 /proc/self/mountinfo：内核把实际生效的挂载点与只读状态写在
# 里面，/etc/fstab、本地文件系统单元与最终状态都可能不一致，现场要回答
# 「这块盘到底挂没挂上」时只有它可信。
#
# 输出首行三个键，供阶段运行器搬进阶段行：
#   root=rw|ro     根文件系统是否可写
#   boot=mount|dir /boot 是独立挂载，还是根上的空目录
#   efi=mount|dir  /boot/efi 是独立挂载，还是根上的空目录
#
# 为什么 boot 与 efi 要分开报：装机、固件升级与引导修复都往 ESP 写。ESP
# 没挂上时写入会落进根文件系统上的同名目录，装机看着成功、重启后固件里
# 却没有新引导项；只报 root 会把这种「合起来看一切正常」的故障漏掉。
#
# 为什么根为只读只报 note 而不 FAIL：救援与恢复启动会刻意以 ro 挂根，此时
# 系统仍能起来；真正的后果是后续写盘失败，由写盘的那一阶段自己冒出来。
# 在这一阶段把它判死只会让每次救援启动都多一条 incident。
#
# 退出码：0 根可写；3 根为只读（note）；1 读不到挂载表或找不到根挂载点。
# 环境变量：AIRY_MOUNTINFO 覆盖挂载表路径，AIRY_ESP 覆盖 ESP 挂载点，
# 供回归在临时文件上验证；产品路径恒为内核提供的两处默认值。
# ===========================================================================

set -euo pipefail

readonly MOUNTINFO="${AIRY_MOUNTINFO:-/proc/self/mountinfo}"
readonly ESP="${AIRY_ESP:-/boot/efi}"
readonly USAGE='用法: boot_mount.sh'

# 挂载点是八进制转义的：空格 \040、制表 \011、反斜杠 \134。装机脚本生成的
# ESP 路径可能带空格，不还原就会把「已挂载」判成「空目录」。只处理这三个
# 转义——mountinfo 里其余字符按原样出现，多还原反而会把正常路径改坏。
mount_state() {
    awk -v esp="${ESP}" '
    function unesc(s) {
        gsub(/\\040/, " ", s)
        gsub(/\\011/, "\t", s)
        gsub(/\\134/, "\\", s)
        return s
    }
    {
        mp = unesc($5)
        if (mp == "/") {
            root = ($6 ~ /(^|,)ro(,|$)/) ? "ro" : "rw"
        } else if (mp == "/boot") {
            boot = "mount"
        } else if (mp == esp) {
            efi = "mount"
        }
    }
    END {
        printf "root=%s boot=%s efi=%s\n", \
            (root == "" ? "unknown" : root), \
            (boot == "" ? "dir" : boot), \
            (efi == "" ? "dir" : efi)
    }' "${MOUNTINFO}"
}

main() {
    local line

    if [ ! -r "${MOUNTINFO}" ]; then
        printf 'boot_mount: 读不到挂载表：%s\n' "${MOUNTINFO}" >&2
        return 1
    fi

    line=$(mount_state)
    printf '%s\n' "${line}"
    case "${line%% *}" in
    root=rw) return 0 ;;
    root=ro) return 3 ;;
    *) return 1 ;;
    esac
}

main "$@"
