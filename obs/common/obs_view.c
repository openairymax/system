// SPDX-License-Identifier: GPL-2.0-only
/*
 * Copyright (c) 2025-2026 SPHARX Ltd. All Rights Reserved.
 *
 * obs_view.c — 观测快照的公共解析、渲染与目录扫描。
 *
 * 行格式只有一种：小写键、冒号、一个空格、到行尾的值。解析器不维护
 * 键白名单，内核多一个键，JSON 与 top 都自动跟上；少一个键，读它的
 * 调用方得到缺失而不是零值，不会把「没有数据」冒充成「数据为零」。
 */

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "obs_view.h"

int obs_num(const char *s, unsigned long long *out)
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

int obs_id(const char *s, unsigned *out)
{
	unsigned long long v;

	if (obs_num(s, &v) || v == 0 || v > 0xffffffffULL)
		return -1;

	*out = (unsigned)v;
	return 0;
}

int obs_path(char *out, size_t len, const char *root, const char *sub,
	     unsigned id, const char *file)
{
	int n = snprintf(out, len, "%s/%s/%u/%s", root, sub, id, file);

	return n < 0 || (size_t)n >= len ? -1 : 0;
}

int obs_read(const char *path, char *buf, size_t len)
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

/* 键只允许小写字母、数字与下划线，避免把内核的排版文本当成字段。 */
static int key_ok(const char *k, size_t n)
{
	size_t i;

	if (!n)
		return 0;

	for (i = 0; i < n; i++) {
		char c = k[i];

		if ((c < 'a' || c > 'z') && (c < '0' || c > '9') && c != '_')
			return 0;
	}

	return 1;
}

int obs_parse(const char *buf, struct obs_kv *kv, unsigned max)
{
	const char *p = buf;
	unsigned n = 0;

	while (p && *p) {
		const char *nl = strchr(p, '\n');
		const char *sep = strstr(p, ": ");
		size_t klen;
		size_t vlen;

		if (!nl)
			nl = p + strlen(p);

		if (nl == p) {
			p = *nl ? nl + 1 : nl;
			continue;
		}

		if (!sep || sep > nl)
			return -1;

		klen = (size_t)(sep - p);
		vlen = (size_t)(nl - sep - 2);
		if (!key_ok(p, klen) || klen >= OBS_KEY_MAX ||
		    vlen >= OBS_VAL_MAX || n >= max)
			return -1;

		memcpy(kv[n].key, p, klen);
		kv[n].key[klen] = '\0';
		memcpy(kv[n].val, sep + 2, vlen);
		kv[n].val[vlen] = '\0';
		n++;

		p = *nl ? nl + 1 : nl;
	}

	return n ? (int)n : -1;
}

const char *obs_get(const struct obs_kv *kv, unsigned n, const char *key)
{
	unsigned i;

	for (i = 0; i < n; i++) {
		if (!strcmp(kv[i].key, key))
			return kv[i].val;
	}

	return NULL;
}

int obs_u64(const struct obs_kv *kv, unsigned n, const char *key,
	    unsigned long long *out)
{
	const char *v = obs_get(kv, n, key);

	return v ? obs_num(v, out) : -1;
}

static int val_num(const char *v)
{
	unsigned long long tmp;

	return obs_num(v, &tmp) == 0;
}

void obs_json(const struct obs_kv *kv, unsigned n)
{
	unsigned i;

	putchar('{');
	for (i = 0; i < n; i++) {
		printf("%s\"%s\":", i ? "," : "", kv[i].key);
		if (val_num(kv[i].val))
			printf("%s", kv[i].val);
		else
			printf("\"%s\"", kv[i].val);
	}
	printf("}\n");
}

int obs_load(const char *root, const char *sub, unsigned id,
	     const char *file, struct obs_kv *kv, unsigned max)
{
	char path[256];
	char buf[OBS_BUF];

	if (obs_path(path, sizeof(path), root, sub, id, file) ||
	    obs_read(path, buf, sizeof(buf)))
		return -1;

	return obs_parse(buf, kv, max);
}

int obs_cat(const char *root, const char *sub, unsigned id, const char *file)
{
	char path[256];
	char buf[OBS_BUF];

	if (obs_path(path, sizeof(path), root, sub, id, file) ||
	    obs_read(path, buf, sizeof(buf)))
		return -1;

	fputs(buf, stdout);
	return 0;
}

int obs_row_cmp(const void *a, const void *b)
{
	const struct obs_row *x = a;
	const struct obs_row *y = b;

	if (x->val != y->val)
		return x->val < y->val ? 1 : -1;

	return x->id < y->id ? -1 : (x->id > y->id);
}

int obs_scan(const char *root, const char *sub, const char *file,
	     const char *metric, struct obs_row *rows, unsigned max)
{
	struct obs_kv kv[OBS_KV_MAX];
	struct dirent *e;
	char dir[256];
	unsigned n = 0;
	DIR *d;
	int len;

	len = snprintf(dir, sizeof(dir), "%s/%s", root, sub);
	if (len < 0 || (size_t)len >= sizeof(dir))
		return -1;

	d = opendir(dir);
	if (!d)
		return -1;

	while ((e = readdir(d)) && n < max) {
		unsigned id;
		unsigned long long v;
		int got;

		if (obs_id(e->d_name, &id))
			continue;

		got = obs_load(root, sub, id, file, kv, OBS_KV_MAX);
		if (got < 0 || obs_u64(kv, (unsigned)got, metric, &v))
			continue;

		rows[n].id = id;
		rows[n].val = v;
		n++;
	}
	closedir(d);

	return (int)n;
}
