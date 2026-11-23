# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# system/obs/ebpf/CMakeLists.txt — 可编程探针（P-09）独立构建单元
#
# 独立于 system 顶层工程：eBPF 目标需要 clang 与 libbpf 头文件，而 system
# 的常规 C 构建不引入这些依赖。由 ci/gates/ebpf-probe.sh 以出树方式构建：
#   cmake -S system/obs/ebpf -B <临时目录> && cmake --build <临时目录>
#
# 产物：
#   airy-probe        加载器，安装到 bin/
#   airy_probe.bpf.o  探针字节码，安装到 lib/airymaxos/bpf/（OS-STD-OBS-014）
#
# 编译参数按 OS-OBS-015 固定 -O2 -g -target bpf；字节码不含架构相关偏移，
# 四架构共用同一份对象，加载器按本机架构构建。
# ===========================================================================

cmake_minimum_required(VERSION 3.20)

project(airy_obs_ebpf VERSION 0.0.5 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBBPF REQUIRED IMPORTED_TARGET libbpf>=1.0)

find_program(CLANG_EXECUTABLE NAMES clang REQUIRED)

# clang 以 bpf 为目标时不带宿主三元组，默认头文件搜索面里没有 Debian 系
# 发行版的多架构目录，linux/types.h 随之找不到 asm/types.h。这里向宿主 C
# 编译器查询多架构名，只补这一处头文件路径；字节码本身仍不含架构分支。
execute_process(
	COMMAND ${CMAKE_C_COMPILER} -print-multiarch
	OUTPUT_VARIABLE AIRY_MULTIARCH
	OUTPUT_STRIP_TRAILING_WHITESPACE
	ERROR_QUIET)
if(AIRY_MULTIARCH AND EXISTS "/usr/include/${AIRY_MULTIARCH}/asm/types.h")
	list(APPEND AIRY_BPF_CFLAGS "-idirafter" "/usr/include/${AIRY_MULTIARCH}")
endif()

add_executable(airy-probe airy_probe.c)
target_compile_options(airy-probe PRIVATE
	-Wall -Wextra -Werror -D_GNU_SOURCE)
target_link_libraries(airy-probe PRIVATE PkgConfig::LIBBPF)

add_custom_command(
	OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/airy_probe.bpf.o
	COMMAND ${CLANG_EXECUTABLE} -g -O2 -target bpf -c
		${AIRY_BPF_CFLAGS}
		${CMAKE_CURRENT_SOURCE_DIR}/airy_probe.bpf.c
		-o ${CMAKE_CURRENT_BINARY_DIR}/airy_probe.bpf.o
	DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/airy_probe.bpf.c
	COMMENT "编译 eBPF 探针字节码"
	VERBATIM)

add_custom_target(airy-bpf ALL
	DEPENDS ${CMAKE_CURRENT_BINARY_DIR}/airy_probe.bpf.o)

install(TARGETS airy-probe RUNTIME DESTINATION bin)
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/airy_probe.bpf.o
	DESTINATION lib/airymaxos/bpf)
