// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy_perf.c — AirymaxOS perf 采样入口
 *
 * 职责：
 *   把上游 perf 的 stat / record / report 三条链路收成一个可编排入口，
 *   输出固定为机器可读形态：stat 落 CSV（OS-OBS-032），report 落 JSON 行，
 *   bench 把三者收进同一目录（03-perf-analysis.md §8.3）。
 *
 * 边界：
 *   perf 只作可观测性前端。本工具只读采样，不改调度、不写 sysfs/procfs，
 *   不 fork perf 源码，也不解析 perf.data——统计仍由上游 perf 产出。
 *
 * 命令行：
 *   airy-perf stat   [--duration N] [--pid PID] [--out FILE]
 *   airy-perf record [--duration N] [--pid PID] [--freq F] [--out FILE]
 *   airy-perf report [--input FILE] [--top N]
 *   airy-perf bench  [--duration N] [--out DIR]
 *
 * 退出码：0 成功；2 参数不合法；3 perf 无法启动；4 报告缺失或为空。
 */

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define PF_LOG_DIR	"/var/log/airy"
#define PF_IRQ		"/proc/sys/kernel/irq_balancing"
#define PF_SECS		10
#define PF_FREQ		99
#define PF_TOP		20
#define PF_PATH		256
#define PF_LINE		512

/* OS-OBS-032：基准数据源只用 CSV，事件集随基准固定，避免各次口径漂移。 */
#define PF_EVENTS	"cycles,instructions,cache-misses,cache-references," \
			"branch-misses,branch-instructions"

struct pf_opt {
	long secs;
	long freq;
	long top;
	int pid;
	const char *out;
	const char *in;
};

static void usage(FILE *to, int rc)
{
	fprintf(to,
		"airy-perf stat   [--duration N] [--pid PID] [--out FILE]\n"
		"airy-perf record [--duration N] [--pid PID] [--freq F] [--out FILE]\n"
		"airy-perf report [--input FILE] [--top N]\n"
		"airy-perf bench  [--duration N] [--out DIR]\n");
	exit(rc);
}

static long arg_long(const char *opt, const char *val, long lo, long hi)
{
	char *end = NULL;
	long v;

	errno = 0;
	v = strtol(val, &end, 10);
	if (errno || !end || *end || v < lo || v > hi) {
		fprintf(stderr, "airy-perf: %s 取值不合法: %s\n", opt, val);
		exit(2);
	}
	return v;
}

static void path_stamp(char *buf, size_t n, const char *dir,
		       const char *tag, const char *ext)
{
	long long now = (long long)time(NULL);

	if (snprintf(buf, n, "%s/%s-%lld%s", dir, tag, now, ext) >= (int)n) {
		fprintf(stderr, "airy-perf: 输出路径过长\n");
		exit(2);
	}
}

static void path_join(char *buf, size_t n, const char *dir, const char *name)
{
	int len = snprintf(buf, n, "%s/%s", dir, name);

	if (len < 0 || (size_t)len >= n) {
		fprintf(stderr, "airy-perf: 输出路径过长\n");
		exit(2);
	}
}

static void mk_one(const char *dir)
{
	if (mkdir(dir, 0755) == 0 || errno == EEXIST)
		return;
	fprintf(stderr, "airy-perf: 无法创建 %s: %s\n", dir, strerror(errno));
	exit(2);
}

static void mkdir_p(const char *dir)
{
	char path[PF_PATH];
	char *p;

	if (snprintf(path, sizeof(path), "%s", dir) >= (int)sizeof(path)) {
		fprintf(stderr, "airy-perf: 输出路径过长\n");
		exit(2);
	}
	for (p = path + 1; *p; p++) {
		if (*p != '/')
			continue;
		*p = '\0';
		mk_one(path);
		*p = '/';
	}
	mk_one(path);
}

/*
 * run_child —— 以 perf 身份执行 ARGV，OUT 非空时把 stdout 落到该文件。
 * 返回 perf 的退出码；无法启动时统一折成 3，与「perf 报错」区分。
 */
static int run_child(char *const argv[], const char *out)
{
	int fd = -1;
	pid_t pid;
	int st;

	if (out) {
		fd = open(out, O_WRONLY | O_CREAT | O_TRUNC, 0644);
		if (fd < 0) {
			fprintf(stderr, "airy-perf: 无法写 %s: %s\n", out,
				strerror(errno));
			return 2;
		}
	}
	pid = fork();
	if (pid < 0) {
		fprintf(stderr, "airy-perf: fork 失败: %s\n", strerror(errno));
		if (fd >= 0)
			close(fd);
		return 3;
	}
	if (pid == 0) {
		if (fd >= 0 && dup2(fd, STDOUT_FILENO) < 0)
			_exit(3);
		if (fd >= 0)
			close(fd);
		execvp(argv[0], argv);
		dprintf(STDERR_FILENO, "airy-perf: 无法执行 %s: %s\n", argv[0],
			strerror(errno));
		_exit(3);
	}
	if (fd >= 0)
		close(fd);
	while (waitpid(pid, &st, 0) < 0) {
		if (errno != EINTR)
			return 3;
	}
	return WIFEXITED(st) ? WEXITSTATUS(st) : 3;
}

