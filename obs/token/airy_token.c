// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy_token.c — AirymaxOS token 计量读取与预算设置入口
 *
 * 职责：
 *   把 /proc/airy/agents/<id>/token_stats 的快照与 /dev/airy_token 的
 *   ioctl 收成一个可编排入口。文本视图与内核逐字一致；--json 供 TUI
 *   面板与自动归集消费。
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

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <linux/airymax/token.h>

#define PT_NAME		"airy-token"
#define PT_ROOT		"/proc/airy"
#define PT_DEV		"/dev/airy_token"
#define PT_PATH		512
#define PT_BUF		8192
#define PT_ROWS		1024
#define PT_ARGS		2
#define PT_MISS		3
#define PT_DEVFAIL	4

struct snap {
	unsigned long long agent_id;
	unsigned long long report_time_ns;
	unsigned long long input;
	unsigned long long output;
	unsigned long long cache;
	unsigned long long reasoning;
	unsigned long long total;
	unsigned long long billable;
	unsigned long long window;
	unsigned long long win_start;
	unsigned long long win_secs;
	unsigned long long budget;
	unsigned long long used;
	unsigned long long budget_ratio;
	unsigned long long budget_secs;
	unsigned long long cache_ratio;
	unsigned long long oi_ratio;
	unsigned long long overhead;
	unsigned long long updated;
	char alert[16];
};

struct key_off {
	const char *key;
	size_t off;
};

static const struct key_off SNAP_KEYS[] = {
	{ "agent_id", offsetof(struct snap, agent_id) },
	{ "report_time_ns", offsetof(struct snap, report_time_ns) },
	{ "input_tokens_total", offsetof(struct snap, input) },
	{ "output_tokens_total", offsetof(struct snap, output) },
	{ "cache_hit_tokens_total", offsetof(struct snap, cache) },
	{ "reasoning_tokens_total", offsetof(struct snap, reasoning) },
	{ "total_tokens_total", offsetof(struct snap, total) },
	{ "billable_tokens_total", offsetof(struct snap, billable) },
	{ "window_tokens_total", offsetof(struct snap, window) },
	{ "window_start_ns", offsetof(struct snap, win_start) },
	{ "window_duration_s", offsetof(struct snap, win_secs) },
	{ "budget_tokens", offsetof(struct snap, budget) },
	{ "budget_used", offsetof(struct snap, used) },
	{ "budget_ratio_ppm", offsetof(struct snap, budget_ratio) },
	{ "budget_period_s", offsetof(struct snap, budget_secs) },
	{ "cache_hit_ratio_ppm", offsetof(struct snap, cache_ratio) },
	{ "output_input_ratio_ppm", offsetof(struct snap, oi_ratio) },
	{ "reasoning_overhead_ppm", offsetof(struct snap, overhead) },
	{ "updated_ns", offsetof(struct snap, updated) },
};

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

static int parse_u64(const char *s, unsigned long long *out)
{
	char *end;
	unsigned long long v;

	if (!s || *s < '0' || *s > '9')
		return -1;

	errno = 0;
	v = strtoull(s, &end, 10);
	if (errno || *end)
		return -1;

	*out = v;
	return 0;
}

static int parse_id(const char *s, unsigned *out)
{
	unsigned long long v;

	if (parse_u64(s, &v) || v > 0xffffffffULL)
		return -1;

	*out = (unsigned)v;
	return 0;
}

static const char *find_key(const char *buf, const char *key)
{
	size_t n = strlen(key);
	const char *p = buf;

	while (p && *p) {
		if (!strncmp(p, key, n) && p[n] == ':' && p[n + 1] == ' ')
			return p + n + 2;
		p = strchr(p, '\n');
		if (p)
			p++;
	}

	return NULL;
}

static int get_u64(const char *buf, const char *key,
		   unsigned long long *out)
{
	const char *p = find_key(buf, key);
	char *end;

	if (!p)
		return -1;

	errno = 0;
	*out = strtoull(p, &end, 10);
	if (end == p || errno || (*end != '\n' && *end != '\0'))
		return -1;

	return 0;
}

static int get_str(const char *buf, const char *key, char *out, size_t len)
{
	const char *p = find_key(buf, key);
	size_t n = 0;

	if (!p)
		return -1;

	while (p[n] && p[n] != '\n' && n + 1 < len) {
		out[n] = p[n];
		n++;
	}
	out[n] = '\0';

	return n ? 0 : -1;
}

static int path_snap(char *out, size_t len, const char *root, unsigned id)
{
	int n = snprintf(out, len, "%s/agents/%u/token_stats", root, id);

	return n < 0 || (size_t)n >= len ? -1 : 0;
}

static int read_all(const char *path, char *buf, size_t len)
{
	FILE *f = fopen(path, "r");
	size_t n;

	if (!f)
		return -1;

	n = fread(buf, 1, len - 1, f);
	if (ferror(f)) {
		fclose(f);
		return -1;
	}
	buf[n] = '\0';
	fclose(f);

	return 0;
}

static int load_snap(const char *root, unsigned id, struct snap *s)
{
	char path[PT_PATH];
	char buf[PT_BUF];
	size_t i;

	if (path_snap(path, sizeof(path), root, id) || read_all(path, buf,
							       sizeof(buf)))
		return -1;

	memset(s, 0, sizeof(*s));
	for (i = 0; i < sizeof(SNAP_KEYS) / sizeof(SNAP_KEYS[0]); i++) {
		unsigned long long *slot =
			(unsigned long long *)((char *)s + SNAP_KEYS[i].off);

		if (get_u64(buf, SNAP_KEYS[i].key, slot))
			return -1;
	}

	return get_str(buf, "alert_state", s->alert, sizeof(s->alert));
}

