# [2026-09-13] /status 上下文展示增强与 /compact 手动压缩指令

> 本文件为独立变更日志，记录 2026-09-13 一次对话状态与上下文管理改动：
> `/status` 增加当前上下文占用、历史轮数与 k/M 格式化累计消耗；
> 新增 `/compact` 手动触发上下文压缩（LLM 摘要优先，支持保留最近 N 轮，
> 无压缩模型时不自动回退截断）；
> `provider_stats` 落库最近一次模型返回的上下文占用。

---

## 问题现象

1. `/status` 显示「总计: 2,936,059」，用户对话并不长却像有 290 万 token；
   实际该数字是本对话**累计 API 计费消耗**（工具循环反复把历史当输入发出、
   含缓存命中），不是当前上下文长度，文案与格式（无 k/M）都容易误解；
2. 无法从指令侧看到「当前上下文大概多大」；
3. 上下文压缩只能等占用超过阈值自动触发，没有手动指令；
4. `/reset` 清空历史后，`provider_stats` 按同一 `conversation_id` 继续累加，
   累计消耗不会清零（本次仅澄清展示，不改累加语义）。

## 根因 / 设计说明

### /status 口径

- 旧实现对 `provider_stats` 按 `conversation_id` 做 `SUM(token_input_*)`，
  含义是「本对话累计消耗」；
- 真正的「当前上下文」是最近一次 LLM 请求的 `usage.input`（`AgentStats.current_context_tokens`），
  4.27.22 已用它做压缩判断快照，但未落库，空闲时查不到；
- 本地估算器 `EstimateTokenCounter`：中文约 0.6 token/字、其他约 0.3 token/字，
  图片/音频固定估值，不含 system prompt 与工具 schema。

### 压缩链路

- 压缩走统一 `ContextCompressor`（`LLMSummaryCompressor` / `TruncateByTurnsCompressor`），
  由 `ContextManager` 在自动路径调用；`/compact` 复用同一套处理器，不另写实现；
- `LLMSummaryCompressor` 原先只有 `keep_recent_ratio`（token 比例，钳制 0–0.3），
  没有「保留最近 N 轮」的轮次粒度控制。

## 改动内容

### `/status`（`conversation.py`）

- Token 数字改为 k / M 格式（如 `2.94M`、`938.24k`）；
- 新增「当前上下文」，取数优先级：
  1. Agent 运行中：runner 内存里的 `current_context_tokens`（来源标注「模型返回」）；
  2. 空闲：最近一条 `provider_stats.current_context_tokens > 0` 的记录（「模型返回」）；
  3. 都没有：历史消息本地估算（「历史消息估算」）；
- 新增「历史消息轮数」：按 `split_into_rounds` 统计（一轮 = 某条 user 起到下一条 user 前的 assistant/tool）；
- 累计消耗文案改为「累计消耗」，展示：
  - 总计
  - 输入（缓存 + 其他）
  - 输入缓存
  - 输出

### `/compact`（`conversation.py` + `main.py`）

- 新增指令 `/compact [yes] [保留最近N轮]`：
  - `/compact`：仅 LLM 摘要压缩；
  - `/compact yes`：允许在无可用压缩模型时回退按轮次截断；
  - `/compact 3`：LLM 摘要并保留最近 3 轮原文；
  - `/compact yes 3`：允许截断回退 + 保留最近 3 轮；
- **不自动回退截断**：无 LLM 压缩模型或摘要失败时提示改用 `/compact yes`；
- 第三方 Agent（dify/coze/dashscope/deerflow）直接拒绝；
- 执行前 `stop_all` 停掉本会话活跃 Agent，避免压缩中历史被写回；
- 成功后回复方法、轮数变化与估算占用变化。

### `LLMSummaryCompressor`（`compressor.py`）

- 新增 `keep_recent_rounds: int | None`：
  - 设置且 >0：保留最近 N 轮原文，更早轮次进摘要；
  - 未设置：仍走原 `keep_recent_ratio` 逻辑。

### 落库（`po.py` / `sqlite.py`）

- `ProviderStat` 新增 `current_context_tokens`（最近一次 LLM 请求 input tokens）；
- 启动时 `PRAGMA table_info` + `ALTER TABLE provider_stats ADD COLUMN` 平滑升级；
- `insert_provider_stat` 从 `stats["current_context_tokens"]` 写入；
- 写入源即 `AgentStats.to_dict()` 中已有字段，runner 侧无需再改。

## 修改文件清单

1. `astrbot/builtin_stars/builtin_commands/commands/conversation.py`
2. `astrbot/builtin_stars/builtin_commands/main.py`
3. `astrbot/core/agent/context/compressor.py`
4. `astrbot/core/db/po.py`
5. `astrbot/core/db/sqlite.py`

## 验证

- `py_compile` 通过：`conversation.py`、`main.py`、`compressor.py`、`po.py`、`sqlite.py`；
- 本地手测 `_format_tokens`：`0/999/1000/12345/2936059/938240/16971` →
  `0/999/1.00k/12.35k/2.94M/938.24k/16.97k`；
- `_estimate_history_context_tokens`：空列表/非法 JSON 返回 0；正常多轮历史返回正估算值。

## 部署/测试注意

- 需重启 ldm 使新指令与 DB 列迁移生效；
- 存量 `provider_stats` 行 `current_context_tokens` 为 0，首次 `/status` 在空闲且
  无新完成请求时会退回「历史消息估算」；跑过一轮对话后即有「模型返回」值；
- 累计消耗仍按对话 ID 累加，`/reset` 不清零；`/new` 新对话 ID 后从零开始；
- 自动压缩路径未改行为，仅手动 `/compact` 与 `/status` 展示受影响。
