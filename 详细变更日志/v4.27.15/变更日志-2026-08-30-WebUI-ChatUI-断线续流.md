# [2026-08-30] WebUI ChatUI 断线续流

## 问题现象

### LLM 聊天断线丢回复

ChatUI 发消息后离开页面再回来，AI 回复丢失。

**根因**：旧架构中 `build_chat_stream` 的 `stream()` 生成器同时消费 `back_queue` 和推送 SSE——消费者和推送者耦合在一起。断连时 `finally` 只 flush 已累积片段（半截或空），删 `back_queue`；LLM 后续输出写进新建的孤儿 `back_queue` 无人消费，永不落库。

### 生图会话断线丢图片

生图会话发消息后离开页面再回来，看不到生成中的进度；生成完成后要手动刷新才能看到图片。

**根因**：生图是同步 HTTP POST 请求，后端 `generate_image_in_session` 阻塞在 `provider.generate_image()` 调用。用户离开时 bot 消息还没落库，回来看到空白；完成后前端不知道要刷新。

## 修复内容

### LLM 聊天：ChatRunState 断线续流（参照官方 PR #9259）

将 `back_queue` 消费和 SSE 推送解耦：

- **ChatRunState** 数据类：持有 `run_id`、`back_queue`、`subscribers`（set[asyncio.Queue]）、`message_parts`、`agent_stats`、`refs`、`revision`、`status`、`task`
- **`_consume_chat_run`**：后台 asyncio.Task，独立消费 `back_queue` → 累积 → 落库 → `_publish_chat_run` 扇出给所有 subscriber。订阅者随时离开或重连都不影响消费
- **`_subscribe_chat_run`**：创建 subscriber Queue，`include_snapshot=True` 先发已累积的完整快照，再推送增量事件
- **`build_chat_stream`**：创建 ChatRunState → 启动消费 task → 返回订阅者流（不含快照）
- **`build_chat_run_stream`**：断线续流入口 → 找到活跃 run → 返回订阅者流（含快照）
- **`get_session` / `get_thread`**：返回 `active_runs` 列表
- **`delete_session_internal`**：取消活跃 run task

**前端**：
- `loadSessionMessages` 读 `payload.active_runs` → 调 `restoreNextActiveRun`
- `restoreNextActiveRun` 从快照构建 botRecord，移除同 checkpoint 旧 bot 记录 → 调 `startResumeStream`
- `startResumeStream` 连接 `GET /api/v1/chat/runs/{runId}/stream`，5 次指数退避重试
- `processStreamPayload` 处理 `run_snapshot` 类型：用 snapshot content 替换 botRecord

### 生图会话：运行中标记 + 轮询

- **后端**：`generate_image_in_session` 开始时标记 `running_convs[session_id] = True`，完成（成功/失败）时 `finally` 清除。这样 `get_session` 返回 `is_running: true`
- **前端**：`loadSessionMessages` 检测到 `is_running=true` 但没有 `active_runs`（非 LLM 流），且最后一条是 user 消息（bot 还没落库）→ 插入本地"生成中"占位 botRecord + 启动 5 秒间隔轮询 `get_session`，检测到 bot 消息落库后替换占位
- 占位文案区分：生图会话显示"生成中..."，聊天会话显示"思考中..."

### 切换会话闪屏修复

切换到已加载的会话时，如果没有活跃连接就重新加载一次（同步后台可能已落库的新消息）。但点击当前正在进行的会话时不重新加载，避免消息列表清空重建导致闪屏。

## 修改文件清单

### 后端

| 文件 | 改动 |
|------|------|
| `astrbot/dashboard/services/chat_service.py` | 新增 ChatRunState 数据类、chat_runs/chat_runs_by_session 字典、get_active_chat_runs/_publish_chat_run/_subscribe_chat_run/build_chat_run_stream/_consume_chat_run 方法；重构 build_chat_stream 解耦消费和推送；get_session/get_thread 返回 active_runs；delete_session_internal 取消活跃 run task；generate_image_in_session 标记/清除 running_convs |
| `astrbot/dashboard/api/chat.py` | 新增 `GET /chat/runs/{run_id}/stream` 路由 |

### 前端

| 文件 | 改动 |
|------|------|
| `dashboard/src/api/v1.ts` | 新增 `resumeRunStreamUrl(runId)` 方法 |
| `dashboard/src/composables/useMessages.ts` | 新增 ActiveChatRun 类型、restoreNextActiveRun、startResumeStream、startImageGenerationPolling、processStreamPayload 处理 run_snapshot、loadSessionMessages 读 active_runs + 生图轮询、cleanupConnections 清理定时器 |
| `dashboard/src/components/chat/Chat.vue` | stop 后不续流；切换会话时同会话不重新加载 |
| `dashboard/src/components/chat/ChatMessageList.vue` | isLoading 占位文案区分生图/聊天 |
| `dashboard/src/i18n/locales/zh-CN/features/chat.json` | 新增 `message.imageGenerating` = "生成中..." |

## 验证

- py_compile + ruff 通过
- 前端构建部署通过
- 保留魔改特性：can_regenerate / conversation history reload / sort_history_by_turn / 落库点中文日志 / has_pending_tool_calls 推迟落库 / enable_streaming flags

## 部署

已执行 `bash ~/构建部署.sh`，需手动重启 ldm 生效。
