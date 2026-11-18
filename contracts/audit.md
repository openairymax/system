# 审计与审批契约（audit）

本文件固定两件事：一条高危动作凭什么被放行，以及一次执行留下什么可关联的
记录。审批回答「这条完全确定的 argv 是否被 root 预先许可」；审计回答「谁在
哪个会话、因为哪条批准、执行了哪一条命令、结果是什么」。

运行时格式（目录、权限、匹配写法）见
[`APPROVAL.md`](../tui/crates/airy-tui-exec/APPROVAL.md)；本文只定义契约字段
与判定语义，两者冲突时以本文为准。

## 1. 审批记录

审批不是布尔值，而是随意图传入的一条记录；它只对同一条 `trace_id` 有效，
`scope` 必须覆盖实际动作，`expires_ms` 过期即作废。

```contract
[fields]
approval.id = required
approval.by = required
approval.ts = required
approval.scope = required
approval.trace_id = required
approval.expires_ms = optional
```

`approval` 整体可省略（见 [`intent.md`](intent.md)）；省略表示没有审批凭据，
高危动作因此被拒绝，而不是视为「无需审批」。缺失、过期与不可信三种原因
都必须可区分，写进审计行的 `reason` 列。

```text
reason = no_approval | approval_expired | approval_insecure
```

三者统一映射为状态 `approval-required`、返回码 `-EACCES`（`-13`）。
不可信包括：条目不是普通文件、权限不符、未知键、重复键、`match` 缺失或
摘要格式不合法。**宁可否决，不可放宽**。

## 2. 审批范围

只有白名单内的九个入口可以被批准；每个入口只接受注册动作生成的固定
argv，发起方不能追加或改写实参。名单与顺序由代码常量给出：

```contract
[programs]
airy-bright
airy-deb
airy-disk
airy-model
airy-pkg
airy-power
airy-probe
airy-rt-cap
airy-update
```

摘要口径是「入口名与全部实参按 `\0` 连接后的 SHA-256」，小写十六进制，
不含环境变量、工作目录、用户与时间。一条批准只覆盖一条 argv。

## 3. 执行记录

一次执行至少留下下列字段，缺任一字段的记录不可用于归因：

```contract
[outcome]
trace = required
status = required
errno = required
advice = required
code = optional
reason = optional
approval = optional
stdout = required
stderr = required
```

- `trace` 与意图的 `trace_id` 相同，是接入、审批与执行三处的关联键。
- `status` 与 `errno` 取自 [`intent.md`](intent.md) §4 的失败语义表，执行面
  不得另造状态串。
- `reason` 只取 §1 的三个值；非审批失败时为空。
- `approval` 是命中的批准文件名，只写文件名，不写目录。
- `stdout`／`stderr` 截断到管道上限，截断事实独立可读，不与内容混淆。

## 4. 拒绝原因

拒绝原因的三个取值与代码枚举逐条对应：

```contract
[reasons]
no_approval
approval_expired
approval_insecure
```

## 5. 不变量

1. **同一追踪号**：审批、执行与审计三处的 `trace_id` 必须相同；重试复用
   旧追踪号，但必须重新审批。
2. **动作在范围内**：`approval.scope` 不含实际动作即拒绝，不做前缀或通配
   匹配。
3. **执行前判定**：审批在拉起子进程之前完成；失败不得留下部分副作用。
4. **只读目录**：`amuser` 对批准目录只读，写入由内核返回 `EACCES`；程序
   不回写该目录，也不缓存批准。
5. **无交互审批**：引导期 TUI 不占 logind 会话，polkit 的 `allow_active`
   判定不成立；会话内审批随 dock 侧提供，不在本契约内。
6. **失败可归因**：任何拒绝都要能回答「哪一条记录、哪个字段、哪一步」，
   不得只返回一个布尔值。
