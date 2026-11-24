# SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
# ===========================================================================
# system/obs/perf/CMakeLists.txt — perf 采样入口（P-10）独立构建单元
#
# 独立于 system 顶层工程：本工具只是上游 perf 的编排前端，不引入新的库依赖，
# 也不需要随 monitoring 一起构建。由 ci/gates/perf-hooks.sh 出树构建：
#   cmake -S system/obs/perf -B <临时目录> && cmake --build <临时目录>
#
# 产物：airy-perf，安装到 bin/。运行期依赖 /usr/bin/perf（linux-tools）。
# ===========================================================================

cmake_minimum_required(VERSION 3.20)

project(airy_obs_perf VERSION 0.0.5 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

add_executable(airy-perf airy_perf.c)
target_compile_options(airy-perf PRIVATE
	-Wall -Wextra -Werror -D_GNU_SOURCE)

install(TARGETS airy-perf RUNTIME DESTINATION bin)
