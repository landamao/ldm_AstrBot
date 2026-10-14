# [2026-10-11] `@filter` 注册器 priority 参数显式化

> 本文件为独立变更日志，记录 StarHandler 注册器 priority 参数从 **kwargs 隐式透传改为显式签名参数的改动范围、排序语义与兼容性。

---

## 动机

`@filter` 全家桶（command、event_message_type、on_llm_request 等装饰器）的签名全是 `**kwargs`，priority 完全靠「约定俗成往 kwargs 里塞」才能用——IDE 补全看不见、类型检查器报未知参数、文档里也无从查起。框架自身已在这么用（builtin_commands 的 `@filter.on_waiting_llm_request(priority=sys.maxsize)`），但这个参数对插件作者是隐形的。本次把它在签名上显式出来。

## 改动内容（astrbot/core/star/register/star_handler.py）

21 个 register_* 函数签名加 `priority: int = 0`，装饰器内显式 `priority=priority` 透传给 `get_handler_or_create`，最终照旧落进 `extras_configs["priority"]`：

- 指令族：`register_command`、`register_command_group`、`register_custom_filter`（后者排在 `*args` 之后，keyword-only）
- 过滤器族：`register_event_message_type`、`register_platform_adapter_type`、`register_regex`、`register_permission_type`
- 钩子族：`register_on_astrbot_loaded`、`register_on_platform_loaded`、`register_on_plugin_error/loaded/unloaded`、`register_on_waiting_llm_request`、`register_on_llm_request`、`register_on_llm_response`、`register_on_agent_begin/done`、`register_on_using_llm_tool`、`register_on_llm_tool_respond`、`register_on_decorating_result`、`register_after_message_sent`

**两个未加**：`register_llm_tool`（工具由 LLM 按需选择，不进 handler 排序注册表，priority 无意义）与 `register_agent`（不注册 handler）。这两个的 kwargs 本就没透传给 handler 注册表。

## 排序语义（未动）

`star_handlers_registry.append` 时按 priority 降序（数值越大越先执行）、缺省补 0 的逻辑原样保留，本次只是让参数在签名上可见并文档化。

## 文档

每个改动函数的 docstring 补中文 Args 节：「priority: 处理优先级，数值越大越先执行，默认为 0。」

## 兼容性

- **纯增量**：原 `priority=N` 的 kwargs 写法继续有效——同名实参现在绑定到显式参数，行为一致；其他未知 kwargs 照旧进 extras_configs。
- 不传 priority 时默认 0，与之前缺省行为一致；框架内无按位置传 priority 的调用点（此前它也不是位置参数）。

## 测试

- 冒烟（venv）：11 个代表函数签名断言含 `priority` 且默认 0；`@event_message_type(EventMessageType.ALL, priority=5)` 与缺省装饰后 `extras_configs` 分别为 5/0，透传正确；`StarTools.send_message` 未初始化报错输出中文（见 StarTools 中文化条目）。
- 回归：tests/test_get_all_commands.py、test_image_command.py、test_plugin_display_name_match.py 共 23 项全过（覆盖内置指令装饰器导入路径）。

## 回归点

- 新增注册器时，凡透传 `**kwargs` 给 `get_handler_or_create` 的，签名里显式声明 `priority: int = 0` 并 `priority=priority` 透传，别再让它藏进 kwargs。
- `register_llm_tool` / `register_agent` 不加 priority 是定案：它们的执行顺序不由 handler 注册表决定。

## 修改文件清单

1. `astrbot/core/star/register/star_handler.py` — 21 个注册器签名 + 透传 + docstring Args