static void fill_scope(char **argv, int *n, const struct pf_opt *o,
		       char *pidbuf, size_t npid)
{
	if (o->pid > 0) {
		snprintf(pidbuf, npid, "%d", o->pid);
		argv[(*n)++] = "-p";
		argv[(*n)++] = pidbuf;
	} else {
		argv[(*n)++] = "-a";
	}
}

static int pf_stat(const struct pf_opt *o)
{
	char out[PF_PATH];
	char secs[16];
	char pidbuf[16];
	char *argv[16];
	int n = 0;
	int rc;

	if (o->out) {
		snprintf(out, sizeof(out), "%s", o->out);
	} else {
		mkdir_p(PF_LOG_DIR);
		path_stamp(out, sizeof(out), PF_LOG_DIR, "perf-stat", ".csv");
	}
	snprintf(secs, sizeof(secs), "%ld", o->secs);
	argv[n++] = "perf";
	argv[n++] = "stat";
	argv[n++] = "-x,";
	argv[n++] = "-e";
	argv[n++] = PF_EVENTS;
	fill_scope(argv, &n, o, pidbuf, sizeof(pidbuf));
	argv[n++] = "--";
	argv[n++] = "sleep";
	argv[n++] = secs;
	argv[n] = NULL;

	rc = run_child(argv, out);
	if (rc == 0)
		printf("%s\n", out);
	return rc;
}

static int pf_record(const struct pf_opt *o)
{
	char out[PF_PATH];
	char secs[16];
	char freq[16];
	char pidbuf[16];
	char *argv[20];
	int n = 0;
	int rc;

	if (o->out) {
		snprintf(out, sizeof(out), "%s", o->out);
	} else {
		mkdir_p(PF_LOG_DIR);
		path_stamp(out, sizeof(out), PF_LOG_DIR, "perf-record",
			   ".perf.data");
	}
	snprintf(secs, sizeof(secs), "%ld", o->secs);
	snprintf(freq, sizeof(freq), "%ld", o->freq);
	argv[n++] = "perf";
	argv[n++] = "record";
	argv[n++] = "-o";
	argv[n++] = out;
	argv[n++] = "-F";
	argv[n++] = freq;
	argv[n++] = "-g";
	argv[n++] = "--call-graph";
	argv[n++] = "dwarf";
	fill_scope(argv, &n, o, pidbuf, sizeof(pidbuf));
	argv[n++] = "--";
	argv[n++] = "sleep";
	argv[n++] = secs;
	argv[n] = NULL;

	rc = run_child(argv, NULL);
	if (rc == 0)
		printf("%s\n", out);
	return rc;
}

static void json_esc(const char *s, char *d, size_t n)
{
	size_t i = 0;

	for (; *s && i + 2 < n; s++) {
		if (*s == '"' || *s == '\\')
			d[i++] = '\\';
		d[i++] = *s;
	}
	d[i] = '\0';
}

static int pf_report(const struct pf_opt *o)
{
	const char *in = o->in ? o->in : "perf.data";
	char *argv[12];
	char line[PF_LINE];
	char sym[PF_LINE];
	char dso[PF_LINE];
	char esym[PF_LINE];
	char edso[PF_LINE];
	int fds[2];
	int n = 0;
	int cnt = 0;
	double oh;
	FILE *fp;
	pid_t pid;
	int st;

	if (access(in, R_OK) != 0) {
		fprintf(stderr, "airy-perf: 读不到 %s: %s\n", in, strerror(errno));
		return 4;
	}
	argv[n++] = "perf";
	argv[n++] = "report";
	argv[n++] = "--stdio";
	argv[n++] = "--sort";
	argv[n++] = "overhead,symbol,dso";
	argv[n++] = "-i";
	argv[n++] = (char *)in;
	argv[n] = NULL;

	if (pipe(fds) != 0) {
		fprintf(stderr, "airy-perf: pipe 失败: %s\n", strerror(errno));
		return 3;
	}
	pid = fork();
	if (pid <= 0) {
		if (pid == 0) {
			close(fds[0]);
			if (dup2(fds[1], STDOUT_FILENO) < 0)
				_exit(3);
			close(fds[1]);
			execvp(argv[0], argv);
			_exit(3);
		}
		close(fds[0]);
		close(fds[1]);
		return 3;
	}
	close(fds[1]);
	fp = fdopen(fds[0], "r");
	if (!fp) {
		close(fds[0]);
		return 3;
	}
	puts("[");
	while (fgets(line, sizeof(line), fp)) {
		if (line[0] == '#' || line[0] == '\n')
			continue;
		if (sscanf(line, " %lf%% %511s %511s", &oh, sym, dso) != 3)
			continue;
		if (cnt >= o->top)
			continue;
		json_esc(sym, esym, sizeof(esym));
		json_esc(dso, edso, sizeof(edso));
		printf("%s{\"overhead\":%.2f,\"symbol\":\"%s\",\"dso\":\"%s\"}\n",
		       cnt ? "," : "", oh, esym, edso);
		cnt++;
	}
	puts("]");
	fclose(fp);
	while (waitpid(pid, &st, 0) < 0) {
		if (errno != EINTR)
			return 3;
	}
	if (cnt == 0) {
		fprintf(stderr, "airy-perf: %s 无热点样本\n", in);
		return 4;
	}
	return WIFEXITED(st) ? WEXITSTATUS(st) : 3;
}

