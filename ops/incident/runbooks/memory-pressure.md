# 内存压力处置

适用：可用内存持续偏低、发生 OOM、记忆层淘汰跟不上、Agent 被内核杀死。

## 判定

1. 看压力指标与回收行为，不只看瞬时剩余量：

   ```sh
   cat /proc/pressure/memory
   free -m
   vmstat 1 5
   ```

2. 确认 OOM 击杀对象与触发者：

   ```sh
   journalctl -k -n 200 --no-pager | grep -i -E "oom|killed process"
   journalctl -u mem_d -n 100 --no-pager
   ```

3. 核对是否有单一 Agent 长期超配额：

   ```sh
   systemd-cgtop -m -n 1
   airy-incident list --state RESPONDING
   ```

## 处置

1. 登记事件。核心守护进程被 OOM 击杀按 P0，单 Agent 被杀按 P1：

   ```sh
   airy-incident create --severity P1 --category resource \
     --title "可用内存低于阈值" --source mem_d --agent <n> \
     --detail "MemAvailable <x>%，PSI full avg10 <y>"
   ```

2. 先触发主动淘汰，再考虑暂停负载，顺序不可颠倒：

   ```sh
   airy-incident respond <id> --detail "触发记忆层主动淘汰"
   airy-incident act <id> --name reclaim --result success
   ```

3. 仍不足时按优先级暂停最低优先级的 Agent，保留管理与核心 Agent：

   ```sh
   airy-incident act <id> --name pause-agent --result success
   ```

4. 核心功能受影响时进入降级模式，只保留核心守护进程：

   ```sh
   airy-incident act <id> --name dsl-enter --result success
   ```

5. 指标回落后再转恢复，避免抖动反复：

   ```sh
   airy-incident recover <id> --detail "MemAvailable 回到阈值以上"
   ```

## 升级

- 淘汰与暂停都无法回收：转 ESCALATED，按 P0 通知值班。
- 反复在同一 Agent 上发生：转人工评估配额与负载画像。

## 验证

```sh
free -m
cat /proc/pressure/memory
journalctl -u mem_d -n 50 --no-pager
```

## 复盘要点

- 是容量不足、泄漏还是配额失当，三者处置方式不同。
- 淘汰顺序是否命中真正的冷数据。
- 降级期间核心功能是否可用。
