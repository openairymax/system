// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy_mem.c — AirymaxOS 记忆计量读取与配额设置入口
 *
 * 职责：
 *   把 /proc/airy/agents/<id>/memory_stats 的快照与 /dev/airy_mem 的
 *   ioctl 收成一个可编排入口。文本视图与内核逐字一致；--json 由公共
 *   解析层按内核顺序渲染，工具的字段集合因此不必再抄一份。
 *
 * 边界：
 *   本工具不持久化历史、不做趋势分析（属 logger_d），也不改变内核
 *   口径：比例全部按 ppm 原样透出，档位文本由内核给出。四层记忆的
 *   语义见 docs-linux/90-observability/09-memory-monitoring.md。
 *
 * 命令行：
 *   airy-mem [--root DIR] show <agent> [--json]
 *   airy-mem [--root DIR] top [-n N] [--json]
 *   airy-mem [--dev PATH] quota <agent> <quota_kb>
 *   airy-mem [--dev PATH] gauge <agent> <level> <used_kb>
 *   airy-mem [--dev PATH] report <agent> <level> <access> <hit> <lat_sum> \
 *            <lat_max> [evict fill page_in page_out read write add drop]
 *
 * 退出码：0 成功；2 参数不合法；3 快照缺失或不完整；4 设备或 ioctl 失败。
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <linux/airymax/mem.h>

#include "obs_view.h"

#define MM_NAME		"airy-mem"
#define MM_ROOT		"/proc/airy"
#define MM_DEV		"/dev/airy_mem"
#define MM_SUB		"agents"
#define MM_FILE		"memory_stats"
#define MM_ROWS		1024
#define MM_ARGS		2
#define MM_MISS		3
#define MM_DEVFAIL	4
#define MM_FIELDS	12

static int usage(FILE *out)
{
	fprintf(out,
		"usage: " MM_NAME " [--root DIR] [--dev PATH] <command> ...\n"
		"  show <agent> [--json]\n"
		"  top [-n N] [--json]\n"
		"  quota <agent> <quota_kb>\n"
		"  gauge <agent> <level> <used_kb>\n"
		"  report <agent> <level> <access> <hit> <lat_sum> <lat_max>"
		" [evict fill page_in page_out read write add drop]\n");
	return out == stdout ? 0 : MM_ARGS;
}

static int bad_args(void)
{
	return usage(stderr);
}

static int cmd_show(const char *root, int argc, char **argv)
{
	struct obs_kv kv[OBS_KV_MAX];
	unsigned id;
	int json = 0;
	int n;

	if (argc < 1 || argc > 2)
		return bad_args();
	if (argc == 2) {
		if (strcmp(argv[1], "--json"))
			return bad_args();
		json = 1;
	}
	if (obs_id(argv[0], &id))
		return bad_args();

	if (!json) {
		if (obs_cat(root, MM_SUB, id, MM_FILE)) {
			fprintf(stderr, MM_NAME ": agent %u: no snapshot\n",
				id);
			return MM_MISS;
		}
		return 0;
	}

	n = obs_load(root, MM_SUB, id, MM_FILE, kv, OBS_KV_MAX);
	if (n < 0) {
		fprintf(stderr, MM_NAME ": agent %u: no snapshot\n", id);
		return MM_MISS;
	}
	obs_json(kv, (unsigned)n);

	return 0;
}

static int cmd_top(const char *root, int argc, char **argv)
{
	struct obs_row rows[MM_ROWS];
	unsigned limit = 10;
	int json = 0;
	int n;
	int i;

	for (i = 0; i < argc; i++) {
		if (!strcmp(argv[i], "--json")) {
			json = 1;
		} else if (!strcmp(argv[i], "-n") && i + 1 < argc) {
			unsigned long long v;

			if (obs_num(argv[++i], &v) || v > MM_ROWS)
				return bad_args();
			limit = (unsigned)v;
		} else {
			return bad_args();
		}
	}

	n = obs_scan(root, MM_SUB, MM_FILE, "used_kb", rows, MM_ROWS);
	if (n < 0) {
		fprintf(stderr, MM_NAME ": %s/%s: %s\n", root, MM_SUB,
			strerror(errno));
		return MM_MISS;
	}

	qsort(rows, (size_t)n, sizeof(rows[0]), obs_row_cmp);
	if ((unsigned)n > limit)
		n = (int)limit;

	if (json)
		putchar('[');
	else
		printf("AGENT\tUSED_KB\n");

	for (i = 0; i < n; i++) {
		if (json)
			printf("%s{\"agent_id\":%u,\"used_kb\":%llu}",
			       i ? "," : "", rows[i].id, rows[i].val);
		else
			printf("%u\t%llu\n", rows[i].id, rows[i].val);
	}

	if (json)
		printf("]\n");

	return 0;
}