/* irq_state —— 只读 OS-STD-022 的判据位；缺失返回 -1，不代写。 */
static int irq_state(void)
{
	FILE *f = fopen(PF_IRQ, "r");
	int v = -1;

	if (!f)
		return -1;
	if (fscanf(f, "%d", &v) != 1)
		v = -1;
	fclose(f);
	return v;
}

static void pf_meta(const char *dir, const struct pf_opt *o)
{
	char path[PF_PATH];
	FILE *f;
	int irq = irq_state();

	if (snprintf(path, sizeof(path), "%s/meta.json", dir) >= (int)sizeof(path))
		return;
	f = fopen(path, "w");
	if (!f)
		return;
	fprintf(f, "{\"duration\":%ld,\"freq\":%ld,\"top\":%ld,"
		"\"irq_balancing\":%d}\n", o->secs, o->freq, o->top, irq);
	fclose(f);
}

static int pf_bench(const struct pf_opt *o)
{
	char dir[PF_PATH];
	char path[PF_PATH];
	struct pf_opt sub = *o;
	int fd;
	int saved;
	int rc;
	int irq;

	if (o->out) {
		snprintf(dir, sizeof(dir), "%s", o->out);
	} else {
		mkdir_p(PF_LOG_DIR);
		path_stamp(dir, sizeof(dir), PF_LOG_DIR, "perf-bench", "");
	}
	mkdir_p(dir);

	irq = irq_state();
	if (irq > 0) {
		fprintf(stderr,
			"airy-perf: irq_balancing=%d，基线需置 0（OS-STD-022）\n",
			irq);
	}

	path_join(path, sizeof(path), dir, "stat-summary.csv");
	sub.out = path;
	rc = pf_stat(&sub);
	if (rc)
		return rc;
	fflush(stdout);

	path_join(path, sizeof(path), dir, "record.perf.data");
	sub.out = path;
	rc = pf_record(&sub);
	if (rc)
		return rc;

	fflush(stdout);
	saved = dup(STDOUT_FILENO);
	path_join(path, sizeof(path), dir, "report.json");
	fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
	if (saved < 0 || fd < 0 || dup2(fd, STDOUT_FILENO) < 0) {
		if (fd >= 0)
			close(fd);
		if (saved >= 0)
			close(saved);
		return 2;
	}
	close(fd);
	path_join(path, sizeof(path), dir, "record.perf.data");
	sub.in = path;
	sub.out = NULL;
	rc = pf_report(&sub);
	fflush(stdout);
	dup2(saved, STDOUT_FILENO);
	close(saved);
	pf_meta(dir, o);
	if (rc == 0)
		printf("%s\n", dir);
	return rc;
}

int main(int argc, char **argv)
{
	struct pf_opt o = { .secs = PF_SECS, .freq = PF_FREQ, .top = PF_TOP };
	const char *cmd;
	int i;

	if (argc < 2)
		usage(stderr, 2);
	if (!strcmp(argv[1], "-h") || !strcmp(argv[1], "--help"))
		usage(stdout, 0);
	cmd = argv[1];
	for (i = 2; i < argc; i++) {
		const char *a = argv[i];
		int last = i + 1 >= argc;

		if (!strcmp(a, "--duration") && !last)
			o.secs = arg_long(a, argv[++i], 1, 86400);
		else if (!strcmp(a, "--freq") && !last)
			o.freq = arg_long(a, argv[++i], 1, 100000);
		else if (!strcmp(a, "--pid") && !last)
			o.pid = (int)arg_long(a, argv[++i], 1, 0x7fffffff);
		else if (!strcmp(a, "--top") && !last)
			o.top = arg_long(a, argv[++i], 1, 100);
		else if (!strcmp(a, "--out") && !last)
			o.out = argv[++i];
		else if (!strcmp(a, "--input") && !last)
			o.in = argv[++i];
		else
			usage(stderr, 2);
	}
	if (!strcmp(cmd, "stat"))
		return pf_stat(&o);
	if (!strcmp(cmd, "record"))
		return pf_record(&o);
	if (!strcmp(cmd, "report"))
		return pf_report(&o);
	if (!strcmp(cmd, "bench"))
		return pf_bench(&o);
	usage(stderr, 2);
	return 2;
}
