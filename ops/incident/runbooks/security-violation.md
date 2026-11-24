# 安全违规处置

适用：LSM 拒绝记录异常聚集、越权访问密钥、审计链路被改写、Agent 越界访问。

## 判定

1. 取安全模块结论与拒绝明细：

   ```sh
   journalctl -u sec_d -n 200 --no-pager
   airy-alert list
   ```

2. 确认违规进程的身份与所属 Agent：

   ```sh
   ps -o pid,ppid,uid,cmd -p <pid>
   cat /proc/<pid>/attr/current
   ```

3. 核对审计链路是否完整，先判读再处置：

   ```sh
   journalctl -u audit_d -n 100 --no-pager
   ```

## 处置

1. 登记事件。密钥泄露、审计篡改、跨 Agent 越权一律 P0：

   ```sh
   airy-incident create --severity P0 --category security \
     --title "<违规摘要>" --source sec_d --agent <n> \
     --detail "LSM 拒绝 <hook>，目标 <path>"
   ```

2. 先隔离后恢复：冻结进程与挂载点，不急于杀进程，保留现场：

   ```sh
   systemctl stop <unit>
   airy-incident respond <id> --detail "隔离 <unit>"
   airy-incident act <id> --name isolate --result success
   ```

3. 撤销持久化入口，避免重启后复发：

   ```sh
   systemctl disable <unit>
   ```

4. 涉及 Agent 时冻结该 Agent 并迁移其负载，不自动恢复：

   ```sh
   airy-incident act <id> --name freeze-agent --result success
   ```

5. 处置结论由人工确认后再恢复：

   ```sh
   airy-incident recover <id> --detail "隔离完成，等待处置结论"
   airy-incident resolve <id> --root-cause "<根因结论>"
   ```

## 升级

- 涉及宿主机密钥、审计链路或跨租户数据：立即通知安全值班，保持隔离。
- 无法判定是否误报：维持隔离，按 ESCALATED 转人工，不自行放行。

## 验证

```sh
journalctl -u sec_d -n 50 --no-pager
airy-alert history --severity CRITICAL --limit 20
```

## 复盘要点

- 违规入口是配置、权限还是设计缺口。
- 隔离范围是否最小，是否影响其他 Agent。
- 审计记录能否支撑完整还原时间线。
