from __future__ import annotations

from typing import Any

from astrbot.core import logger
from astrbot.core.config.default import CONFIG_METADATA_3
from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
from astrbot.core.star.star import star_registry

# 模型选择类配置的 _special 标记 → 对应的提供商类型。
# 与前端 ConfigItemRenderer 的渲染分支保持一致。
SELECT_CHAT_MODEL_CHAIN = "select_chat_model_chain"
MODEL_SELECT_SPECIALS: dict[str, str] = {
    "select_provider": "chat_completion",
    "select_providers": "chat_completion",
    "select_provider_stt": "speech_to_text",
    "select_provider_tts": "text_to_speech",
    "select_provider_image_generation": "image_generation",
    SELECT_CHAT_MODEL_CHAIN: "chat_completion",
}
AGENT_RUNNER_SPECIAL = "select_agent_runner_provider"

# 值匹配启发式的上限，超过视为普通长文本，不是提供商 ID。
_MAX_VALUE_LEN_FOR_MATCH = 128


def _get_nested(mapping: Any, dotted_key: str) -> Any:
    current = mapping
    for part in dotted_key.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _set_nested(mapping: dict, dotted_key: str, value: Any) -> None:
    keys = dotted_key.split(".")
    current = mapping
    for key in keys[:-1]:
        node = current.get(key)
        if not isinstance(node, dict):
            node = {}
            current[key] = node
        current = node
    current[keys[-1]] = value


def _special_name(special: Any) -> str:
    if not isinstance(special, str) or not special:
        return ""
    return special.split(":", 1)[0]


