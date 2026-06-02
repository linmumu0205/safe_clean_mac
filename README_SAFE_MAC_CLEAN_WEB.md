# Safe Mac Cleaner Web（本地可视化版）

文件：`safe_clean_web.py`

## 特性
- 本地可视化页面（浏览器访问）
- 后台异步扫描，不会因为大目录导致页面假死
- 扫描预设：标准扫描、重点扫描、开发者扫描
- 扫描结果可视化：候选数量、估算大小、Top 目录、Top 大文件
- 执行前强制确认词：`MOVE_TO_TRASH`
- 清理动作仅移动到 `~/.Trash/safe-clean-web-时间戳/`，不做永久删除
- 生成审计日志：`~/safe-clean-web-时间戳.log`

## 架构（安全优先）
- `CleanerPolicy`：白名单目录策略
- `scan_worker`：扫描引擎（按天数、上限、可选目录）
- `CleanerExecutor`：执行引擎（仅移动到废纸篓）
- `JobStore`：任务状态管理（用于前端轮询进度）
- HTTP API：`/api/scan`、`/api/scan/{id}`、`/api/apply`、`/api/jobs`

## 启动
```bash
cd /Users/linmy/Downloads/appliaction/codex
python3 safe_clean_web.py
```

默认监听：
- `http://127.0.0.1:8765`

可选环境变量：
```bash
SAFE_CLEAN_HOST=127.0.0.1 SAFE_CLEAN_PORT=8765 python3 safe_clean_web.py
```

## 使用流程
1. 打开页面后选择扫描预设
2. 点击“开始扫描”，等待状态变为“扫描完成”
3. 检查扫描摘要、Top 目录和 Top 大文件
4. 输入确认词 `MOVE_TO_TRASH`
5. 点击“执行清理”
6. 在废纸篓检查会话目录，确认后再手工清空废纸篓

## 扫描预设
- 标准扫描：日常保守扫描
- 重点扫描：自动包含 Container 缓存与 Xcode DerivedData
- 开发者扫描：更偏向开发工具缓存，默认天数更短

## 安全边界
默认扫描目录：
- `~/Library/Caches`
- `~/Library/Logs`
- `~/Library/Application Support/CrashReporter`
- `~/Library/Developer/CoreSimulator/Caches`

可选扩展：
- Container 缓存
- Xcode DerivedData

不会扫描：
- 桌面、下载、文档、图片等个人数据目录

## 注意
- 当前任务列表保存在进程内存中，重启服务后会清空（不影响已写入的日志与废纸篓内容）。
- 如果你想下一步升级，我可以把任务历史持久化到 SQLite，并加“恢复/回滚”按钮。
