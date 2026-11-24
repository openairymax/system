# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# system/obs/token/CMakeLists.txt — token 计量入口（P-11）独立构建单元
#
# 只依赖内核导出的 [SC] 契约头。源码树的 kernel/include/uapi 不能直接当
# include 根：那里是原样内核头，linux/types.h 会拒绝用户态使用，stddef.h
# 会去找只在内核内部存在的 linux/compiler_types.h。因此由调用方先执行
# headers_install，再把导出根经 -DAIRY_HDR_ROOT 传进来：
#   make -C kernel O=<obj> headers_install INSTALL_HDR_PATH=<hdr>
#   cmake -S system/obs/token -B <tmp> -DAIRY_HDR_ROOT=<hdr>/include
#
# 产物：airy-token，安装到 bin/。运行期依赖 /proc/airy 与 /dev/airy_token。
# ===========================================================================

cmake_minimum_required(VERSION 3.20)

project(airy_obs_token VERSION 0.0.5 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

if(NOT DEFINED AIRY_HDR_ROOT)
	message(FATAL_ERROR
		"AIRY_HDR_ROOT 未设置：先执行 kernel headers_install")
endif()

if(NOT EXISTS "${AIRY_HDR_ROOT}/linux/airymax/token.h")
	message(FATAL_ERROR "导出树缺少 linux/airymax/token.h")
endif()

add_executable(airy-token airy_token.c)
target_compile_options(airy-token PRIVATE
	-Wall -Wextra -Werror -D_GNU_SOURCE)
target_include_directories(airy-token PRIVATE ${AIRY_HDR_ROOT})

install(TARGETS airy-token RUNTIME DESTINATION bin)