class ModelUsageService:
    """扫描并批量修改各处配置中的模型（提供商）使用选择。

    - 主配置：按 CONFIG_METADATA_3 的 _special 标记识别模型选择项，
      覆盖 default 配置与所有 abconf 配置档案。
    - 插件配置：优先按插件 schema 的 _special 标记识别；没有标记时，
      对值为字符串/字符串列表且恰好命中某个提供商 ID 的配置项做“疑似引用”兜底。
    """

    def __init__(self, core_lifecycle: AstrBotCoreLifecycle) -> None:
        self.core_lifecycle = core_lifecycle

    # ------------------------------------------------------------------ 扫描

    def scan(self) -> dict:
        providers = self._provider_summaries()
        usages = self._scan_core_configs(providers)
        usages += self._scan_plugin_configs(providers)
        return {"providers": providers, "usages": usages}

    def _provider_summaries(self) -> list[dict]:
        source_types = {
            source.get("id"): source.get("provider_type", "chat_completion")
            for source in self.core_lifecycle.provider_manager.provider_sources_config
        }
        providers = []
        seen: set[str] = set()
        for conf in self.core_lifecycle.provider_manager.providers_config:
            provider_id = conf.get("id")
            if not provider_id or provider_id in seen:
                continue
            seen.add(provider_id)
            provider_type = conf.get("provider_type")
            if not provider_type and conf.get("provider_source_id"):
                provider_type = source_types.get(
                    conf.get("provider_source_id"), "chat_completion"
                )
            providers.append(
                {
                    "id": provider_id,
                    "model": conf.get("model") or "",
                    "enable": conf.get("enable", False) is not False,
                    "provider_type": provider_type or "chat_completion",
                }
            )
        return providers

    def _provider_id_set(self) -> set[str]:
        return {
            p["id"]
            for p in self.core_lifecycle.provider_manager.providers_config
            if p.get("id")
        }

    def _scan_core_configs(self, providers: list[dict]) -> list[dict]:
        acm = self.core_lifecycle.astrbot_config_mgr
        provider_ids = {p["id"] for p in providers}
        conf_names = {
            info["id"]: info.get("name") or info["id"]
            for info in acm.get_conf_list()
        }
        usages: list[dict] = []
        for conf_id, conf in acm.confs.items():
            conf_name = conf_names.get(conf_id, conf_id)
            for group_key, group in CONFIG_METADATA_3.items():
                group_name = group.get("name") or group_key
                for section_key, section in (group.get("metadata") or {}).items():
                    if not isinstance(section, dict) or section.get("type") != "object":
                        continue
                    section_desc = section.get("description") or section_key
                    for item_key, item_meta in (section.get("items") or {}).items():
                        if not isinstance(item_meta, dict):
                            continue
                        usage = self._core_usage_from_meta(
                            conf,
                            conf_id,
                            conf_name,
                            group_name,
                            section_desc,
                            item_key,
                            item_meta,
                            provider_ids,
                        )
                        if usage:
                            usages.append(usage)
        return usages

    def _core_usage_from_meta(
        self,
        conf: dict,
        conf_id: str,
        conf_name: str,
        group_name: str,
        section_desc: str,
        item_key: str,
        item_meta: dict,
        provider_ids: set[str],
    ) -> dict | None:
        special = item_meta.get("_special")
        special_name = _special_name(special)
        is_chain = special_name == SELECT_CHAT_MODEL_CHAIN
        if special_name in MODEL_SELECT_SPECIALS:
            provider_type = MODEL_SELECT_SPECIALS[special_name]
        elif special_name == AGENT_RUNNER_SPECIAL:
            provider_type = "agent_runner"
        else:
            return None
        # 回退对话模型列表已合并进「对话模型链」条目，自身是隐藏项，不再单列
        if (
            not is_chain
            and special_name == "select_providers"
            and item_key.endswith("fallback_chat_models")
        ):
            return None

        if is_chain:
            # 对话模型链 = 主模型(default_provider_id) + 回退列表(fallback_chat_models)。
            # 回退列表已合并进链条目，不再单列，避免重复。
            fallback_key = self._chain_fallback_key(item_key)
            main_value = _get_nested(conf, item_key)
            fallback_value = _get_nested(conf, fallback_key) or []
            values = [
                v for v in [main_value, *fallback_value] if isinstance(v, str) and v
            ]
            keys = [item_key, fallback_key]
            value_type = "chain"
            multiple = True
        else:
            values = self._as_str_list(_get_nested(conf, item_key))
            keys = [item_key]
            value_type = "list" if item_meta.get("type") == "list" else "single"
            multiple = value_type == "list" or special_name == "select_providers"

        matched = [v for v in values if v in provider_ids]
        return {
            "scope": "core",
            "config_id": conf_id,
            "config_name": conf_name,
            "group": f"{group_name} / {section_desc}",
            "plugin_name": "",
            "plugin_display_name": "",
            "plugin_activated": True,
            "plugin_reserved": False,
            "key_path": item_key,
            "keys": keys,
            "display_key": item_meta.get("description") or item_key,
            "hint": item_meta.get("hint") or "",
            "match_type": "schema",
            "special": special if isinstance(special, str) else "",
            "provider_type": provider_type,
            "value_type": value_type,
            "multiple": multiple,
            "value": values,
            "matched_provider_ids": matched,
            "invalid_provider_ids": [v for v in values if v not in provider_ids],
        }

    @staticmethod
    def _chain_fallback_key(item_key: str) -> str:
        parts = item_key.split(".")
        if parts[-1] == "default_provider_id":
            parts[-1] = "fallback_chat_models"
        else:
            parts[-1] = f"{parts[-1]}_fallback_chat_models"
        return ".".join(parts)

    def _scan_plugin_configs(self, providers: list[dict]) -> list[dict]:
        provider_ids = {p["id"] for p in providers}
        usages: list[dict] = []
        for md in star_registry:
            if not md.config or not md.name:
                continue
            schema = getattr(md.config, "schema", None) or {}
            self._walk_plugin_schema(schema, md.config, [], md, provider_ids, usages)
        return usages

    def _walk_plugin_schema(
        self,
        schema: dict,
        conf: dict,
        path: list[str],
        md,
        provider_ids: set[str],
        usages: list[dict],
    ) -> None:
        for key, meta in schema.items():
            if not isinstance(meta, dict):
                continue
            key_path = [*path, key]
            if meta.get("type") == "object" and isinstance(meta.get("items"), dict):
                self._walk_plugin_schema(
                    meta["items"], conf, key_path, md, provider_ids, usages
                )
                continue

            special = meta.get("_special")
            special_name = _special_name(special)
            value = _get_nested(conf, ".".join(key_path))
            if special_name in MODEL_SELECT_SPECIALS:
                match_type = "schema"
                provider_type = MODEL_SELECT_SPECIALS[special_name]
            elif special_name == AGENT_RUNNER_SPECIAL:
                match_type = "schema"
                provider_type = "agent_runner"
            else:
                # 没有 _special 标记的插件配置，用「值恰好命中提供商 ID」做疑似引用兜底
                if not self._match_value_against_ids(value, provider_ids):
                    continue
                match_type = "value"
                provider_type = ""

            values = self._as_str_list(value)
            usages.append(
                {
                    "scope": "plugin",
                    "config_id": "",
                    "config_name": "",
                    "plugin_name": md.name,
                    "plugin_display_name": md.display_name or md.name,
                    "plugin_activated": bool(md.activated),
                    "plugin_reserved": bool(md.reserved),
                    "group": "",
                    "key_path": ".".join(key_path),
                    "keys": [".".join(key_path)],
                    "display_key": meta.get("description") or key,
                    "hint": meta.get("hint") or "",
                    "match_type": match_type,
                    "special": special if isinstance(special, str) else "",
                    "provider_type": provider_type,
                    "value_type": "list" if isinstance(value, list) else "single",
                    "multiple": isinstance(value, list)
                    or special_name == "select_providers",
                    "value": values,
                    "matched_provider_ids": [v for v in values if v in provider_ids],
                    "invalid_provider_ids": [
                        v for v in values if v not in provider_ids
                    ],
                }
            )

    @staticmethod
    def _as_str_list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [v for v in value if isinstance(v, str) and v]
        return []

    @staticmethod
    def _match_value_against_ids(value: Any, provider_ids: set[str]) -> bool:
        if isinstance(value, str):
            return (
                bool(value)
                and len(value) <= _MAX_VALUE_LEN_FOR_MATCH
                and value in provider_ids
            )
        if isinstance(value, list):
            return any(
                isinstance(v, str)
                and bool(v)
                and len(v) <= _MAX_VALUE_LEN_FOR_MATCH
                and v in provider_ids
                for v in value
            )
        return False

    # ------------------------------------------------------------------ 修改

    async def apply(self, replacements: list[dict]) -> dict:
        """把一批配置项的模型引用直接改写为指定值。

        每个替换项携带 new_value（字符串列表）：
        - 单值项写第一个元素（空列表表示清空）；
        - 列表项整体替换；
        - 对话模型链第一项写主模型、其余写回退列表。
        同一配置目标（插件或主配置档案）的修改会合并为一次落盘，
        插件额外做一次热重载，主配置档案重载流水线调度器。
        """
        if not replacements:
            raise ValueError("没有需要修改的配置项")

        results: list[dict] = []
        pending_saves: dict[tuple[str, str], list[dict]] = {}

        for item in replacements:
            result = self._apply_one(item, pending_saves)
            results.append(result)

        for (scope, target_id), group_results in pending_saves.items():
            save_result = await self._save_target(scope, target_id)
            for result in group_results:
                result["saved"] = save_result["saved"]
                result["reloaded"] = save_result["reloaded"]
                if save_result.get("error"):
                    result["save_error"] = save_result["error"]

        replaced = sum(1 for r in results if r.get("status") == "replaced")
        unchanged = sum(1 for r in results if r.get("status") == "unchanged")
        failed = len(results) - replaced - unchanged
        return {
            "results": results,
            "replaced": replaced,
            "unchanged": unchanged,
            "failed": failed,
            "message": f"替换成功 {replaced} 处，未变化 {unchanged} 处，失败 {failed} 处。",
        }

    def _apply_one(self, item: dict, pending_saves: dict) -> dict:
        scope = item.get("scope")
        key_path = item.get("key_path")
        new_value = item.get("new_value")
        base = {"scope": scope, "key_path": key_path}

        if not key_path:
            return {**base, "status": "error", "message": "缺少配置项"}
        if new_value is None:
            new_value = []
        if not isinstance(new_value, list) or not all(
            isinstance(v, str) for v in new_value
        ):
            return {**base, "status": "error", "message": "new_value 必须是字符串列表"}
        new_value = [v for v in new_value if v]

        if scope == "core":
            conf_id = item.get("config_id")
            conf = self.core_lifecycle.astrbot_config_mgr.confs.get(conf_id)
            if conf is None:
                return {
                    **base,
                    "status": "error",
                    "message": f"配置文件 {conf_id} 不存在",
                }
            target_key = (scope, conf_id)
            label = f"配置文件「{conf_id}」"
        elif scope == "plugin":
            plugin_name = item.get("plugin_name")
            md = next((m for m in star_registry if m.name == plugin_name), None)
            if md is None or not md.config:
                return {
                    **base,
                    "status": "error",
                    "message": f"插件 {plugin_name} 不存在或没有配置",
                }
            conf = md.config
            target_key = (scope, plugin_name)
            label = f"插件「{md.display_name or plugin_name}」"
        else:
            return {**base, "status": "error", "message": f"未知的配置来源: {scope}"}

        special = item.get("special") or ""
        is_chain = _special_name(special) == SELECT_CHAT_MODEL_CHAIN
        try:
            if is_chain:
                changed = self._write_chain_value(conf, key_path, new_value)
            else:
                current = _get_nested(conf, key_path)
                if isinstance(current, list):
                    changed = current != new_value
                else:
                    new_single = new_value[0] if new_value else ""
                    changed = current != new_single
                    new_value = new_single
                if changed:
                    _set_nested(conf, key_path, new_value)
        except Exception as exc:
            logger.error(f"模型引用替换失败（{label} {key_path}）: {exc}")
            return {**base, "status": "error", "message": str(exc)}

        if not changed:
            return {
                **base,
                "status": "unchanged",
                "message": f"{label} 的「{key_path}」已是该值，未做修改",
            }

        result = {
            **base,
            "status": "replaced",
            "message": f"{label} 的「{key_path}」已替换",
        }
        pending_saves.setdefault(target_key, []).append(result)
        return result

    def _write_chain_value(
        self, conf: dict, key_path: str, new_value: list[str]
    ) -> bool:
        fallback_key = self._chain_fallback_key(key_path)
        old_main = _get_nested(conf, key_path)
        old_fallback = _get_nested(conf, fallback_key)
        if not isinstance(old_fallback, list):
            old_fallback = []

        new_main = new_value[0] if new_value else ""
        new_fallback = [v for v in new_value[1:] if v != new_main]

        changed = old_main != new_main or old_fallback != new_fallback
        if changed:
            _set_nested(conf, key_path, new_main)
            _set_nested(conf, fallback_key, new_fallback)
        return changed

    async def _save_target(self, scope: str, target_id: str) -> dict:
        try:
            if scope == "core":
                conf = self.core_lifecycle.astrbot_config_mgr.confs.get(target_id)
                if conf is None:
                    return {
                        "saved": False,
                        "reloaded": False,
                        "error": f"配置文件 {target_id} 不存在",
                    }
                conf.save_config()
                await self.core_lifecycle.reload_pipeline_scheduler(target_id)
                return {"saved": True, "reloaded": True, "error": ""}
            md = next((m for m in star_registry if m.name == target_id), None)
            if md is None or not md.config:
                return {
                    "saved": False,
                    "reloaded": False,
                    "error": f"插件 {target_id} 不存在",
                }
            md.config.save_config()
            success, reload_err = await self.core_lifecycle.plugin_manager.reload(
                target_id
            )
            if not success:
                return {
                    "saved": True,
                    "reloaded": False,
                    "error": f"插件重载失败：{reload_err or '未知错误'}",
                }
            return {"saved": True, "reloaded": True, "error": ""}
        except Exception as exc:
            logger.error(f"模型引用保存失败（{scope}:{target_id}）: {exc}")
            return {"saved": False, "reloaded": False, "error": str(exc)}
