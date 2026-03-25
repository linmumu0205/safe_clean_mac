# 安全版 Mac 清理脚本（可复用）

脚本文件：`safe_mac_clean.sh`

## 设计目标（安全优先）
- 默认只预览：不带 `--apply` 时不会删除或移动任何文件。
- 仅清理白名单目录：只处理常见缓存/日志目录，不碰文档、桌面、下载、图片等个人数据目录。
- 真正执行时也不“永久删除”：只把文件移动到 `~/.Trash` 的会话子目录。
- 二次确认：执行模式下会显示预计文件数和空间占用，并要求你确认。
- 记录日志：每次执行会写入 `~/safe-clean-时间戳.log`。
- 扫描有进度：大目录时会持续输出进度，避免“看起来卡住”。

## 会扫描的目录（默认）
- `~/Library/Caches`
- `~/Library/Logs`
- `~/Library/Application Support/CrashReporter`
- `~/Library/Developer/CoreSimulator/Caches`

可选目录（需手动开启）
- `--include-container-caches`：`~/Library/Containers/.../Data/Library/Caches`
- `--include-xcode`：`~/Library/Developer/Xcode/DerivedData`

## 推荐使用流程
1. 先预览（最安全）
```bash
./safe_mac_clean.sh
```
2. 如果结果合理，再执行移动到回收站
```bash
./safe_mac_clean.sh --apply
```
3. 想更保守：只清理更久之前的文件（如 60 天）
```bash
./safe_mac_clean.sh --apply --days 60
```

## 性能相关参数（解决“大量缓存看起来卡住”）
- `--progress-every N`：每匹配 N 个文件打印一次进度（默认 5000）
- `--max-candidates N`：最多扫描 N 个匹配文件后提前停止（默认 0 不限制）

推荐大目录先这样跑：
```bash
./safe_mac_clean.sh --days 30 --progress-every 1000 --max-candidates 50000
```

## 常用命令
- 预览 + 显示每个文件（文件很多时会非常长）：
```bash
./safe_mac_clean.sh -v
```
- 执行 + 跳过确认（建议你非常确认时再用）：
```bash
./safe_mac_clean.sh --apply -y
```
- 启用更多缓存清理：
```bash
./safe_mac_clean.sh --apply --include-container-caches --include-xcode
```

## 回滚/恢复
脚本只是把文件移动到回收站：
- 打开 Finder 的“废纸篓”，可直接还原。
- 脚本会把本次清理放在 `~/.Trash/safe-clean-时间戳/` 下面，方便你按批次检查。

## 建议
- 第一次先运行：`./safe_mac_clean.sh --days 60 --progress-every 1000`
- 如果你缓存非常多，先加 `--max-candidates 30000` 分批处理。
- 观察一两天没问题后，再改成 `--days 30`。
