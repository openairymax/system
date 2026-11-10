#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# ===========================================================================
# ukui_compat.sh — 补齐 ukui 上游服务声明与二进制落点之间的差
#
# 背景：ukui-interface 的 D-Bus 服务文件把 Exec 写成
# /usr/libexec/ukui-*server，同一批 RPM 却把二进制装在 /usr/bin。D-Bus 按
# Exec 的绝对路径直接 execve，路径不存在时激活立即失败，症状是控制中心的
# 每个模块（触控板、鼠标、键盘、电源、字体、屏保、桌面外观……）打开都没有
# 内容，而后端本体其实完好——只看进程表会以为「服务没跑」，看不到真实原因。
#
# 修法不是改上游文件，也不是硬编码一份 12 个文件名的清单：清单从「本次装机
# 实际存在的 /usr/bin/ukui-*server」推导，只对缺失的入口建链接。上游增删模块
# 或改名时本脚本自动跟随，不会出现第二份需要同步维护的文件名列表。
#
# 用法：ukui_compat.sh [根目录]。根目录参数供门禁在临时树上验证；生产安装由
# %posttrans 无参调用。可重复执行：已存在的路径一律不动，也不覆盖管理员自己
# 放下的同名文件。
#
# 退出码：0 无事可做或全部补齐；1 建链失败（目录不可写等）。
# ===========================================================================
set -u

root="${1:-}"
bindir="${root}/usr/bin"
libdir="${root}/usr/libexec"
made=0
kept=0

for path in "$bindir"/ukui-*server; do
    if [ ! -x "$path" ]; then
        continue
    fi
    name="${path##*/}"
    if [ -e "${libdir}/${name}" ] || [ -L "${libdir}/${name}" ]; then
        kept=$((kept + 1))
        continue
    fi
    install -d -m 0755 "$libdir" || exit 1
    ln -s "/usr/bin/${name}" "${libdir}/${name}" || exit 1
    made=$((made + 1))
done

if [ "$made" -eq 0 ]; then
    exit 0
fi
printf 'ukui-compat: linked %d ukui server entries into /usr/libexec (kept %d)\n' \
    "$made" "$kept"