static int dev_open(const char *dev)
{
	int fd = open(dev, O_RDWR | O_CLOEXEC);

	if (fd < 0)
		fprintf(stderr, MM_NAME ": %s: %s\n", dev, strerror(errno));

	return fd;
}

static int lvl_ok(unsigned long long level)
{
	return level >= AIRY_MEM_L1 && level <= AIRY_MEM_LEVELS;
}

static int cmd_quota(const char *dev, int argc, char **argv)
{
	struct airy_mem_quota q = { 0 };
	unsigned long long kb;
	unsigned id;
	int fd;

	if (argc != 2)
		return bad_args();
	if (obs_id(argv[0], &id) || obs_num(argv[1], &kb) ||
	    kb > AIRY_MEM_DELTA_MAX)
		return bad_args();

	q.agent_id = id;
	q.quota_kb = kb;

	fd = dev_open(dev);
	if (fd < 0)
		return MM_DEVFAIL;

	if (ioctl(fd, AIRY_MEM_IOC_QUOTA, &q)) {
		fprintf(stderr, MM_NAME ": quota %u: %s\n", id,
			strerror(errno));
		close(fd);
		return MM_DEVFAIL;
	}
	close(fd);

	return 0;
}

static int cmd_gauge(const char *dev, int argc, char **argv)
{
	struct airy_mem_gauge g = { 0 };
	unsigned long long level, kb;
	unsigned id;
	int fd;

	if (argc != 3)
		return bad_args();
	if (obs_id(argv[0], &id) || obs_num(argv[1], &level) ||
	    obs_num(argv[2], &kb) || !lvl_ok(level) ||
	    kb > AIRY_MEM_DELTA_MAX)
		return bad_args();

	g.agent_id = id;
	g.level = (unsigned char)level;
	g.used_kb = kb;

	fd = dev_open(dev);
	if (fd < 0)
		return MM_DEVFAIL;

	if (ioctl(fd, AIRY_MEM_IOC_GAUGE, &g)) {
		fprintf(stderr, MM_NAME ": gauge %u: %s\n", id,
			strerror(errno));
		close(fd);
		return MM_DEVFAIL;
	}
	close(fd);

	return 0;
}

static int cmd_report(const char *dev, int argc, char **argv)
{
	struct airy_mem_delta d = { 0 };
	unsigned long long v[MM_FIELDS] = { 0 };
	unsigned long long level;
	unsigned id;
	int fd;
	int i;

	if (argc < 6 || argc > MM_FIELDS + 2)
		return bad_args();
	if (obs_id(argv[0], &id) || obs_num(argv[1], &level) || !lvl_ok(level))
		return bad_args();

	for (i = 0; i < argc - 2; i++) {
		if (obs_num(argv[i + 2], &v[i]) ||
		    v[i] > AIRY_MEM_DELTA_MAX)
			return bad_args();
	}

	d.agent_id = id;
	d.level = (unsigned char)level;
	d.access = v[0];
	d.hit = v[1];
	d.lat_sum_ns = v[2];
	d.lat_max_ns = v[3];
	d.evict = v[4];
	d.fill_ns = v[5];
	d.page_in = v[6];
	d.page_out = v[7];
	d.read_bytes = v[8];
	d.write_bytes = v[9];
	d.pat_add = v[10];
	d.pat_drop = v[11];

	fd = dev_open(dev);
	if (fd < 0)
		return MM_DEVFAIL;

	if (ioctl(fd, AIRY_MEM_IOC_REPORT, &d)) {
		fprintf(stderr, MM_NAME ": report %u: %s\n", id,
			strerror(errno));
		close(fd);
		return MM_DEVFAIL;
	}
	close(fd);

	return 0;
}

int main(int argc, char **argv)
{
	const char *root = MM_ROOT;
	const char *dev = MM_DEV;
	int i = 1;
	const char *cmd;

	while (i < argc && argv[i][0] == '-' && argv[i][1] == '-') {
		if (!strcmp(argv[i], "--root") && i + 1 < argc) {
			root = argv[++i];
		} else if (!strcmp(argv[i], "--dev") && i + 1 < argc) {
			dev = argv[++i];
		} else if (!strcmp(argv[i], "--help")) {
			return usage(stdout);
		} else {
			return bad_args();
		}
		i++;
	}

	if (i >= argc)
		return bad_args();

	cmd = argv[i++];
	if (!strcmp(cmd, "show"))
		return cmd_show(root, argc - i, argv + i);
	if (!strcmp(cmd, "top"))
		return cmd_top(root, argc - i, argv + i);
	if (!strcmp(cmd, "quota"))
		return cmd_quota(dev, argc - i, argv + i);
	if (!strcmp(cmd, "gauge"))
		return cmd_gauge(dev, argc - i, argv + i);
	if (!strcmp(cmd, "report"))
		return cmd_report(dev, argc - i, argv + i);

	return bad_args();
}
