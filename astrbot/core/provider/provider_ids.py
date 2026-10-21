"""提供商配置项 ID 与 model 的同步工具。

WebUI 添加模型时用 ``{provider_source_id}/{model}`` 生成配置项 ID。
如果之后只改 model 不改 ID，配置项名字就会和实际模型失配；这里负责
计算期望 ID、判断是否为自动生成的 ID，并在改名时同步配置里的引用。
"""

from __future__ import annotations

from typing import Any


def expected_provider_id(provider_config: dict) -> str | None:
    """按 ``{provider_source_id}/{model}`` 计算期望的配置项 ID。"""
    source_id = str(provider_config.get("provider_source_id", "") or "").strip()
    model = str(provider_config.get("model", "") or "").strip()
    if not source_id or not model:
        return None
    return f"{source_id}/{model}"


def is_auto_generated_provider_id(provider_config: dict) -> bool:
    """配置项 ID 是否属于 WebUI 自动生成的 ``来源/模型`` 形式。"""
    source_id = str(provider_config.get("provider_source_id", "") or "").strip()
    current_id = str(provider_config.get("id", "") or "").strip()
    if not source_id or not current_id:
        return False
    return current_id.startswith(f"{source_id}/")


def replace_provider_id_in_config(node: Any, old_id: str, new_id: str) -> None:
    """把配置树里的提供商 ID 引用从 old_id 换成 new_id。

    只处理专门的引用字段，避免误改提示词等普通字符串：

    - key 以 ``provider_id`` 结尾（default_provider_id、
      llm_compress_provider_id、provider_tts_settings.provider_id 等）；
    - key 以 ``fallback_chat_models`` 结尾的列表。
    """
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if (
                isinstance(value, str)
                and value == old_id
                and str(key).endswith("provider_id")
            ):
                node[key] = new_id
            elif isinstance(value, list) and str(key).endswith(
                "fallback_chat_models"
            ):
                node[key] = [
                    new_id if item == old_id else item for item in value
                ]
            else:
                replace_provider_id_in_config(value, old_id, new_id)
    elif isinstance(node, list):
        for item in node:
            replace_provider_id_in_config(item, old_id, new_id)
