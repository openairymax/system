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
		PATTERN "*.timer"
	)
	install(PROGRAMS
		scripts/boot_set.sh
		scripts/firstboot.sh
		scripts/health_check.sh
		DESTINATION lib/airymaxos/scripts
	)
	# 字符设备默认 0600 root:root，非 root 守护需要按属组放权；规则与
	# 单元同包交付，避免出现「单元已装、设备仍不可读」的升级窗口。
	install(FILES udev/60-airymax.rules
		DESTINATION lib/udev/rules.d
	)
endif()

# ---------------------------------------------------------------------------
# 单元静态契约校验：无需 root，不依赖 systemd 运行时
# ---------------------------------------------------------------------------
include(CTest)
find_package(Python3 REQUIRED COMPONENTS Interpreter)

if(BUILD_TESTING)
	add_test(NAME units-lint
		COMMAND ${Python3_EXECUTABLE}
			${CMAKE_CURRENT_SOURCE_DIR}/tools/units_lint.py
			${CMAKE_CURRENT_SOURCE_DIR}/units
	)
	add_test(NAME units-tests
		COMMAND ${Python3_EXECUTABLE} -m unittest discover
			-s ${CMAKE_CURRENT_SOURCE_DIR}/tests/units
			-p "test_*.py"
	)
	add_test(NAME health-script
		COMMAND ${Python3_EXECUTABLE} -m unittest discover
			-s ${CMAKE_CURRENT_SOURCE_DIR}/tests/scripts
			-p "test_*.py"
	)
	add_test(NAME airydisk-model
		COMMAND ${Python3_EXECUTABLE} -m unittest discover
			-s ${CMAKE_CURRENT_SOURCE_DIR}/tests/services
			-p "test_*.py"
	)
endif()

# ---------------------------------------------------------------------------
# 安装规则
# ---------------------------------------------------------------------------
install(TARGETS airymaxmon
	RUNTIME DESTINATION bin
)

# ---------------------------------------------------------------------------
# 磁盘管理服务（M-30/M-31）与升级通道（V-14）：Python 包安装到
# site-packages，由 system/bin 下的入口脚本导入
# ---------------------------------------------------------------------------
install(DIRECTORY services/airydisk services/airyupdate
	DESTINATION ${Python3_SITELIB}
	PATTERN "__pycache__" EXCLUDE
)
# 命令行共用层：退出码、输出与台账落盘原语，安装到 site-packages 顶层，
# 与各服务包同级，导入路径不依赖任何一方。
install(DIRECTORY services/airylib
	DESTINATION ${Python3_SITELIB}
	PATTERN "__pycache__" EXCLUDE
)
# 告警聚合（P-13）：包体进 site-packages，默认规则进 share，入口进 bin。
# 规则与包分开安装，运维可直接在 /etc/agentrt/alert/rules.d 覆盖默认值
# 而不必改动包内文件。
install(DIRECTORY services/daemons/alert_d
	DESTINATION ${Python3_SITELIB}
	PATTERN "__pycache__" EXCLUDE
	PATTERN "rules" EXCLUDE
)
install(FILES services/daemons/alert_d/rules/10-default.json
	DESTINATION share/airymax/alert/rules.d
)
install(PROGRAMS
	bin/airy-disk
	bin/airy-update
	bin/airy-alertd
	bin/airy-alert
	bin/airy-incident
	bin/airy-backup
	bin/airy-agent
	DESTINATION bin
)

# 备份恢复（P-15）：仓库、核验与恢复实现装进 site-packages，入口进 bin。
install(DIRECTORY ops/backup
	DESTINATION ${Python3_SITELIB}
	PATTERN "__pycache__" EXCLUDE
)

# 事件响应（P-14）：台账包进 site-packages，运行手册进 share，入口进 bin。
# 手册与包分开安装，现场可按需替换运行时手册而不动代码。
install(DIRECTORY ops/incident
	DESTINATION ${Python3_SITELIB}
	PATTERN "__pycache__" EXCLUDE
	PATTERN "runbooks" EXCLUDE
)
install(DIRECTORY ops/incident/runbooks
	DESTINATION share/airymax/incident/runbooks
)

# Agent 运维（P-16）：元数据、健康分与调优实现装进 site-packages，入口进 bin。
install(DIRECTORY ops/agent
	DESTINATION ${Python3_SITELIB}
	PATTERN "__pycache__" EXCLUDE
)
