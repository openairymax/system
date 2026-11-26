// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy_probe.c — AirymaxOS eBPF 探针加载器
 *
 * 职责：
 *   打开 airy_probe.bpf.o，经 libbpf 加载并把十个程序挂到真实内核点：
 *   四个边界（能力闸门、551 相位通知、550 调度控制、548 执行分发）的
 *   入口/返回各一对，以及 airy_policy_decision / airy_superv_enforce
 *   两个追踪事件。ringbuf 事件逐条输出为 JSON 行，退出前汇总 per-CPU
 *   计数器。
 *
 * 失败处理：
 *   libbpf 的打开、加载与验证器输出统一经打印回调落到
 *   /var/log/airymaxos/bpf-load.log；该目录不可写时退回 stderr，不静默丢弃。
 *
 * 资源释放：
 *   每个 bpf_link 显式 destroy，ringbuf 显式 free，bpf_object 显式 close；
 *   SIGINT/SIGTERM 与到时退出走同一释放路径，不留悬挂 link。
 *
 * 命令行：
 *   airy-probe [--object PATH] [--seconds N]
 *   --seconds 为 0 时持续运行到收到信号。
 *
 * 相关文档：
 *   docs/docs-linux/90-observability/02-ebpf-probes.md
 *   system/obs/ebpf/airy_probe.bpf.c
 */

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#define AIRY_BPF_OBJ	"/usr/lib/airymaxos/bpf/airy_probe.bpf.o"
#define AIRY_BPF_LOG	"/var/log/airymaxos/bpf-load.log"
#define AIRY_BPF_DIR	"/var/log/airymaxos"

#define AIRY_LINK_NR	10
#define AIRY_STAT_NR	10

/* 与 airy_probe.bpf.c 的 struct airy_evt 逐字段一致的镜像。 */
struct airy_evt {
	uint64_t ts;
	uint64_t pid;
	uint32_t kind;
	union {
		struct {
			uint32_t hook;
			uint32_t table;
			uint32_t verdict;
			uint32_t fault;
		} pol;
		struct {
			uint32_t agent;
			uint32_t action;
			uint32_t reason;
			uint32_t pad;
		} enf;
	};
};

static volatile sig_atomic_t g_stop;
static int g_log = -1;

static void on_sig(int sig)
{
	(void)sig;
	g_stop = 1;
}

static void mk_log(void)
{
	if (mkdir(AIRY_BPF_DIR, 0750) < 0 && errno != EEXIST)
		return;
	g_log = open(AIRY_BPF_LOG, O_WRONLY | O_CREAT | O_APPEND, 0640);
}

/* libbpf 打印回调：验证器日志与装载诊断都从这里进日志文件。 */
static int log_cb(enum libbpf_print_level level, const char *fmt, va_list ap)
{
	(void)level;
	if (g_log >= 0)
		return vdprintf(g_log, fmt, ap);
	return vfprintf(stderr, fmt, ap);
}

struct link_spec {
	const char *prog;
	const char *sym;	/* NULL：tracepoint 挂载 */
	const char *tp;
	int ret;
};

static int attach_one(struct bpf_object *obj, const struct link_spec *s,
		      struct bpf_link **link)
{
	struct bpf_program *prog;
	long err;

	prog = bpf_object__find_program_by_name(obj, s->prog);
	if (!prog) {
		errno = ENOENT;
		return -1;
	}
	if (s->tp)
		*link = bpf_program__attach_tracepoint(prog, "airy", s->tp);
	else
		*link = bpf_program__attach_kprobe(prog, s->ret != 0, s->sym);
	err = libbpf_get_error(*link);
	if (err) {
		*link = NULL;
		errno = (int)-err;
		return -1;
	}
	return 0;
}

static int attach_all(struct bpf_object *obj, struct bpf_link **links)
{
	static const struct link_spec spec[AIRY_LINK_NR] = {
		{ "cap_in",	"airy_cap_check",		NULL, 0 },
		{ "cap_out",	"airy_cap_check",		NULL, 1 },
		{ "clt_in",	"airy_clt_serve",		NULL, 0 },
		{ "clt_out",	"airy_clt_serve",		NULL, 1 },
		{ "sched_in",	"airy_sched_ctl",		NULL, 0 },
		{ "sched_out",	"airy_sched_ctl",		NULL, 1 },
		{ "exec_in",	"airy_invoke_dispatch",		NULL, 0 },
		{ "exec_out",	"airy_invoke_dispatch",		NULL, 1 },
		{ "on_policy",	NULL, "airy_policy_decision",	0 },
		{ "on_enforce",	NULL, "airy_superv_enforce",	0 },
	};
	int n = 0, i;

	for (i = 0; i < AIRY_LINK_NR; i++) {
		if (attach_one(obj, &spec[i], &links[n])) {
			fprintf(stderr, "airy-probe: 挂载 %s 失败: %s\n",
				spec[i].prog, strerror(errno));
			break;
		}
		n++;
	}
	if (n == AIRY_LINK_NR)
		return 0;
	while (n > 0)
		bpf_link__destroy(links[--n]);
	return -1;
}

static int stat_sum(struct bpf_map *map, uint32_t key, uint64_t *sum)
{
	int ncpu = libbpf_num_possible_cpus();
	uint64_t *vals, total = 0;
	int i, fd = bpf_map__fd(map);

	if (ncpu <= 0)
		return -1;
	vals = calloc((size_t)ncpu, sizeof(*vals));
	if (!vals)
		return -1;
	if (bpf_map_lookup_elem(fd, &key, vals) < 0) {
		free(vals);
		return -1;
	}
	for (i = 0; i < ncpu; i++)
		total += vals[i];
	free(vals);
	*sum = total;
	return 0;
}

