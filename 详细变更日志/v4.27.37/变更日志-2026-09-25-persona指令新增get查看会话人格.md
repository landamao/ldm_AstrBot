# [2026-09-25] persona 指令新增 get 子指令：查看目标会话的人格配置

> 本文件为独立变更日志，记录 `/persona` 指令新增 `get` 子指令。此前 set/unset/reset 都支持带会话ID远程操作，但没有任何指令能在聊天侧查到一个会话当前生效的人格（`/persona` 无参数的帮助信息只展示发消息者自己的会话），远程核对只能靠猜，本次补齐。

---

## 需求背景

`/persona` 的 set/unset/reset 已支持「末位会话ID」远程操作（群号/QQ号/昵称/别称），但缺少对应的只读查询：把某群设成某人格后，想确认「到底生效没有、生效的是哪一层配置」，只能去 WebUI 会话管理里翻。新增 `persona get [会话ID]`，与 set/unset/reset 形成完整闭环。

## 功能说明

### 用法

- `persona get`：查看当前会话。
- `persona get <会话ID>`：查看目标会话。会话ID解析与 set/unset/reset 完全同一套机制（`SessionTargetResolver`：完整 UMO / 会话ID段 / WebChat 线程 `!` 分段 / 昵称群名别称）；多候选列出候选拒绝执行，未命中提示「未找到会话」，均不产生任何写入。

### 输出格式

```
[Persona] 会话人格

- 会话: aiocqhttp:GroupMessage:111111（测试群）
- 当前对话: 日常(cid-)
- 自定义规则人格: 规则人格
- 对话人格: 对话人格
- 默认人格情景: default
- 最终生效: 规则人格（来源: 自定义规则）
```

各行的取值口径：

- **当前对话**：当前对话标题 + cid 前 4 位（沿用 `/persona` 无参数帮助信息的 `cid[:4]` 截断惯例）；无对话显示「无」。
- **自定义规则人格**：`session_service_config.persona_id`（`sp` 存储，WebUI 会话服务配置里强制的人格），无则「无」。
- **对话人格**：当前对话记录的 `persona_id`；`[%None]` 显示「无」，对话存在但字段为空显示「未设置」。
- **默认人格情景**：`get_default_persona_v3(umo=目标)` 按目标会话的配置解析。
- **最终生效 + 来源**：直接复用 `persona_manager.resolve_selected_persona()`（与 LLM 管线同一套判定），来源按优先级标注——`自定义规则` > `对话设置` > `WebChat 专用默认人格` > `默认人格`。set/unset 之后用 get 可直接核对生效结果。

### 边界行为

1. **纯只读**：查看对话用 `get_conversation` 默认的 `create_if_not_exists=False`，远程 get 一个只剩别称残留的会话不会把死会话建活（与 set 的自动建对话行为刻意区分）。
2. **远程平台判定**：传给 `resolve_selected_persona` 的 `platform_name` 取目标 umo 的平台段（`umo.split(":", 1)[0]`），而非发消息者所在平台——从 QQ 群远程查一个 WebChat 会话，才能正确命中 ChatUI 默认人格分支。
3. **WebChat 特殊默认人格**：`_chatui_default_` 是虚拟名（不在人格列表中），显示为「ChatUI 默认人格」，不暴露内部 id。
4. **人格已失效告警**：解析出 persona_id 但人格列表中查无此人格（如对话里残留已删除人格、规则指向已删除人格）时，「最终生效」追加「⚠️ 人格不存在」。
5. **帮助信息同步**：`/persona` 无参数的帮助清单新增一行「查看会话人格: `persona get [会话ID]`」。

## 修改文件清单

1. `astrbot/builtin_stars/builtin_commands/commands/persona.py` —— 新增 `_get_persona_on` 方法、`get` 入口分支、帮助文案一行。
2. `tests/test_persona_remote.py` —— 新增 8 个 get 用例（远程有规则/远程对话来源/本会话无对话/`[%None]`/webchat 特殊默认不暴露内部名/人格已失效告警/会话ID未命中拒绝/帮助文案含 get），并补断言 `new_conversation`、`update_conversation_persona_id` 未被调用（只读保护）。

## 验证

- `pytest tests/test_persona_remote.py` 28 个用例全过（含新增 8 个）；连带 `test_session_target_shorthand.py`、`test_webchat_persona_rule.py`、`test_cross_session_commands.py` 共 80 个全过。
- ruff（改动文件范围）通过。

## 部署注意

- 纯后端指令改动，重启生效，无数据库迁移、无 WebUI 改动。
