# `airy.intent/1` 契约（intent）

本文件是 `airy.intent/1` 信封的唯一契约文本。字段、取值集合、校验顺序与
失败语义都以这里为准；`system/tui/crates/intent` 的模型与常量表是它的
代码投影，两边由 `ci/gates/contract-conform.sh` 逐项对照，任一侧先改即判负。

机读部分写在 `contract` 代码块里，块外是说明。块内语法只有三种：
`[小节]`、`键 = 值`、每行一个值；`#` 开头为注释，空行忽略。

## 1. 信封字段

必填指「父对象存在时该字段必须出现」。`optional` 表示可以省略：省略后由
消费方取缺省值，而不是按空值处理。未知字段一律忽略（§6 兼容策略），因此
这里的表不构成封闭集合，但表中列出的字段名与类型不得改名。

```contract
[meta]
schema = airy.intent/1
trace_hex_len = 32

[fields]
schema = required
trace_id = required
ts = required
source = required
source.kind = required
source.device = optional
trust = required
trust.level = required
trust.principal = required
session = required
session.mode = required
session.id = required
payload = required
payload.kind = required
payload.text = optional
payload.lang = optional
payload.structured = optional
payload.structured.action = required
payload.structured.args = optional
payload.structured.expect = optional
payload.structured.expect.schema = required
payload.audio_ref = optional
payload.audio_ref.path = required
payload.audio_ref.digest = required
payload.audio_ref.duration_ms = required
constraints = optional
constraints.deadline_ms = optional
constraints.allow = optional
constraints.deny = optional
approval = optional
```

`approval` 内部的字段属于审计记录，写在 [`audit.md`](audit.md)；本表只声明
「审批凭据整体可省略」。两处分工是为了让审计面单独改版时不必触碰信封。

`trace_id` 是 32 位十六进制文本，全链路唯一；重试复用，新请求不得复用。
`payload.kind` 决定哪个载荷字段必填：`text` 要 `payload.text` 非空，
`structured` 要 `payload.structured.action` 非空，`audio-ref` 要
`payload.audio_ref` 齐备且 `digest` 带 `sha256:` 前缀。

## 2. 取值集合

四个集合的线格式由 `serde` 声明固定；机读块里的值与代码里的
`#[serde(rename…)]` 必须逐字相同，不接受同义词。

```contract
[enum:source]
keyboard
voice
api
batch

[enum:trust]
T0
T1
T2
T3

[enum:payload-kind]
text
structured
audio-ref

[enum:session-mode]
tui
gui
headless
```

## 3. 来源与信任级别

每个来源只落在固定级别上，判定在适配器完成；组合层复核但不改写，界面
也不得提升。级别不足返回 `denied`，附加字段写「所需级别」。

```contract
[trust]
keyboard = T0
voice = T0
api = T1 T2
batch = T3
```

`api` 的身份由 `SO_PEERCRED` 决定，故允许 T1 与 T2 两档；`batch` 固定
T3，不接受提升。

## 4. 校验顺序与失败语义

校验按下列顺序执行，**首个不通过的步骤决定返回值**；不得为了给出更友好
的提示而跳过前面的步骤。

| 步 | 判据 | 归属 | 失败类别 |
|---|---|---|---|
| 1 | 语法可解析、必填字段齐备、`schema` 相符 | 文档模型 | `invalid` |
| 2 | `trace_id` 在会话内未被使用 | 会话层 | `invalid` |
| 3 | `ts`、`trust.principal`、`session.id` 非空 | 文档模型 | `invalid` |
| 4 | 来源与信任级别配对 | 文档模型 | `denied` |
| 5 | `constraints.deny` 未命中且 `constraints.allow` 放行 | 路由 | `denied` |
| 6 | 动作已注册 | 执行侧 | `unsupported` |

```contract
[errors]
invalid -22
unsupported -22
denied -13
denied -1
approval-required -13
timeout -110
busy -11
unavailable -95
internal -5
```

同一个状态串可以对应多个返回码：`denied` 的 `-EACCES` 是级别不足，
`-EPERM` 是黑名单命中。消费方按 `status` 分支、把 `errno` 原样透传，
不自行改写。

## 5. 示例

```json
{
  "schema": "airy.intent/1",
  "trace_id": "9f2c1b7e4d6a4c0e8b3f5a1d2c7e9b40",
  "ts": "2026-11-17T10:02:11+08:00",
  "source": {"kind": "keyboard", "device": "tty1"},
  "trust": {"level": "T0", "principal": "console"},
  "session": {"mode": "tui", "id": "s-0f31"},
  "payload": {"kind": "text", "text": "查看磁盘使用情况", "lang": "zh-CN"},
  "constraints": {"deadline_ms": 5000, "deny": ["disk.format"]}
}
```

## 6. 兼容性

- MAJOR 变更才改 `schema` 值；本契约的 MAJOR 为 1。
- 新增字段必须可选；旧消费方忽略未知字段，不得因此拒绝整条意图。
- 字段改名、类型变更、取值集合增删都按 MAJOR 处理，不做静默兼容。
- `optional` 字段的缺省值由契约固定：`source.device=local`、
  `payload.lang=zh-CN`、`constraints.deny=[]`，其余可选项缺省为未设置。
