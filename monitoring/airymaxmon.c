// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airymaxmon.c — AirymaxOS 实时监控守护进程
 *
 * 职责：
 *   每秒读取 /proc/airy/status，解析关键字段（agent_count / fault_count /
 *   last_heartbeat / sched_budget_ns），结合 [SC] error.h 的故障码定义
 *   与 [SC] lsm_types.h 的 Agent 安全上下文，输出结构化监控信息到 stdout。
 *
 * 信号处理：
 *   SIGINT / SIGTERM — 优雅退出（设置 stop_flag，主循环检测后退出）
 *
 * 决策 F1：真实可编译实现（非桩，非空 main）。
 *
 * [IND] 完全独立层（agent-linux 专属实现）。
 *
 * 相关文档：
 *   docs/AirymaxOS/10-architecture/07-directory-structure.md §4.7
 *   kernel/include/uapi/linux/airymax/error.h
 *   kernel/include/uapi/linux/airymax/lsm_types.h
 */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* [SC] 共享契约头文件（通过 -I../kernel/include/uapi/linux 引用） */
#include <airymax/error.h>
#include <airymax/lsm_types.h>

/* ─── 常量 ──────────────────────────────────────────────────────────── */

#define AIRYMAXMON_NAME		"airymaxmon"
#define AIRYMAXMON_VERSION	"0.1.1"
#define AIRYMAXMON_INTERVAL_S	1
#define AIRYMAXMON_STATUS_PATH	"/proc/airy/status"
#define AIRYMAXMON_BUF		4096

/* ─── 全局状态 ──────────────────────────────────────────────────────── */

static volatile sig_atomic_t g_stop_flag = 0;

/* ─── 信号处理 ──────────────────────────────────────────────────────── */

static void airymaxmon_signal_handler(int signum)
{
	(void)signum;
	g_stop_flag = 1;
}

static int airymaxmon_install_signal_handlers(void)
{
	struct sigaction sa;

	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = airymaxmon_signal_handler;
	sigemptyset(&sa.sa_mask);
	sa.sa_flags = 0;

	if (sigaction(SIGINT, &sa, NULL) < 0) {
		perror(AIRYMAXMON_NAME ": sigaction(SIGINT)");
		return -1;
	}
	if (sigaction(SIGTERM, &sa, NULL) < 0) {
		perror(AIRYMAXMON_NAME ": sigaction(SIGTERM)");
		return -1;
	}

	/* 忽略 SIGPIPE（写入已关闭的管道时不中断） */
	sa.sa_handler = SIG_IGN;
	if (sigaction(SIGPIPE, &sa, NULL) < 0) {
		perror(AIRYMAXMON_NAME ": sigaction(SIGPIPE)");
		return -1;
	}

	return 0;
}

/* ─── 状态文件读取 ──────────────────────────────────────────────────── */

/*
 * airymaxmon_read_status - 读取 /proc/airy/status 全部内容
 *
 * @buf:    输出缓冲区
 * @bufsz:  缓冲区大小
 *
 * 返回值：
 *   ≥ 0 - 实际读取的字节数
 *   -1  - 失败（文件不存在或读取错误）
 */
