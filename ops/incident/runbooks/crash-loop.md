# 崩溃循环处置

适用：核心守护进程反复重启、Agent 反复退出、单元进入 failed 状态。

## 判定

1. 确认重启次数与最近一次退出码：

   ```sh
   systemctl show -p NRestarts -p ExecMainStatus -p ActiveState <unit>
   ```

2. 取最近 200 行日志，先看最后一段再回溯首个错误：

   ```sh
   journalctl -u <unit> -n 200 --no-pager
   ```

3. 核对是否已有同类事件与告警：

   ```sh
   airy-alert list
   airy-incident list --state RESPONDING
   ```

## 处置

1. 登记事件。5 分钟内重启 3 次及以上按 P0，否则按 P1：

   ```sh
   airy-incident create --severity P0 --category system \
     --title "<unit> 反复重启" --source macro_d \
     --daemon <unit> --detail "5 分钟内重启 <n> 次"
   ```

2. 分类后转入响应，暂停自动重启，避免把故障放大成雪崩：

   ```sh
   airy-incident triage <id> --detail "确认重启计数超限"
   airy-incident respond <id> --detail "暂停自动重启并保留现场"
   systemctl stop <unit>
   ```

3. 保留现场：核心转储与日志先落盘再重启：

   ```sh
   coredumpctl list <unit>
   coredumpctl info <unit> > /var/lib/agentrt/incidents/<id>.dump.txt
   ```

4. 修正后单次拉起并记录动作：

   ```sh
   systemctl reset-failed <unit>
   airy-incident act <id> --name restart --result success
   ```

5. 观察就绪状态，稳定后转入恢复：

   ```sh
   systemctl show -p ActiveState -p SubState <unit>
   airy-incident recover <id> --detail "连续 <n> 分钟无重启"
   ```

## 升级

- 重启后仍退出：保留现场并转 ESCALATED，由人工判定是否进入降级模式。
- 核心守护进程不可用：按 P0 通知值班，先降级保核心功能，再定位根因。

## 验证

```sh
systemctl is-active <unit>
systemctl show -p NRestarts <unit>
airy-alert list
```

## 复盘要点

- 首次失败的真实错误是什么，重启只是放大了它。
- 重启上限与退避策略是否与故障形态匹配。
- 现场是否完整保留，能否复现。
