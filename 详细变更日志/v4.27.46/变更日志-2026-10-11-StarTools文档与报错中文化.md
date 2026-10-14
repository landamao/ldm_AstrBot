# [2026-10-11] StarTools 用法说明与报错中文化

> 本文件为独立变更日志，记录 star_tools.py 的 docstring 与运行时错误消息中文化，以及「用户可见文案一律中文」的约定。

---

## 约定

用户定案（2026-10-11）：「除了代码，其他能看见的都要中文」——docstring、运行时报错等用户可见文案一律中文，代码标识符不动。注意与「注入 LLM 的提示词一律英文」约定不冲突：StarTools 的 docstring 是给插件开发者看的 API 文档，不进提示词。

## docstring 中文化（astrbot/core/star/star_tools.py）

类 docstring 与 10 个方法的英文 docstring 全部译为中文：`initialize`、`send_message`、`send_message_by_id`、`create_message`、`create_event`、`activate_llm_tool`、`deactivate_llm_tool`、`register_llm_tool`、`unregister_llm_tool`、`get_data_dir`。

风格与文件内已是中文的 `get_font` 对齐：正文中文、全角标点，保留 `Args`/`Returns`/`Raises` 英文节名（Google 风格）。

## 运行时报错中文化

| 原文 | 现文 |
| --- | --- |
| `StarTools not initialized`（7 处） | `StarTools 未初始化` |
| `Platform not found: {platform}` | `找不到平台: {platform}` |
| `Unable to resolve caller module information` | `无法解析调用者模块信息` |
| `Unable to resolve metadata for module {module.__name__}` | `无法解析模块 {module.__name__} 的插件元数据` |
| `Unable to resolve plugin name` | `无法解析插件名称` |
| `Unable to create directory {data_dir}: permission denied` | `无法创建目录 {data_dir}: 权限不足` |
| `Unable to create directory {data_dir}: {e!s}` | `无法创建目录 {data_dir}: {e!s}` |

「找不到平台」同时统一了 `send_message_by_id`（原本就是中文）与 `create_event`（原英文）两处的措辞。

## 兼容性

- docstring 变更无运行时影响（grep 全仓确认无测试或代码断言其文本）。
- 错误消息变更：grep 全仓无代码/测试按这些文本匹配；调用方按异常类型捕获，不按消息内容判断，无兼容问题。

## 遗留

上游继承的其余英文报错（context.py、各平台适配器等）未动，全面清扫是独立工作，待用户拍板后再做。

## 测试

py_compile 通过；冒烟确认未初始化调用报错输出「StarTools 未初始化」；test_get_all_commands / test_image_command / test_plugin_display_name_match 共 23 项全过。

## 修改文件清单

1. `astrbot/core/star/star_tools.py` — 类与 10 个方法 docstring 译文 + 7 类报错中文化
