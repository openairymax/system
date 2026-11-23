// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * airy_probe.bpf.c — AirymaxOS 可编程探针（eBPF 侧）
 *
 * 职责：
 *   把安全能力判定与监督强制动作变成可从用户态消费的事件流：kprobe 与
 *   kretprobe 成对挂在 airy_cap_check，求判定耗时；tracepoint 挂在内核
 *   已发布的 airy_policy_decision / airy_superv_enforce 上，取带类型的
 *   裁决与强制语义。事件经 ringbuf 送出，计数留在 per-CPU array。
 *
 * 挂载点与来源：
 *   kernel/security/airy/airy_cap_check.c   int airy_cap_check()
 *   kernel/include/trace/events/airy.h      airy_policy_decision
 *                                           airy_superv_enforce
 *
 * 结构体契约：
 *   pd_ctx / enf_ctx 的字段顺序逐项对应 trace/events/airy.h 的
 *   TP_STRUCT__entry，首 8 字节是 trace_entry（type/flags/preempt_count/
 *   pid）。该顺序是追踪 ABI，由 ci/gates/ebpf-probe.sh 与内核头逐项比对，
 *   不在此处复制第二份判据。
 *
 * 不读 pt_regs：kprobe 上下文按架构各有一套布局，脱离 vmlinux.h 读取必然
 * 变成硬编码偏移（OS-OBS-012 禁止）。判定语义改由 tracepoint 提供，故本
 * 对象不含任何架构相关偏移，四架构共用同一份字节码。
 */

#include <linux/bpf.h>
#include <linux/types.h>
#include <bpf/bpf_helpers.h>

/* 事件类别，与加载器的打印分支一一对应。 */
enum airy_evt_kind {
	EVT_CAP_IN	= 1,
	EVT_CAP_OUT	= 2,
	EVT_POLICY	= 3,
	EVT_ENF		= 4,
};

/* per-CPU 计数格，顺序即加载器的打印顺序。 */
enum airy_stat_idx {
	ST_CAP_IN	= 0,
	ST_CAP_OUT	= 1,
	ST_POLICY	= 2,
	ST_ENF		= 3,
	ST_MAX		= 4,
};

/*
 * 事件载荷：kind 决定联合体分支。
 *   pol — airy_policy_decision 的 hook/table/verdict/fault
 *   enf — airy_superv_enforce 的 agent/action/reason
 * kprobe 类事件只带 ts/pid/kind。
 */
struct airy_evt {
	__u64 ts;
	__u64 pid;
	__u32 kind;
	union {
		struct {
			__u32 hook;
			__u32 table;
			__u32 verdict;
			__u32 fault;
		} pol;
		struct {
			__u32 agent;
			__u32 action;
			__u32 reason;
			__u32 pad;
		} enf;
	};
};

/* tracepoint 上下文：8 字节 trace_entry + TP_STRUCT__entry 顺序字段。 */
struct pd_ctx {
	__u64 pad;
	__u32 hook;
	__u32 table;
	__u32 verdict;
	__u32 fault;
};

struct enf_ctx {
	__u64 pad;
	__u32 agent_id;
	__u32 action;
	__u32 reason;
};

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, 1U << 22);
} airy_events SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
	__uint(max_entries, ST_MAX);
	__type(key, __u32);
	__type(value, __u64);
} airy_stats SEC(".maps");

static __always_inline void stat_add(__u32 idx)
{
	__u64 *v;

	v = bpf_map_lookup_elem(&airy_stats, &idx);
	if (v)
		(*v)++;
}

/* 预留事件并填公共字段；ringbuf 满时返回 NULL，调用方直接放弃本条。 */
static __always_inline struct airy_evt *evt_new(__u32 kind)
{
	struct airy_evt *e;

	e = bpf_ringbuf_reserve(&airy_events, sizeof(*e), 0);
	if (!e)
		return 0;
	e->ts = bpf_ktime_get_ns();
	e->pid = bpf_get_current_pid_tgid();
	e->kind = kind;
	return e;
}

SEC("kprobe/airy_cap_check")
int cap_in(void *ctx)
{
	struct airy_evt *e;

	(void)ctx;
	stat_add(ST_CAP_IN);
	e = evt_new(EVT_CAP_IN);
	if (e)
		bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("kretprobe/airy_cap_check")
int cap_out(void *ctx)
{
	struct airy_evt *e;

	(void)ctx;
	stat_add(ST_CAP_OUT);
	e = evt_new(EVT_CAP_OUT);
	if (e)
		bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tracepoint/airy/airy_policy_decision")
int on_policy(struct pd_ctx *ctx)
{
	struct airy_evt *e;

	stat_add(ST_POLICY);
	e = evt_new(EVT_POLICY);
	if (!e)
		return 0;
	e->pol.hook = ctx->hook;
	e->pol.table = ctx->table;
	e->pol.verdict = ctx->verdict;
	e->pol.fault = ctx->fault;
	bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tracepoint/airy/airy_superv_enforce")
int on_enforce(struct enf_ctx *ctx)
{
	struct airy_evt *e;

	stat_add(ST_ENF);
	e = evt_new(EVT_ENF);
	if (!e)
		return 0;
	e->enf.agent = ctx->agent_id;
	e->enf.action = ctx->action;
	e->enf.reason = ctx->reason;
	bpf_ringbuf_submit(e, 0);
	return 0;
}

char _license[] SEC("license") = "GPL";