static ssize_t airymaxmon_read_status(char *buf, size_t bufsz)
{
	int fd;
	ssize_t n;

	fd = open(AIRYMAXMON_STATUS_PATH, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return -1;

	n = read(fd, buf, bufsz - 1);
	close(fd);

	if (n < 0)
		return -1;

	buf[n] = '\0';
	return n;
}

/*
 * airymaxmon_parse_field - 从状态文本中解析 key: value 字段
 *
 * @status: 状态文本
 * @key:    字段名（如 "agent_count"）
 *
 * 返回值：
 *   ≥ 0 - 字段值（long）
 *   -1  - 字段不存在
 */
static long airymaxmon_parse_field(const char *status, const char *key)
{
	const char *p;
	size_t keylen = strlen(key);

	p = status;
	while (p && *p) {
		if (strncmp(p, key, keylen) == 0 && p[keylen] == ':') {
			const char *v = p + keylen + 1;
			while (*v == ' ' || *v == '\t')
				v++;
			return strtol(v, NULL, 10);
		}
		p = strchr(p, '\n');
		if (p)
			p++;
	}
	return -1;
}

/*
 * airymaxmon_classify_health - 根据 agent_count / fault_count 评估健康度
 *
 * @agent_count: 当前 Agent 数
 * @fault_count: 累计故障数
 *
 * 返回值：健康等级字符串
 */
static const char *airymaxmon_classify_health(long agent_count,
					      long fault_count)
{
	if (agent_count < 0)
		return "UNKNOWN";
	if (fault_count <= 0)
		return "HEALTHY";
	if (fault_count < AIRY_CAP_MAX_AGENTS / 4)
		return "DEGRADED";
	return "CRITICAL";
}

/* ─── 主循环 ────────────────────────────────────────────────────────── */

static int airymaxmon_main_loop(void)
{
	unsigned long iter = 0;
	char buf[AIRYMAXMON_BUF];

	fprintf(stdout,
		AIRYMAXMON_NAME " v" AIRYMAXMON_VERSION
		" 启动 — 间隔=%ds 状态文件=%s\n",
		AIRYMAXMON_INTERVAL_S, AIRYMAXMON_STATUS_PATH);
	fprintf(stdout, "AIRY_CAP_MAX_AGENTS=%d\n\n", AIRY_CAP_MAX_AGENTS);
	fflush(stdout);

	while (!g_stop_flag) {
		struct timespec ts;
		ssize_t n;
		long agent_count = -1;
		long fault_count = -1;
		long last_heartbeat = -1;
		long sched_budget_ns = -1;
		const char *health;

		clock_gettime(CLOCK_REALTIME, &ts);
		iter++;

		n = airymaxmon_read_status(buf, sizeof(buf));
		if (n < 0) {
			fprintf(stderr,
				"[%lld.%09ld] " AIRYMAXMON_NAME
				": 读取 %s 失败: %s\n",
				(long long)ts.tv_sec, ts.tv_nsec,
				AIRYMAXMON_STATUS_PATH, strerror(errno));
		} else {
			agent_count = airymaxmon_parse_field(buf,
							     "agent_count");
			fault_count = airymaxmon_parse_field(buf,
							     "fault_count");
			last_heartbeat = airymaxmon_parse_field(buf,
							   "last_heartbeat");
			sched_budget_ns = airymaxmon_parse_field(buf,
							 "sched_budget_ns");

			health = airymaxmon_classify_health(agent_count,
							    fault_count);

			fprintf(stdout,
				"[%lld.%09ld] iter=%lu health=%s "
				"agents=%ld faults=%ld heartbeat=%ld "
				"budget_ns=%ld\n",
				(long long)ts.tv_sec, ts.tv_nsec,
				iter, health, agent_count, fault_count,
				last_heartbeat, sched_budget_ns);

			if (fault_count > 0) {
				fprintf(stdout,
					"  ↳ 故障参考: CAP_FORGED=0x%x "
					"CAP_LEAK=0x%x RING_CORRUPT=0x%x "
					"TIMEOUT=0x%x\n",
					AIRY_FAULT_CAP_FORGED,
					AIRY_FAULT_CAP_LEAK,
					AIRY_FAULT_RING_CORRUPT,
					AIRY_FAULT_TIMEOUT);
			}
		}

		fflush(stdout);

		/* sleep 1 秒（可被信号打断） */
		sleep(AIRYMAXMON_INTERVAL_S);
	}

	fprintf(stdout, "\n" AIRYMAXMON_NAME ": 收到退出信号，终止监控 "
		"(iter=%lu)\n", iter);
	return 0;
}

/* ─── 使用说明 ──────────────────────────────────────────────────────── */

static void airymaxmon_usage(FILE *out, const char *prog)
{
	fprintf(out,
		"用法: %s [选项]\n"
		"\n"
		"AirymaxOS 实时监控守护进程。\n"
		"\n"
		"每秒读取 /proc/airy/status 并打印结构化监控信息。\n"
		"按 Ctrl+C 或发送 SIGTERM 优雅退出。\n"
		"\n"
		"选项:\n"
		"  -h, --help      显示帮助\n"
		"  -v, --version   显示版本\n"
		"\n"
		"示例:\n"
		"  %s                # 启动监控\n"
		"  %s -v             # 显示版本\n",
		prog, prog, prog);
}

/* ─── main 入口 ─────────────────────────────────────────────────────── */

int main(int argc, char **argv)
{
	const char *prog = argv[0] ? argv[0] : AIRYMAXMON_NAME;

	/* 简单参数解析（-h / -v） */
	for (int i = 1; i < argc; i++) {
		if (strcmp(argv[i], "-h") == 0 ||
		    strcmp(argv[i], "--help") == 0) {
			airymaxmon_usage(stdout, prog);
			return EXIT_SUCCESS;
		}
		if (strcmp(argv[i], "-v") == 0 ||
		    strcmp(argv[i], "--version") == 0) {
			printf(AIRYMAXMON_NAME " v" AIRYMAXMON_VERSION
			       " — AirymaxOS monitoring daemon\n");
			return EXIT_SUCCESS;
		}
	}

	/* 安装信号处理 */
	if (airymaxmon_install_signal_handlers() < 0)
		return EXIT_FAILURE;

	/* 进入主循环 */
	return airymaxmon_main_loop() == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
