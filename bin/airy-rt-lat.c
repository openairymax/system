// SPDX-License-Identifier: AGPL-3.0-or-later OR Apache-2.0
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy-rt-lat.c — AirymaxOS 唤醒延迟测量
 *
 * 职责：
 *   以 SCHED_FIFO 周期线程测量「本该唤醒的时刻」到「实际醒来的时刻」之间的
 *   延迟，输出 JSON 或一行可读结论，供实时性验收门禁与现场诊断使用。
 *
 * 为什么产品要自带测量：
 *   0.0.4 的实时性验收要求在被测机上产出基线，而 AirymaxOS 的仓库里没有
 *   rt-tests，cyclictest 无法作为产品自身能力交付。把验收建立在外部软件包
 *   上，机器装不出来时就只能跳过测量，跳过等于没有门禁。这里的测量循环与
 *   cyclictest 同构：绝对时钟周期唤醒、FIFO 优先级、mlockall 锁页、按微秒
 *   统计 min/avg/max 与高分位。
 *
 * 判定边界：
 *   拿不到 FIFO 优先级时直接失败，不降级成普通调度再报告数字。普通优先级
 *   下测到的是「进程被调度得有多差」，不是这台机器的实时能力，这种数字比
 *   没有数字更危险。
 *
 * 退出码：0 测量完成；1 无法测量；2 用法错误。
 */

#define _GNU_SOURCE

#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define RL_NAME		"airy-rt-lat"
#define RL_VERSION	"0.0.4"
#define RL_SCHEMA	1

#define RL_PRIO_MIN	1
#define RL_PRIO_MAX	99
#define RL_ISEC_MAX	1000000UL
#define RL_SEC_MAX	3600U
#define RL_BUCKET_MAX	100
#define RL_SPAN_MAX	100000

struct opts {
	int prio;
	unsigned long interval_us;
	unsigned int duration_s;
	int threads;
	int bucket_us;
	int span_us;
	int pin;
	int json;
};

struct hist {
	uint64_t *cnt;
	uint64_t over;
	uint64_t loops;
	int64_t sum_ns;
	int64_t max_ns;
	int64_t min_ns;
	int bucket_us;
	int buckets;
};

struct worker {
	const struct opts *opts;
	struct hist hist;
	pthread_t tid;
	int cpu;
	uint64_t loops;
	int err;
};

static volatile sig_atomic_t g_stop;

static void rl_sig(int signum)
{
	(void)signum;
	g_stop = 1;
}

static int64_t rl_ns(const struct timespec *ts)
{
	return (int64_t)ts->tv_sec * 1000000000LL + ts->tv_nsec;
}

static int64_t rl_ceil_us(int64_t ns)
{
	return (ns + 999) / 1000;
}

static void hist_init(struct hist *h, const struct opts *o)
{
	h->bucket_us = o->bucket_us;
	h->buckets = o->span_us / o->bucket_us + 1;
	h->cnt = calloc((size_t)h->buckets, sizeof(*h->cnt));
	h->min_ns = INT64_MAX;
}

static void hist_add(struct hist *h, int64_t late)
{
	int64_t us = rl_ceil_us(late);

	if (us >= (int64_t)h->buckets * h->bucket_us)
		h->over++;
	else
		h->cnt[us / h->bucket_us]++;
	h->loops++;
	h->sum_ns += late;
	if (late > h->max_ns)
		h->max_ns = late;
	if (late < h->min_ns)
		h->min_ns = late;
}

static int64_t hist_pct(const struct hist *h, int per_mille)
{
	uint64_t target = (h->loops * (uint64_t)per_mille + 999) / 1000;
	uint64_t seen = 0;
	int idx;

	if (!h->loops)
		return 0;
	for (idx = 0; idx < h->buckets; idx++) {
		seen += h->cnt[idx];
		if (seen >= target)
			return (int64_t)(idx + 1) * h->bucket_us;
	}
	return (int64_t)h->buckets * h->bucket_us;
}

static int rl_boost(struct worker *w)
{
	struct sched_param sp = { .sched_priority = w->opts->prio };
	cpu_set_t set;

	if (sched_setscheduler(0, SCHED_FIFO, &sp) < 0) {
		fprintf(stderr, RL_NAME ": sched_setscheduler: %s；"
			"需要 root 或 CAP_SYS_NICE\n", strerror(errno));
		return -1;
	}
	if (!w->opts->pin)
		return 0;
	CPU_ZERO(&set);
	CPU_SET(w->cpu, &set);
	if (pthread_setaffinity_np(pthread_self(), sizeof(set), &set)) {
		fprintf(stderr, RL_NAME ": 无法绑定 CPU %d\n", w->cpu);
		return -1;
	}
	return 0;
}