static void dump_stats(struct bpf_object *obj)
{
	static const char *const names[AIRY_STAT_NR] = {
		"cap_in", "cap_out", "policy", "enforce",
		"clt_in", "clt_out", "sched_in", "sched_out",
		"exec_in", "exec_out"
	};
	struct bpf_map *map = bpf_object__find_map_by_name(obj, "airy_stats");
	uint64_t val;
	uint32_t key;

	if (!map)
		return;
	for (key = 0; key < AIRY_STAT_NR; key++) {
		if (stat_sum(map, key, &val) == 0)
			printf("airy-probe: stat %s=%llu\n", names[key],
			       (unsigned long long)val);
	}
}

static int evt_cb(void *ctx, void *data, size_t len)
{
	const struct airy_evt *e = data;
	unsigned int tgid, tid;

	(void)ctx;
	if (len < sizeof(*e))
		return 0;
	tgid = (unsigned int)(e->pid >> 32);
	tid = (unsigned int)e->pid;

	switch (e->kind) {
	case 3:
		printf("{\"ts\":%llu,\"tgid\":%u,\"tid\":%u,\"kind\":3,"
		       "\"hook\":%u,\"table\":%u,\"verdict\":%u,"
		       "\"fault\":%u}\n",
		       (unsigned long long)e->ts, tgid, tid, e->pol.hook,
		       e->pol.table, e->pol.verdict, e->pol.fault);
		break;
	case 4:
		printf("{\"ts\":%llu,\"tgid\":%u,\"tid\":%u,\"kind\":4,"
		       "\"agent\":%u,\"action\":%u,\"reason\":%u}\n",
		       (unsigned long long)e->ts, tgid, tid, e->enf.agent,
		       e->enf.action, e->enf.reason);
		break;
	default:
		printf("{\"ts\":%llu,\"tgid\":%u,\"tid\":%u,\"kind\":%u}\n",
		       (unsigned long long)e->ts, tgid, tid, e->kind);
		break;
	}
	fflush(stdout);
	return 0;
}

static int parse(const char **path, int *secs, int argc, char **argv)
{
	char *end;
	long val;
	int i;

	for (i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--object") && i + 1 < argc) {
			*path = argv[++i];
		} else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) {
			errno = 0;
			val = strtol(argv[++i], &end, 10);
			if (errno || *end || val < 0 || val > 86400)
				return -1;
			*secs = (int)val;
		} else {
			fprintf(stderr, "airy-probe: 未知参数 %s\n", argv[i]);
			return -1;
		}
	}
	return 0;
}

static int run(struct bpf_object *obj, int secs)
{
	struct bpf_map *map;
	struct ring_buffer *rb;
	struct timespec t0, now;
	struct bpf_link *links[AIRY_LINK_NR] = { NULL };
	int n = 0, rc = 1, ret;

	if (attach_all(obj, links))
		goto out;
	n = AIRY_LINK_NR;
	map = bpf_object__find_map_by_name(obj, "airy_events");
	if (!map) {
		fprintf(stderr, "airy-probe: 缺少 airy_events map\n");
		goto out;
	}
	rb = ring_buffer__new(bpf_map__fd(map), evt_cb, NULL, NULL);
	if (!rb) {
		fprintf(stderr, "airy-probe: ringbuf 创建失败: %s\n",
			strerror(errno));
		goto out;
	}
	clock_gettime(CLOCK_MONOTONIC, &t0);
	rc = 0;
	while (!g_stop) {
		ret = ring_buffer__poll(rb, 200);
		if (ret < 0 && ret != -EINTR) {
			fprintf(stderr, "airy-probe: ringbuf 轮询失败: %s\n",
				strerror(-ret));
			rc = 1;
			break;
		}
		if (secs > 0) {
			clock_gettime(CLOCK_MONOTONIC, &now);
			if (now.tv_sec - t0.tv_sec >= secs)
				break;
		}
	}
	ring_buffer__free(rb);
out:
	while (n > 0)
		bpf_link__destroy(links[--n]);
	return rc;
}

int main(int argc, char **argv)
{
	struct bpf_object *obj;
	struct sigaction sa;
	const char *path = AIRY_BPF_OBJ;
	int secs = 0, rc;
	long err;

	if (parse(&path, &secs, argc, argv)) {
		fprintf(stderr, "用法: airy-probe [--object PATH] "
			"[--seconds N]\n");
		return 5;
	}
	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = on_sig;
	sigemptyset(&sa.sa_mask);
	(void)sigaction(SIGINT, &sa, NULL);
	(void)sigaction(SIGTERM, &sa, NULL);

	mk_log();
	libbpf_set_print(log_cb);

	obj = bpf_object__open_file(path, NULL);
	err = libbpf_get_error(obj);
	if (err) {
		fprintf(stderr, "airy-probe: 打开 %s 失败: %s\n", path,
			strerror((int)-err));
		return 1;
	}
	if (bpf_object__load(obj)) {
		fprintf(stderr, "airy-probe: 加载失败，诊断见 %s\n",
			AIRY_BPF_LOG);
		bpf_object__close(obj);
		return 1;
	}
	printf("airy-probe: object=%s seconds=%d\n", path, secs);
	rc = run(obj, secs);
	dump_stats(obj);
	bpf_object__close(obj);
	return rc;
}
