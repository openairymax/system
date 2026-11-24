/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * obs_view.h — 观测快照的公共解析、渲染与目录扫描。
 *
 * 每个计量模块的 procfs 视图都是「键: 值」逐行文本。工具侧若各写一份
 * 解析与 JSON 渲染，字段名就会有两份定义，内核增删一个键时总有一侧
 * 忘记同步。这里只认行格式、不认具体键名：JSON 直接按内核顺序逐字
 * 透出，字段面因此天然对齐，新增计量模块不必再复制一遍。
 *
 * 数值判定同样只做一次：整串为十进制数字即按数字输出，否则按字符串。
 * 这样 ALERT/NORMAL 之类的档位文本不会被误渲染成裸标识符。
 */

#ifndef _AIRY_OBS_VIEW_H
#define _AIRY_OBS_VIEW_H

#include <stddef.h>

#define OBS_KEY_MAX	40
#define OBS_VAL_MAX	56
#define OBS_KV_MAX	64
#define OBS_BUF		8192

struct obs_kv {
	char key[OBS_KEY_MAX];
	char val[OBS_VAL_MAX];
};

struct obs_row {
	unsigned id;
	unsigned long long val;
};

/* 十进制无符号整数；整串必须被消费完。 */
int obs_num(const char *s, unsigned long long *out);

/* Agent 号：十进制且不超过 32 位；0 视为非法。 */
int obs_id(const char *s, unsigned *out);

/* 拼出 <root>/<sub>/<id>/<file>；超长返回 -1。 */
int obs_path(char *out, size_t len, const char *root, const char *sub,
	     unsigned id, const char *file);

/* 读入整个文件并以 NUL 结尾；失败返回 -1。 */
int obs_read(const char *path, char *buf, size_t len);

/* 解析「键: 值」行；返回条数，格式不成立返回 -1。 */
int obs_parse(const char *buf, struct obs_kv *kv, unsigned max);

/* 取一个键的原始值；缺失返回 NULL。 */
const char *obs_get(const struct obs_kv *kv, unsigned n, const char *key);

/* 取一个键的数值；缺失或非数字返回 -1。 */
int obs_u64(const struct obs_kv *kv, unsigned n, const char *key,
	    unsigned long long *out);

/* 按内核顺序输出一行 JSON 对象。 */
void obs_json(const struct obs_kv *kv, unsigned n);

/* 读一个 Agent 的快照并解析；返回条数，缺失或格式不成立返回 -1。 */
int obs_load(const char *root, const char *sub, unsigned id,
	     const char *file, struct obs_kv *kv, unsigned max);

/* 原样透出一个 Agent 的快照文本；失败返回 -1。 */
int obs_cat(const char *root, const char *sub, unsigned id, const char *file);

/* 行排序：指标降序，同值按 Agent 号升序。 */
int obs_row_cmp(const void *a, const void *b);

/* 扫描 <root>/<sub>/<id>/<file>，按 metric 取值；返回行数或 -1。 */
int obs_scan(const char *root, const char *sub, const char *file,
	     const char *metric, struct obs_row *rows, unsigned max);

#endif /* _AIRY_OBS_VIEW_H */