static void *thr_run(void *arg)
{
	struct worker *w = arg;
	struct timespec next, now;
	uint64_t period = (uint64_t)w->opts->interval_us * 1000;
	uint64_t n;
	int rc;

	if (rl_boost(w) < 0) {
		w->err = 1;
		return NULL;
	}
	clock_gettime(CLOCK_MONOTONIC, &next);
	for (n = 0; n < w->loops && !g_stop; n++) {
		next.tv_nsec += (long)(period % 1000000000ULL);
		next.tv_sec += (time_t)(period / 1000000000ULL);
		if (next.tv_nsec >= 1000000000L) {
			next.tv_nsec -= 1000000000L;
			next.tv_sec++;
		}
		rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next,
				     NULL);
		while (rc == EINTR && !g_stop)
			rc = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME,
					     &next, NULL);
		if (g_stop)
			break;
		if (rc) {
			fprintf(stderr, RL_NAME ": clock_nanosleep: %s\n",
				strerror(rc));
			w->err = 1;
			return NULL;
		}
		clock_gettime(CLOCK_MONOTONIC, &now);
		hist_add(&w->hist, rl_ns(&now) - rl_ns(&next));
	}
	return NULL;
}

static void usage(FILE *out)
{
	fprintf(out,
		"usage: " RL_NAME " [-p PRIO] [-i US] [-D SEC] [-t N]\n"
		"                    [-b US] [-s US] [-a] [-j] [-h] [-V]\n"
		"\n"
		"  -p PRIO  FIFO 优先级，1-99，缺省 90\n"
		"  -i US    周期，微秒，缺省 200\n"
		"  -D SEC   测量时长，秒，缺省 30\n"
		"  -t N     线程数，缺省 1\n"
		"  -b US    直方图桶宽，微秒，缺省 1\n"
		"  -s US    直方图上限，微秒，缺省 400\n"
		"  -a       每线程绑定到独立 CPU\n"
		"  -j       输出一行 JSON\n"
		"  -h       显示本帮助\n"
		"  -V       显示版本\n");
}

static int opt_num(const char *text, long low, long high, long *out)
{
	char *end = NULL;
	long val;

	errno = 0;
	val = strtol(text, &end, 10);
	if (errno || !end || *end || val < low || val > high)
		return -1;
	*out = val;
	return 0;
}

static int opt_parse(struct opts *o, int argc, char **argv)
{
	long val;
	int ch;

	o->prio = 90;
	o->interval_us = 200;
	o->duration_s = 30;
	o->threads = 1;
	o->bucket_us = 1;
	o->span_us = 400;
	o->pin = 0;
	o->json = 0;
	while ((ch = getopt(argc, argv, "p:i:D:t:b:s:ajhV")) != -1) {
		switch (ch) {
		case 'p':
			if (opt_num(optarg, RL_PRIO_MIN, RL_PRIO_MAX, &val))
				return -1;
			o->prio = (int)val;
			break;
		case 'i':
			if (opt_num(optarg, 1, (long)RL_ISEC_MAX, &val))
				return -1;
			o->interval_us = (unsigned long)val;
			break;
		case 'D':
			if (opt_num(optarg, 1, (long)RL_SEC_MAX, &val))
				return -1;
			o->duration_s = (unsigned int)val;
			break;
		case 't':
			if (opt_num(optarg, 1, 1024, &val))
				return -1;
			o->threads = (int)val;
			break;
		case 'b':
			if (opt_num(optarg, 1, RL_BUCKET_MAX, &val))
				return -1;
			o->bucket_us = (int)val;
			break;
		case 's':
			if (opt_num(optarg, 1, RL_SPAN_MAX, &val))
				return -1;
			o->span_us = (int)val;
			break;
		case 'a':
			o->pin = 1;
			break;
		case 'j':
			o->json = 1;
			break;
		case 'h':
			usage(stdout);
			exit(0);
		case 'V':
			printf(RL_NAME " %s\n", RL_VERSION);
			exit(0);
		default:
			usage(stderr);
			return -1;
		}
	}
	if (optind != argc || o->span_us < o->bucket_us) {
		usage(stderr);
		return -1;
	}
	return 0;
}

static void thr_out(const struct worker *w, int pin)
{
	int64_t min_us = w->hist.loops ? rl_ceil_us(w->hist.min_ns) : 0;
	double avg = w->hist.loops ?
		(double)w->hist.sum_ns / w->hist.loops / 1000.0 : 0.0;

	printf("T: %d cpu=%d loops=%" PRIu64 " min=%" PRId64
	       "us avg=%.1fus p50=%" PRId64 "us p99=%" PRId64
	       "us p999=%" PRId64 "us max=%" PRId64 "us\n",
	       w->cpu, pin ? w->cpu : -1, w->hist.loops, min_us, avg,
	       hist_pct(&w->hist, 500), hist_pct(&w->hist, 990),
	       hist_pct(&w->hist, 999),
	       w->hist.loops ? rl_ceil_us(w->hist.max_ns) : 0);
}

