# [2026-09-17] /status 与 /compact 跨会话操作：支持群号/QQ号/昵称/群名简写，回执数据改用模型返回

> 本文件为独立变更日志，记录 2026-09-17 一次上下文管理指令增强：
> `/status` 与 `/compact` 支持跨会话操作（会话定位与 /persona 同一套简写），
> `/compact` 回执的占用数据从本地估算改为模型返回优先、估算显式标注。

---

## 背景与问题现象

1. 管理员想看某个群（或 WebChat 线程）的上下文占用、累计消耗，或想压缩某个
   群的对话历史时，`/status`、`/compact` 只能操作「发出指令的当前会话」，
   必须切到那个会话里才能操作；
2. 即使加上跨会话参数，最初版本要求输入完整的
   「平台ID:消息类型:会话号」（如 `aiocqhttp:GroupMessage:123456`），
   又长又容易抄错，操作门槛高；
3. `/compact` 完成回执里的「估算占用」全部来自本地估算，没有利用
   已保存的模型统计与摘要请求返回的 usage：
   - `provider_stats` 保存着该对话最近请求返回的 `current_context_tokens`；
   - 摘要请求返回的 usage 此前没有向指令层透出；
   - 压缩后的完整历史尚未用于新请求，不能直接拿摘要输出 token 数
     当作新历史的完整上下文占用。原回执已标注「估算」，本次调整的是
     数据来源与字段区分，而不是修复未标注估算的问题。

## 改动内容

### 会话定位与 /persona 统一（新增 `astrbot/builtin_stars/builtin_commands/commands/utils/session_target.py`）

- 把 `/persona` 已验证的会话定位逻辑提取为 `SessionTargetResolver` 基类：
  支持完整会话 ID、会话 ID 段、群号、QQ 号、WebChat 线程分段（`!` 拆分）、
  昵称、群名（别称仅精确相等）；
- 昵称/群名命中必须在 conversations 表里有对话，防止只剩昵称残留的死会话
  被定位到；
- `PersonaCommands` 改为继承该基类，`/persona` 行为不变
  （`test_persona_remote.py` 21 个用例全部通过），两处不再各留一份代码。

### `/status`、`/compact` 跨会话（`astrbot/builtin_stars/builtin_commands/commands/conversation.py`）

- 两个指令都新增可选会话参数，定位统一走 `_resolve_target_session`：
  - 完整会话 ID 直连；其余输入走 persona 同款匹配（群号/QQ号/昵称/群名/线程ID）；
  - 未命中 → 「未找到会话「xxx」，请输入对方的群号/QQ号/昵称。」；
  - 同号匹配到多个会话（如同一 QQ 号在不同平台）→ 列出全部候选（带别称），
    不擅自选一个；
  - 权限顺序：先解析后鉴权——解析不到报「未找到会话」，
    命中且目标 ≠ 当前会话时才校验机器人管理员；非管理员不能读取目标
    对话历史、统计或执行压缩，简写解析本身仍需查询会话标识及别称；
- 回执开头新增「会话 ID: 完整UMO」行，跨会话操作结果一目了然；
- `/compact` 数字参数歧义规则（不破坏原有用法）：
  - 数字精确命中已知会话 → 当作会话；
  - 未命中任何会话 → 按原有「保留最近 N 轮」处理；
  - 参数里已有昵称/群名/完整 ID 等非数字目标时，其余数字只作轮数，
    避免把轮数误当第二个目标；
  - `yes`、轮数、会话三者顺序任意。

### `/compact` 回执数据来源（`astrbot/core/agent/context/compressor.py` + `conversation.py`）

- `LLMSummaryCompressor` 新增 `last_usage`：摘要请求的模型返回 usage
  （输入/输出为摘要请求的 API 用量口径，并非新历史的完整占用）；
  请求异常或本次响应未返回 usage 时为 None；
- `/compact` 回执改为：

  ```
  上下文压缩完成（LLM 摘要）
  会话 ID: aiocqhttp:GroupMessage:123456
  轮数: 24 → 6
  压缩前占用: 45.60k（模型返回）     ← provider_stats 最近一次请求的模型返回
  摘要输出: 1.25k（模型返回）        ← 摘要请求 usage.output
  ```

  以上数字仅为格式示例，并非实测数据。没有正数摘要输出用量时，改为显示
  「压缩后占用: …（估算）」，不与「摘要输出」同时出现。

  - 压缩前占用：优先读 `provider_stats` 中最近一条正数记录，缺失才本地估算，
    标注来源（模型返回 / 估算）。该记录是历史请求快照，若期间发生重置、
    删除历史或再次压缩，不能保证它仍等于当前待压缩历史的占用；
  - 摘要输出：有 usage.output 才显示，缺失时整行不出现；
  - 有模型数据时不再显示笼统的「估算占用」行，估算值出现时必带「（估算）」标注。

## 修改文件清单

1. `astrbot/builtin_stars/builtin_commands/commands/utils/session_target.py`（新增）
2. `astrbot/builtin_stars/builtin_commands/commands/conversation.py`
3. `astrbot/builtin_stars/builtin_commands/commands/persona.py`（改为继承基类，行为不变）
4. `astrbot/core/agent/context/compressor.py`
5. `tests/test_session_target_shorthand.py`（新增，18 个用例）
6. `tests/test_llm_summary_compressor_usage.py`（新增，3 个用例）
7. `tests/test_compact_result_usage.py`（新增，4 个用例）
8. `tests/test_command_session_args.py`（新增，2 个用例）
9. `tests/test_compact_command.py`（新增目标隔离测试，调整非数字参数的会话定位回执断言）
10. `astrbot/builtin_stars/builtin_commands/main.py`（指令参数透传与帮助说明）
11. `tests/test_cross_session_commands.py`（新增，跨会话权限、参数顺序、目标统计与空对话回归）

## 验证

- 全量 `pytest tests/ -q` 284 例全过（含 /persona 回归 21 例、/compact 原有 7 例）；
- `ruff check` 对 `conversation.py`、`persona.py`、`utils/session_target.py`
  检查通过；最终对 `conversation.py`、`compressor.py` 检查通过；
- 全量测试输出包含 18 条警告，并非零警告；验证以离线测试为主，未实际向
  在线模型发起压缩请求。

## 部署/使用注意

- 纯 py 改动，未跑前端构建；需手动同步源码并重启 ldm 生效；
- 使用方式：

  ```
  /status 123456                  群号/QQ号定位
  /status 测试群                   昵称或群名定位
  /status webchat!user1!thread-9  WebChat 线程分段
  /compact 123456 yes 1           参数顺序任意，跨会话压缩
  ```

- 同号多平台歧义时会列出候选（含别称）要求输入更精确的 ID，不会猜；
- 跨会话操作仅机器人管理员可用；简写解析会读取会话标识和别称，
  权限拒绝后不会读取目标对话历史、用量统计或修改历史。
