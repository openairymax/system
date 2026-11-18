# 审批文件契约（approvals.d）

本文是引导期 TUI 执行面的审批契约，与 0.0.5 §14.9 一一对应。
审批回答一个问题：**这条完全确定的 argv 是否被 root 预先许可**。
它不解释意图、不做模式匹配、不保存会话状态。

## 1. 位置与权限

| 对象 | 要求 |
|---|---|
| 目录 | `/etc/airymaxos/system/tui/approvals.d`，`root:root`，`0755` |
| 文件 | 每个动作一个 `<action>.conf`，`root:root`，`0644` |
| 条目类型 | 必须是普通文件；符号链接、目录、设备一律不可信 |

目录不存在等同于「没有任何批准」，按 `no_approval` 拒绝，而不是报错。

`amuser` 对该目录只读；向目录写入会由内核返回 `EACCES`，这是权限
事实，不由本程序判定。

## 2. 文件格式

```text
# 允许把系统盘扩到整块设备（示例）
match = "sha256:<64 位小写十六进制>"
expires = 1790000000
```

- 空行与 `#` 开头的行忽略。
- `match` 必填；值必须带 `sha256:` 前缀，长度 64，字符为十六进制。
  引号可省略，读取时会去掉首尾引号。
- `expires` 可选，Unix 秒；`now > expires` 即拒绝。等于 `expires` 的
  那一秒仍然有效。
- 未知键、重复键、缺 `match`、数值解析失败均按不可信处理。

## 3. 摘要口径

摘要是**入口名与全部实参按 `\0` 连接**后的 SHA-256，输出小写十六进制：

```text
sha256("airy-disk" "\0" "grow" "\0" "/dev/sda")
```

- 不含环境变量、工作目录、用户与时间。
- 入口名是 `/usr/bin` 下的文件名（白名单内的 9 个之一），不带路径。
- 一条批准只覆盖一条 argv；换参数、换顺序都算另一条命令。
- 可以用 `printf '%s\0' airy-disk grow /dev/sda | sha256sum` 复算。

## 4. 判定顺序与拒绝原因

1. 扫描目录，逐个条目校验权限与类型；任一不符即整条链不可信。
2. 没有 `<action>.conf`，或摘要不相等 → `no_approval`。
3. `expires` 已过 → `approval_expired`。
4. 以上都通过才放行，并在结果里带回命中的文件名。

三种拒绝都映射为状态 `approval-required`、返回码 `-13`（`EPERM`），
`reason` 列区分具体原因，供审计行记录。任何解析错误都归入
`approval_insecure`——**宁可否决，不可放宽**。

## 5. 安装示例

```sh
install -d -o root -g root -m 0755 /etc/airymaxos/system/tui/approvals.d
printf 'match = "sha256:%s"\n' \
  "$(printf '%s\0' airy-disk grow /dev/sda | sha256sum | cut -d' ' -f1)" \
  > /tmp/disk.grow.conf
install -o root -g root -m 0644 /tmp/disk.grow.conf \
  /etc/airymaxos/system/tui/approvals.d/disk.grow.conf
rm -f /tmp/disk.grow.conf
```

## 6. 不在本契约内

- 通配批准（如 `dnf install *`）、一次性凭据、超时弹窗。
- 交互式审批：引导期 TUI 不占 logind 会话，polkit 的 `allow_active`
  判定不成立；会话内审批随 dock 侧提供。
- 审批的增删由管理员完成，程序只读，不回写该目录。
