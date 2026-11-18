# 语音网关 socket 契约（权威）

本文件是 `asr-gateway` 与 `boot-tui` 之间唯一的进程接口。实现见
`src/ipc.rs`；任何字段、序号或状态语义的改动必须同时改这里，否则视为未评审
的协议变更。接口册来源：`docs-linux/30-interfaces/11-input-ingress.md`
§3.2、§3.3、§7、§10.2；任务方案 §14.7、§14.13。

## 1. 传输

| 项 | 约定 |
|---|---|
| 路径 | `/run/airy/asr.sock`（`SOCKET`），与 `/run/airy/ime.sock` 同目录 |
| 形态 | Unix domain socket + JSON Lines，一连接一会话 |
| 帧上限 | 单条 ≤ 64 KiB（`MAX_FRAME`），不含行尾换行；超限判错并关闭连接，不截断 |
| 编码 | UTF-8；非 UTF-8 判 `Json` |
| 设备 | 只由网关打开录音设备；`VoiceSource` 不读 PCM |

会话标识由客户端在 `open` 中给出，之后每帧必须一致，否则 `Foreign`。

## 2. 请求（客户端 → 网关）

| `msg` | 语义 | 允许阶段 |
|---|---|---|
| `open` | 建立/确认会话 | 任意未关闭阶段；重复发送为空操作 |
| `start` | 开始一次采集 | `Idle`；`Recording` 时回 `busy` |
| `stop` | 结束采集并请求识别 | 仅 `Recording`；否则 `Phase` |
| `cancel` | 放弃当前采集 | 任意未关闭阶段；非采集期为空操作 |
| `close` | 关闭会话 | 任意未关闭阶段；之后所有帧回 `Closed` |

字段：`session`（非空字符串）、`seq`（u64）、`msg`。多余字段忽略，缺失判
`Json`。

## 3. 应答（网关 → 客户端）

应答的 `seq` 必须回显它所回答的请求序号。

| `msg` | 附加字段 | 语义 |
|---|---|---|
| `ready` | 无 | 会话就绪或采集已开始 |
| `result` | `text`、`confidence`、`audio{path,digest,duration_ms}` | 识别结果；进入可编辑缓冲区，不执行 |
| `fail` | `status`、`detail` | 显式失败；`status` 即状态栏取值 |
| `closed` | 无 | 会话已关闭 |

`result` 在解码侧重新走一遍 `AudioRef` / `Transcript` 的构造校验，不信任对端。
`fail.status` 只接受 `unavailable` / `busy` / `empty`；`ready` 与未知取值一律
判 `Field`。

## 4. 序号

- 同一会话内 `seq` 严格递增；每帧成功处理后才前移。
- `seq <= 已处理序号` 判 `Stale`，不重放。**与 IME 的丢弃重放不同**：语音
  采集不是幂等操作，重放一条 `start` 会录出第二段音频，宁可报错也不制造
  用户没说过的内容。
- 处理失败（`Phase`）不前移序号，客户端可用同一 `seq` 修正后重试。

## 5. 状态机

```
Open ──start(可用)──▶ Recording ──stop──▶ 待识别
  ▲                        │                  │
  └────cancel(Idle)◀───────┘     submit(Ok) ──┴──▶ Idle → ready / result
                                          Err ──▶ Idle → fail(status)
Close ──▶ Closed（终态）
```

采集完成后的识别结果由网关经 `Session::submit` 交回，`seq` 必须等于触发
采集的 `stop` 序号；不匹配判 `Phase`。状态机不触碰设备，因此可在无声卡、
无模型的机器上完整测试。

## 6. 降级映射（§14.13）

| 情形 | 应答 | 状态栏 |
|---|---|---|
| 模型缺失或校验失败 | `fail{status:"unavailable"}` | `asr=unavailable` |
| 录音设备被占用 | `fail{status:"busy"}` | `asr=busy` |
| 识别结果为空 | `fail{status:"empty"}` | `asr=empty` |

三种取值均由 `AsrError::availability` 与 `Availability` 给出，调用方不得
自造字符串。除这三种以外的故障（帧损坏、序号回退、会话不符）属于协议错误，
不冒充降级状态。

## 7. 模型校验

`manifest.json` 的 `name`、`version`、`sha256`、`bytes` 四项见 `src/capture.rs`；
摘要与实物不符时网关以 `unavailable` 拒绝采集，不带着半个模型继续跑。

## 8. 设备独占

网关在开始服务前用 `Lease::claim` 认领录音设备（`/dev/snd/*`）：打开设备后取
非阻塞排他 `flock`。第二个网关实例拿到 `EWOULDBLOCK` 即报 `busy`，不得两个实例
轮流读同一路 PCM。锁随文件描述符存在，进程退出（含崩溃）由内核释放，因此不需要
锁文件，也没有陈旧锁回收问题。

打开失败（设备不存在、权限被撤销）报 `unavailable`；这与模型缺失共用同一个
用户可见状态，但审计上仍可从 `detail` 区分。

租约只约束网关之间；与其它程序的设备共享由 ALSA 与设备权限决定。