static void thr_json(const struct worker *w, int idx)
{
	int64_t min_us = w->hist.loops ? rl_ceil_us(w->hist.min_ns) : 0;
	double avg = w->hist.loops ?
		(double)w->hist.sum_ns / w->hist.loops / 1000.0 : 0.0;

	printf("%s{\"thread\":%d,\"cpu\":%d,\"loops\":%" PRIu64
	       ",\"min_us\":%" PRId64 ",\"avg_us\":%.1f"
	       ",\"p50_us\":%" PRId64 ",\"p99_us\":%" PRId64
	       ",\"p999_us\":%" PRId64 ",\"max_us\":%" PRId64
	       ",\"overflow\":%" PRIu64 "}",
	       idx ? "," : "", idx, w->cpu, w->hist.loops, min_us, avg,
	       hist_pct(&w->hist, 500), hist_pct(&w->hist, 990),
	       hist_pct(&w->hist, 999),
	       w->hist.loops ? rl_ceil_us(w->hist.max_ns) : 0,
	       w->hist.over);
}

static void rl_report(struct worker *ws, const struct opts *o, int mlock)
{
	int64_t max_ns = 0;
	uint64_t loops = 0;
	int i;

	for (i = 0; i < o->threads; i++) {
		if (ws[i].hist.max_ns > max_ns)
			max_ns = ws[i].hist.max_ns;
		loops += ws[i].hist.loops;
	}
	if (o->json) {
		printf("{\"tool\":\"" RL_NAME "\",\"schema\":%d,"
		       "\"policy\":\"fifo\",\"prio\":%d,"
		       "\"interval_us\":%lu,\"duration_s\":%u,"
		       "\"bucket_us\":%d,\"span_us\":%d,"
		       "\"mlock\":%s,\"pinned\":%s,\"aborted\":%s,"
		       "\"loops\":%" PRIu64 ",\"max_us\":%" PRId64
		       ",\"threads\":[",
		       RL_SCHEMA, o->prio, o->interval_us, o->duration_s,
		       o->bucket_us, o->span_us, mlock ? "true" : "false",
		       o->pin ? "true" : "false", g_stop ? "true" : "false",
		       loops, loops ? rl_ceil_us(max_ns) : 0);
		for (i = 0; i < o->threads; i++)
			thr_json(&ws[i], i);
		printf("]}\n");
		return;
	}
	printf(RL_NAME " prio=%d interval=%luus duration=%us threads=%d"
	       " mlock=%s\n", o->prio, o->interval_us, o->duration_s,
	       o->threads, mlock ? "yes" : "no");
	for (i = 0; i < o->threads; i++)
		thr_out(&ws[i], o->pin);
	printf("max=%" PRId64 "us loops=%" PRIu64 "%s\n",
	       loops ? rl_ceil_us(max_ns) : 0, loops,
	       g_stop ? " aborted=yes" : "");
}

static int rl_run(struct worker *ws, const struct opts *o)
{
	long cpus = sysconf(_SC_NPROCESSORS_ONLN);
	int i;
	int rc;

	if (cpus < 1)
		cpus = 1;
	for (i = 0; i < o->threads; i++) {
		ws[i].opts = o;
		ws[i].cpu = i % (int)cpus;
		hist_init(&ws[i].hist, o);
		if (!ws[i].hist.cnt)
			return -1;
		ws[i].loops = (uint64_t)o->duration_s * 1000000ULL /
			o->interval_us;
	}
	for (i = 0; i < o->threads; i++) {
		rc = pthread_create(&ws[i].tid, NULL, thr_run, &ws[i]);
		if (rc) {
			fprintf(stderr, RL_NAME ": pthread_create: %s\n",
				strerror(rc));
			ws[i].err = 1;
			break;
		}
	}
	while (--i >= 0)
		pthread_join(ws[i].tid, NULL);
	for (i = 0; i < o->threads; i++) {
		if (ws[i].err)
			return -1;
	}
	return 0;
}

int main(int argc, char **argv)
{
	struct worker *ws;
	struct opts o;
	int mlock;
	int rc;
	int i;

	if (opt_parse(&o, argc, argv))
		return 2;
	ws = calloc((size_t)o.threads, sizeof(*ws));
	if (!ws) {
		fprintf(stderr, RL_NAME ": 内存不足\n");
		return 1;
	}
	signal(SIGINT, rl_sig);
	signal(SIGTERM, rl_sig);
	signal(SIGPIPE, SIG_IGN);
	mlock = mlockall(MCL_CURRENT | MCL_FUTURE) == 0;
	if (!mlock)
		fprintf(stderr, RL_NAME ": mlockall: %s；统计可能受缺页影响\n",
			strerror(errno));
	rc = rl_run(ws, &o);
	if (!rc)
		rl_report(ws, &o, mlock);
	for (i = 0; i < o.threads; i++)
		free(ws[i].hist.cnt);
	free(ws);
	return rc ? 1 : 0;
}
