# 存储耗尽处置

适用：数据分区接近写满、inode 耗尽、文件系统转为只读、台账或日志无法落盘。

## 判定

1. 同时看容量与 inode，两个维度任何一侧耗尽都会写失败：

   ```sh
   df -h /var/lib/agentrt
   df -i /var/lib/agentrt
   ```

2. 确认是否已经转为只读，只读挂载会让写入以 EROFS 失败：

   ```sh
   mount | grep -E "ro,|,ro"
   dmesg | grep -i -E "read-only|ext4-fs error"
   ```

3. 取磁盘模块结论，先判读再清理：

   ```sh
   airy-disk status
   airy-disk health
   journalctl -u vfs_d -n 100 --no-pager
   ```

## 处置

1. 登记事件。核心分区只读或台账无法落盘按 P0：

   ```sh
   airy-incident create --severity P0 --category resource \
     --title "数据分区空间不足" --source vfs_d \
     --detail "使用率 <x>%，inode 使用率 <y>%"
   ```

2. 先冻结非关键写入，避免清理与写入互相踩踏：

   ```sh
   airy-incident respond <id> --detail "限制非关键写入"
   airy-incident act <id> --name freeze-write --result success
   ```

3. 从保留期最长的对象开始清理，先归档后删除，不直接删活动台账：

   ```sh
   airy-incident prune --days 90
   airy-incident prune --days 90 --apply
   journalctl --vacuum-size=200M
   ```

4. 已转只读时先确认数据完整，再以只读方式保留现场：

   ```sh
   airy-incident act <id> --name readonly --result success
   ```

5. 空间恢复且写入正常后再转恢复：

   ```sh
   airy-incident recover <id> --detail "写入恢复，空间回落"
   ```

## 升级

- 文件系统报元数据错误或反复转只读：转 ESCALATED，按 P0 通知值班并保留现场。
- 清理后仍无法满足保留期要求：转人工评估扩容。

## 验证

```sh
df -h /var/lib/agentrt
df -i /var/lib/agentrt
airy-disk health
```

## 复盘要点

- 增长来自日志、台账、制品还是异常写入。
- 保留期与配额是否与真实规模匹配。
- 只读发生后是否及时冻结写入，数据是否受损。
