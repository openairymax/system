# 输入通道契约（input）

本文件固定「四通道 → 一条事件流」的边界：适配器只产 L1 事件，组合层只做
排序与归一化，意图层只认 `airy.intent/1`。三层的职责一旦混写，键盘、语音、
外接 API 与批处理就会各有一套信任判定和错误码，审计也无从对齐。

## 1. 四个通道

| 通道 | 来源 | 信任 | 入口 | 形态 |
|---|---|---|---|---|
| C1 | 键盘 | T0 | `/dev/tty1`，boot TUI 独占 | 键码，不产文本 |
| C2 | 语音 | T0 | `asr-gateway` 的本地会话 | 识别文本 + 置信度 + 音频引用 |
| C3 | 外接 API | T1–T2 | Unix 套接字，JSON Lines | 结构化载荷，身份取 `SO_PEERCRED` |
| C4 | 批处理 | T3 | 标准输入的逐行意图 | 每行一条，固定 T3 |

C1 与 C2 同权：语音结果先落可编辑缓冲区，用户确认后才成为意图，因此
「语音直接执行」不在本契约内。C3 的信任级别由内核凭据决定，不读请求体
里的自称字段；C4 不可提升。

## 2. 三个层级

| 层 | 内容 | 不做什么 |
|---|---|---|
| L0 | 终端的字节流、音频帧、套接字连接 | 不解释语义 |
| L1 | `RawEvent`：追踪号、来源、信任、单调时刻、原始载荷 | 不拼接文本、不判定动作 |
| L2 | `airy.intent/1`（见 [`intent.md`](intent.md)） | 不再改写信任级别 |

L1 的字段与适配器接口是机读契约：

```contract
[l1]
event = RawEvent
field = trace_id
field = source
field = trust
field = at
field = body
adapter = InputSource
method = kind
method = drain
method = close
body = Key
body = Transcript
body = Structured
body = Line
```

`drain(max)` 一次最多取出 `max` 条，空结果表示当前无事件；返回错误时不得
吞掉已取出的部分。`close` 幂等，重复关闭不报错。

## 3. 单调与背压

- **每源单调**：同一来源的事件按产生顺序进入组合层，`at` 不得回退；跨源
  不比较先后，组合层只按各自游标推进。
- **不静默丢弃**：组合队列上限 256 条。达到上限时适配器返回 `busy`
  （`-EAGAIN`），由调用方在下一轮重试；不得丢最旧一条冒充成功。
- **预算优先于公平**：单帧最多消费 64 条（`MAX_BATCH`），余量留到下一帧，
  避免一次事件洪峰把帧预算打穿。

逃逸序列跨读边界时允许暂存：待定缓冲上限 64 字节，超过即丢弃该序列并重新
同步。这个上限是输入语义的一部分，与代码常量同源。

```contract
[limits]
escape_pending = 64
batch_max = 64
```

## 4. 中文输入（C1 扩展）

- 词典格式 `AIRYIME1`：24 字节头 + 12 字节词条，`crc32` 校验，常驻预算
  2 MiB；词典不可用时输入法整体降级，不得只加载半份。
- 会话经 `/run/airy/ime.sock`，JSON Lines，单行 ≤ 64 KiB。
- 词典或会话失败时状态栏显示 `ime=slow`／`ime=off`，**不得**悄悄回落为
  英文直通：用户会以为候选词丢失，而不是输入法不可用。
- 候选与提交只走 L1 的 `Key` 与最终 `text` 载荷；组合层不认识拼音。

## 5. 语音（C2）

识别结果固定为 `Transcript{text, confidence, audio_ref}`：
`confidence` 取 0.0–1.0，`audio_ref` 的 `digest` 带 `sha256:` 前缀。

模型缺失时整条通道返回 `unavailable`（`-ENOTSUP`），状态栏显示
`asr=unavailable`；不得返回空文本冒充识别成功，也不得自动改用云端。

## 6. 结构化输入（C3）

- 单行一条 JSON，按 `airy.intent/1` 解析；未知字段忽略。
- 身份只取套接字凭据：`uid` 映射到 `principal`，级别取 T1 或 T2；请求体
  里的 `trust` 字段不参与判定。
- 语法错误返回 `invalid`，并把首个不通过的字段名写入附加字段。
- 连接关闭前未取走的事件允许留在队列；进程退出即丢弃，不落盘。

## 7. 批处理（C4）

- 逐行一条意图，空行与 `#` 开头忽略；行内不做续行与转义。
- 信任级别固定 T3，`session.mode` 必须为 `headless`。
- 单条失败不终止整批：失败记录带行号写入审计，继续下一行；退出码取首个
  失败对应的 errno。
