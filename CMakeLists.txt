# ===========================================================================
# agent-linux (AirymaxOS) system submodule CMakeLists.txt
# 构建入口：airymaxmon + metrics + tracing + logging + health + perf
# 决策 F1：最小可编译骨架（非桩）
# ===========================================================================

cmake_minimum_required(VERSION 3.20)

project(airy_system
	VERSION 0.1.1
	LANGUAGES C
	DESCRIPTION "AirymaxOS system submodule — observability + monitoring")

# ---------------------------------------------------------------------------
# C 标准：C11（用户态 daemon）
# ---------------------------------------------------------------------------
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

# ---------------------------------------------------------------------------
# [SC] 头文件引用（OS-IRON-014 / 决策 G1）
# 唯一物理宿主：kernel/include/uapi/linux/airymax/
# 禁止物理副本；以 -idirafter 追加检索路径，避免 in-tree uapi 的
# linux/types.h、linux/time.h 遮蔽 glibc 同名头文件
# ---------------------------------------------------------------------------
add_compile_options(-idirafter ${CMAKE_SOURCE_DIR}/../kernel/include/uapi)

# ---------------------------------------------------------------------------
# 编译选项（OS-STD-FMT-001/002）
# ---------------------------------------------------------------------------
add_compile_options(
	-Wall
	-Wextra
	-Werror
	-Wno-unused-parameter
	-fstack-protector-strong
	-D_FORTIFY_SOURCE=2
)

# ---------------------------------------------------------------------------
# 子目录：airymaxmon 监控守护进程
# 0.1.1 仅 airymaxmon 可编译，其余 1.0.1 补齐
# ---------------------------------------------------------------------------
add_subdirectory(monitoring)

# ---------------------------------------------------------------------------
# systemd 单元（M-18）
# 包级路径固定为 /usr/lib/systemd/system
# ---------------------------------------------------------------------------
option(AIRY_INSTALL_SYSTEMD_UNITS
	"Install AirymaxOS systemd units" ON)

if(AIRY_INSTALL_SYSTEMD_UNITS)
	install(DIRECTORY units/
		DESTINATION lib/systemd/system
		FILES_MATCHING
		PATTERN "*.service"
		PATTERN "*.target"
	)
endif()

# ---------------------------------------------------------------------------
# 单元静态契约校验：无需 root，不依赖 systemd 运行时
# ---------------------------------------------------------------------------
include(CTest)
find_package(Python3 COMPONENTS Interpreter)

if(BUILD_TESTING AND Python3_Interpreter_FOUND)
	add_test(NAME units-lint
		COMMAND ${Python3_EXECUTABLE}
			${CMAKE_CURRENT_SOURCE_DIR}/tools/units_lint.py
			${CMAKE_CURRENT_SOURCE_DIR}/units
	)
endif()

# ---------------------------------------------------------------------------
# 安装规则
# ---------------------------------------------------------------------------
install(TARGETS airymaxmon
	RUNTIME DESTINATION bin
)
