// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy_token.c — AirymaxOS token 计量读取与预算设置入口
 *
 * 职责：
 *   把 /proc/airy/agents/<id>/token_stats 的快照与 /dev/airy_token 的
 *   ioctl 收成一个可编排入口。文本视图与内核逐字一致；--json 由公共
 *   解析层按内核顺序渲染，工具的字段集合因此不必再抄一份。
 *
 * 边界：
 *   本工具不持久化历史、不做趋势分析（属 logger_d），也不改变内核
 *   口径：比例全部按 ppm 原样透出，档位文本由内核给出。
 *
 * 命令行：
 *   airy-token [--root DIR] [--dev PATH] show <agent> [--json]
 *   airy-token [--root DIR] top [-n N] [--json]
 *   airy-token [--dev PATH] budget <agent> <tokens>
 *   airy-token [--dev PATH] report <agent> <input> <output> <cache> <reasoning>
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

#include <linux/airymax/token.h>

#include "obs_view.h"

#define PT_NAME		"airy-token"
#define PT_ROOT		"/proc/airy"
#define PT_DEV		"/dev/airy_token"
#define PT_SUB		"agents"
#define PT_FILE		"token_stats"
#define PT_ROWS		1024
#define PT_ARGS		2
#define PT_MISS		3
#define PT_DEVFAIL	4

static int usage(FILE *out)
{
	fprintf(out,
		"usage: " PT_NAME " [--root DIR] [--dev PATH] <command> ...\n"
		"  show <agent> [--json]\n"
		"  top [-n N] [--json]\n"
		"  budget <agent> <tokens>\n"
		"  report <agent> <input> <output> <cache> <reasoning>\n");
	return out == stdout ? 0 : PT_ARGS;
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
		if (obs_cat(root, PT_SUB, id, PT_FILE)) {
			fprintf(stderr, PT_NAME ": agent %u: no snapshot\n",
				id);
			return PT_MISS;
		}
		return 0;
	}

	n = obs_load(root, PT_SUB, id, PT_FILE, kv, OBS_KV_MAX);
	if (n < 0) {
		fprintf(stderr, PT_NAME ": agent %u: no snapshot\n", id);
		return PT_MISS;
	}
	obs_json(kv, (unsigned)n);

	return 0;
}

static int cmd_top(const char *root, int argc, char **argv)
{
	struct obs_row rows[PT_ROWS];
	unsigned limit = 10;
	int json = 0;
	int n;
	int i;

	for (i = 0; i < argc; i++) {
		if (!strcmp(argv[i], "--json")) {
			json = 1;
		} else if (!strcmp(argv[i], "-n") && i + 1 < argc) {
			unsigned long long v;

			if (obs_num(argv[++i], &v) || v > PT_ROWS)
				return bad_args();
			limit = (unsigned)v;
		} else {
			return bad_args();
		}
	}

	n = obs_scan(root, PT_SUB, PT_FILE, "total_tokens_total", rows,
		     PT_ROWS);
	if (n < 0) {
		fprintf(stderr, PT_NAME ": %s/%s: %s\n", root, PT_SUB,
			strerror(errno));
		return PT_MISS;
	}

	qsort(rows, (size_t)n, sizeof(rows[0]), obs_row_cmp);
	if ((unsigned)n > limit)
		n = (int)limit;

	if (json)
		putchar('[');
	else
		printf("AGENT\tTOTAL_TOKENS\n");

	for (i = 0; i < n; i++) {
		if (json)
			printf("%s{\"agent_id\":%u,\"total_tokens\":%llu}",
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
		fprintf(stderr, PT_NAME ": %s: %s\n", dev, strerror(errno));

	return fd;
}

static int cmd_budget(const char *dev, int argc, char **argv)
{
	struct airy_token_budget b = { 0 };
	unsigned long long tokens;
	unsigned id;
	int fd;

	if (argc != 2)
		return bad_args();
	if (obs_id(argv[0], &id) || obs_num(argv[1], &tokens) || !tokens)
		return bad_args();

	b.agent_id = id;
	b.tokens = tokens;

	fd = dev_open(dev);
	if (fd < 0)
		return PT_DEVFAIL;

	if (ioctl(fd, AIRY_TOKEN_IOC_BUDGET, &b)) {
		fprintf(stderr, PT_NAME ": budget %u: %s\n", id,
			strerror(errno));
		close(fd);
		return PT_DEVFAIL;
	}
	close(fd);

	return 0;
}

static int cmd_report(const char *dev, int argc, char **argv)
{
	struct airy_token_delta d = { 0 };
	unsigned long long in, out, cache, reasoning;
	unsigned id;
	int fd;

	if (argc != 5)
		return bad_args();
	if (obs_id(argv[0], &id) ||
	    obs_num(argv[1], &in) || obs_num(argv[2], &out) ||
	    obs_num(argv[3], &cache) || obs_num(argv[4], &reasoning))
		return bad_args();
	if (in > AIRY_TOKEN_DELTA_MAX || out > AIRY_TOKEN_DELTA_MAX ||
	    cache > AIRY_TOKEN_DELTA_MAX || reasoning > AIRY_TOKEN_DELTA_MAX)
		return bad_args();

	d.agent_id = id;
	d.input = (unsigned)in;
	d.output = (unsigned)out;
	d.cache_hit = (unsigned)cache;
	d.reasoning = (unsigned)reasoning;

	fd = dev_open(dev);
	if (fd < 0)
		return PT_DEVFAIL;

	if (ioctl(fd, AIRY_TOKEN_IOC_REPORT, &d)) {
		fprintf(stderr, PT_NAME ": report %u: %s\n", id,
			strerror(errno));
		close(fd);
		return PT_DEVFAIL;
	}
	close(fd);

	return 0;
}

int main(int argc, char **argv)
{
	const char *root = PT_ROOT;
	const char *dev = PT_DEV;
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
	if (!strcmp(cmd, "budget"))
		return cmd_budget(dev, argc - i, argv + i);
	if (!strcmp(cmd, "report"))
		return cmd_report(dev, argc - i, argv + i);

	return bad_args();
}
