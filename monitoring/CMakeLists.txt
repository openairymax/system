# ===========================================================================
# airymaxmon CMakeLists.txt
# AirymaxOS 实时监控守护进程（[IND]）
# 决策 F1：真实可编译实现（非桩）
# ===========================================================================

# 可执行目标 airymaxmon
add_executable(airymaxmon
	airymaxmon.c
)

# [SC] 头文件引用已由顶层 CMakeLists.txt 通过 include_directories 配置

# 链接库
target_link_libraries(airymaxmon
	PRIVATE
		rt
)

# 编译定义
target_compile_definitions(airymaxmon
	PRIVATE
		_GNU_SOURCE
)

# 安装
install(TARGETS airymaxmon
	RUNTIME DESTINATION bin
)