static void snap_json(const struct snap *s)
{
	printf("{\"agent_id\":%llu,\"alert_state\":\"%s\","
	       "\"report_time_ns\":%llu,\"updated_ns\":%llu,",
	       s->agent_id, s->alert, s->report_time_ns, s->updated);
	printf("\"input_tokens_total\":%llu,\"output_tokens_total\":%llu,"
	       "\"cache_hit_tokens_total\":%llu,\"reasoning_tokens_total\":%llu,",
	       s->input, s->output, s->cache, s->reasoning);
	printf("\"total_tokens_total\":%llu,\"billable_tokens_total\":%llu,",
	       s->total, s->billable);
	printf("\"window_tokens_total\":%llu,\"window_start_ns\":%llu,"
	       "\"window_duration_s\":%llu,",
	       s->window, s->win_start, s->win_secs);
	printf("\"budget_tokens\":%llu,\"budget_used\":%llu,"
	       "\"budget_ratio_ppm\":%llu,\"budget_period_s\":%llu,",
	       s->budget, s->used, s->budget_ratio, s->budget_secs);
	printf("\"cache_hit_ratio_ppm\":%llu,\"output_input_ratio_ppm\":%llu,"
	       "\"reasoning_overhead_ppm\":%llu}\n",
	       s->cache_ratio, s->oi_ratio, s->overhead);
}

static int cmd_show(const char *root, int argc, char **argv)
{
	struct snap s;
	unsigned id;
	char path[PT_PATH];
	char buf[PT_BUF];
	int json = 0;

	if (argc < 1 || argc > 2)
		return bad_args();
	if (argc == 2) {
		if (strcmp(argv[1], "--json"))
			return bad_args();
		json = 1;
	}
	if (parse_id(argv[0], &id))
		return bad_args();

	if (json) {
		if (load_snap(root, id, &s)) {
			fprintf(stderr, PT_NAME ": agent %u: no snapshot\n",
				id);
			return PT_MISS;
		}
		snap_json(&s);
		return 0;
	}

	if (path_snap(path, sizeof(path), root, id) ||
	    read_all(path, buf, sizeof(buf))) {
		fprintf(stderr, PT_NAME ": agent %u: no snapshot\n", id);
		return PT_MISS;
	}

	fputs(buf, stdout);
	return 0;
}

struct row {
	unsigned id;
	unsigned long long total;
};

static int row_cmp(const void *a, const void *b)
{
	const struct row *x = a;
	const struct row *y = b;

	if (x->total != y->total)
		return x->total < y->total ? 1 : -1;

	return x->id < y->id ? -1 : (x->id > y->id);
}

static int row_one(const char *root, const char *name, struct row *r)
{
	char path[PT_PATH];
	char buf[PT_BUF];
	unsigned id;

	if (parse_id(name, &id) || id == 0)
		return -1;
	if (path_snap(path, sizeof(path), root, id) ||
	    read_all(path, buf, sizeof(buf)))
		return -1;

	r->id = id;
	return get_u64(buf, "total_tokens_total", &r->total);
}

static int scan_rows(const char *root, struct row *rows, unsigned max)
{
	char dir[PT_PATH];
	struct dirent *e;
	DIR *d;
	unsigned n = 0;
	int len;

	len = snprintf(dir, sizeof(dir), "%s/agents", root);
	if (len < 0 || (size_t)len >= sizeof(dir))
		return -1;

	d = opendir(dir);
	if (!d)
		return -1;

	while ((e = readdir(d)) && n < max) {
		if (row_one(root, e->d_name, &rows[n]) == 0)
			n++;
	}
	closedir(d);

	return (int)n;
}

static int cmd_top(const char *root, int argc, char **argv)
{
	struct row rows[PT_ROWS];
	unsigned limit = 10;
	int json = 0;
	int n;
	int i;

	for (i = 0; i < argc; i++) {
		if (!strcmp(argv[i], "--json")) {
			json = 1;
		} else if (!strcmp(argv[i], "-n") && i + 1 < argc) {
			unsigned long long v;

			if (parse_u64(argv[++i], &v) || v > PT_ROWS)
				return bad_args();
			limit = (unsigned)v;
		} else {
			return bad_args();
		}
	}

	n = scan_rows(root, rows, PT_ROWS);
	if (n < 0) {
		fprintf(stderr, PT_NAME ": %s/agents: %s\n", root,
			strerror(errno));
		return PT_MISS;
	}

	qsort(rows, (size_t)n, sizeof(rows[0]), row_cmp);
	if ((unsigned)n > limit)
		n = (int)limit;

	if (json)
		putchar('[');
	else
		printf("AGENT\tTOTAL_TOKENS\n");

	for (i = 0; i < n; i++) {
		if (json)
			printf("%s{\"agent_id\":%u,\"total_tokens\":%llu}",
			       i ? "," : "", rows[i].id, rows[i].total);
		else
			printf("%u\t%llu\n", rows[i].id, rows[i].total);
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
	if (parse_id(argv[0], &id) || parse_u64(argv[1], &tokens) || !tokens)
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
	if (parse_id(argv[0], &id) ||
	    parse_u64(argv[1], &in) || parse_u64(argv[2], &out) ||
	    parse_u64(argv[3], &cache) || parse_u64(argv[4], &reasoning))
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
