# [2026-09-02] /persona 支持远程换人格（会话ID参数）与远程 reset

> 本文件为独立变更日志，记录 2026-09-02 一次 persona.py 重写改造：
> `/persona` 指令族新增可选「会话ID」参数，管理员可在聊天侧远程给其他
> 会话（群/用户/WebChat 线程）设置/取消人格并清空其上下文，无需对方执行指令。

---

## 背景

原 `/persona` 只能作用于当前会话。想给别人的群/私聊换人格，只能让对方自己
发指令，或者管理员去 WebUI 规则页操作，远程场景不便。

## 新指令形态

```
/persona <人格名> [会话ID]
/persona unset [会话ID]
/persona reset [会话ID]   ← 新增子指令
```

- 会话ID 省略时行为与旧版完全一致（作用于当前会话）。
- 权限：/persona 在指令注册处本就是 ADMIN，未改；reset 挂在 /persona 下
  而不是扩展 /reset，因为 /reset 有场景化权限配置（私聊默认 member 可用），
  若给 /reset 加会话ID参数，普通成员即可远程清别人对话，权限面被扩大。

## 会话ID 解析规则

- 支持：完整 UMO、群号/QQ号（UMO 会话ID段精确相等）、WebChat 线程 `!`
  分段精确相等（如 user1、thread-9）、昵称（umo_aliases.user_alias）、
  群名/发送者名（umo_aliases.auto_name）。
- 全部为**精确匹配，不做子串部分匹配**（防输「12」误撞「123456」）。
- 候选来源与 WebUI 会话管理同源：conversations 表 distinct user_id；
  昵称/群名命中额外要求该会话在 conversations 表有对话，只剩昵称残留的
  死条目一律不认（防止死会话被远程换人格建活）。
- 匹配到多个 → 拒绝执行并列出候选（含完整 UMO 与显示名），要求输入更
  精确的 ID；未命中 → 回执「未找到会话」。两种情况都不写任何数据。
- 末位 token 不是有效会话ID时，若整体拼接（"".join 不带空格，兼容旧行为）
  是有效人格名，回退按当前会话设置，保持旧用法兼容。

## set 三分支统一逻辑（_set_persona_on）

1. 目标会话有自定义规则人格 → 改规则 session_service_config.persona_id，
   实时生效；
2. 无规则有当前对话 → 写对话 persona_id；
3. 无对话 → new_conversation(persona_id=人格名) 自动创建，对方下一条消息
   即使用新人格（若不自动创建，对方一发消息就建了默认人格的对话，违背
   换人格目的——用户明确要求必须自动建）。

unset：有规则 → 从规则 pop persona_id（其余字段保留）；无规则有对话 →
写 [%None]；两者皆无 → 提示，不写数据。

reset（_reset_on）：复用 /reset 核心链——第三方 Agent runner 会话键清理
（THIRD_PARTY_AGENT_RUNNER_KEY）、active_event_registry.stop_all 停活跃
任务、update_conversation 清对话历史、群聊上下文内存清理（经
get_registered_star("ldm").star_cls.group_chat_context.remove_session）、
message_history_manager.delete_all 清群聊消息历史数据库记录。

## 其他

- 回执文案：指令提示前换行单独成行（「请使用指令\n/persona reset …」），
  方便直接复制。
- `_clean_ltm_session` extra 确认为无消费方的死标记（3 处写入 0 读取），
  本次未模仿使用。

## 修改文件清单

1. `astrbot/builtin_stars/builtin_commands/commands/persona.py`（重写）
2. `tests/test_persona_remote.py`（新增，21 用例）

纯后端改动，无需重新构建前端。

## 验证

- `pytest tests/ -q`：75 passed（含新增 21 例：分段精确匹配/昵称命中/
  死残留不认/同名多候选拒绝/规则改写且 custom_name 保留/对话改写/
  无对话自动创建带人格/人格名不存在不写/reset 清历史/reset 无对话/
  多段拼接人格名回退/list 与帮助不受影响等）；
- 模块导入冒烟通过（THIRD_PARTY_AGENT_RUNNER_KEY 导入无循环依赖）。

## 部署 / 测试注意

- `bash ~/同步源码.sh` 同步到运行目录后手动重启 ldm 生效；
- 回归验证点：
  1. `/persona 人格 群号` → 对方群下一条消息按新人格回复；对方无对话时
     自动建对话并带人格；
  2. `/persona reset 群号` → 对方上下文清空，重新按当前人格开始；
  3. `/persona unset 群号` → 规则页人格恢复「跟随配置文件」；
  4. 不带会话ID的 /persona 各行为与旧版一致；
  5. WebUI 规则页改人格 → 聊天侧 /persona 带会话ID → 规则页同步变化。

## 回滚

还原 persona.py（tests 新增文件可留）。
