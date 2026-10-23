"""会话级模型/提供商选择的存储与解析。

选择分三层，优先级从高到低：

1. 自定义规则（WebUI「会话管理 → 自定义规则」，或指令带 ``-save``）：
   ``provider_perf_<type>`` 保存提供商，``session_service_config`` 里的
   ``model`` / ``model_provider_id`` 保存该会话的模型。
2. 会话级临时覆盖（会话没有自定义规则时，``/model``、``/provider``
   不带 ``-save`` 的选择）：只保存在当前进程的内存里，重启后自动失效。
3. 全局配置：提供商配置里的 ``model`` 与 ``provider_settings.default_provider_id``。

``/model``、``/provider`` 通过这里的读写函数更新选择，请求构建时用
``resolve_session_model`` 取当前会话真正要用的模型。
"""

from __future__ import annotations

from typing import Any

from astrbot.core import logger, sp
from astrbot.core.provider.entities import ProviderType

SESSION_MODEL_OVERRIDE_KEY = "session_model_override"
"""旧版本持久化的会话级模型覆盖键（现在只存内存，启动时清理残留）"""

SESSION_PROVIDER_OVERRIDE_KEY_PREFIX = "session_provider_override_"
"""旧版本持久化的会话级提供商覆盖键前缀（现在只存内存，启动时清理残留）"""

RULE_MODEL_KEY = "model"
"""自定义规则里保存的模型名（session_service_config.model）"""

RULE_MODEL_PROVIDER_KEY = "model_provider_id"
"""自定义规则里模型所属的提供商 id"""

SESSION_SERVICE_CONFIG_KEY = "session_service_config"

SESSION_RULE_KEYS = [
    SESSION_SERVICE_CONFIG_KEY,
    "session_plugin_config",
    "kb_config",
    f"provider_perf_{ProviderType.CHAT_COMPLETION.value}",
    f"provider_perf_{ProviderType.SPEECH_TO_TEXT.value}",
    f"provider_perf_{ProviderType.TEXT_TO_SPEECH.value}",
]
"""WebUI「自定义规则」会读写的全部键。存在任意一个即视为该会话有自定义规则。"""

_runtime_provider_overrides: dict[tuple[str, str], str] = {}
"""进程内会话级提供商覆盖：(umo, provider_type) -> provider_id"""

_runtime_model_overrides: dict[str, dict[str, str]] = {}
"""进程内会话级模型覆盖：umo -> {"provider_id": str, "model": str}"""


def rule_provider_key(provider_type: ProviderType) -> str:
    return f"provider_perf_{provider_type.value}"


def _pref_value(pref: Any) -> Any:
    value = getattr(pref, "value", None)
    if isinstance(value, dict):
        return value.get("val")
    return value


def _normalize_model_override(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    model = value.get("model")
    provider_id = value.get("provider_id")
    model = model.strip() if isinstance(model, str) else ""
    provider_id = provider_id.strip() if isinstance(provider_id, str) else ""
    if not model:
        return None
    return {"model": model, "provider_id": provider_id}


async def get_session_prefs(umo: str) -> dict[str, Any]:
    """一次读出该会话的全部偏好，避免逐键查询。"""
    prefs = await sp.range_get_async("umo", umo)
    result: dict[str, Any] = {}
    for pref in prefs or []:
        key = getattr(pref, "key", None)
        if key:
            result[key] = _pref_value(pref)
    return result


async def has_session_rule(umo: str) -> bool:
    """该会话是否存在自定义规则（任一规则键存在即算）。"""
    prefs = await get_session_prefs(umo)
    return any(key in prefs for key in SESSION_RULE_KEYS)


async def get_session_model_override(umo: str) -> dict[str, str] | None:
    """读取会话级临时模型覆盖（仅当前进程有效）。"""
    return _normalize_model_override(_runtime_model_overrides.get(umo))


def get_session_provider_override(umo: str, provider_type: ProviderType) -> str | None:
    """同步读取会话级临时提供商覆盖（供 get_using_provider 使用）。"""
    value = _runtime_provider_overrides.get((umo, provider_type.value))
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def resolve_session_model(
    umo: str, provider_id: str | None
) -> tuple[str | None, str]:
    """返回当前会话在指定提供商上应使用的模型。

    Returns:
        (model, source)，source 为 ``rule`` / ``session`` / ``none``。
        模型带有提供商绑定：绑定不匹配时视为不适用，回退到下一层。
    """
    provider_id = (provider_id or "").strip()

    cfg = await sp.get_async("umo", umo, SESSION_SERVICE_CONFIG_KEY, {})
    if isinstance(cfg, dict):
        rule_model = cfg.get(RULE_MODEL_KEY)
        rule_provider = cfg.get(RULE_MODEL_PROVIDER_KEY)
        rule_model = rule_model.strip() if isinstance(rule_model, str) else ""
        rule_provider = rule_provider.strip() if isinstance(rule_provider, str) else ""
        if rule_model and (not rule_provider or rule_provider == provider_id):
            return rule_model, "rule"

    session_override = _normalize_model_override(_runtime_model_overrides.get(umo))
    if session_override:
        bound_provider = session_override["provider_id"]
        if not bound_provider or bound_provider == provider_id:
            return session_override["model"], "session"

    return None, "none"


async def resolve_effective_model(
    umo: str, provider_id: str | None, provider_model: str | None
) -> tuple[str | None, str]:
    """返回当前会话实际生效的模型及其来源。

    配置项的 ``model`` 是唯一事实来源：规则/会话选中某个配置项后，模型就
    取该配置项的 model。因此来源要看“谁选中了这个配置项”，只看模型字段
    会把「规则只选了提供商」的会话误判成全局配置。

    Returns:
        (model, source)，source 为 ``rule`` / ``session`` / ``none``。
    """
    provider_id = (provider_id or "").strip()
    provider_model = (provider_model or "").strip()

    model, source = await resolve_session_model(umo, provider_id)
    if source != "none":
        return provider_model or model, source

    if provider_id:
        prefs = await get_session_prefs(umo)
        if (
            prefs.get(rule_provider_key(ProviderType.CHAT_COMPLETION))
            == provider_id
        ):
            return provider_model, "rule"
        if (
            get_session_provider_override(umo, ProviderType.CHAT_COMPLETION)
            == provider_id
        ):
            return provider_model, "session"

    return provider_model, "none"


async def set_session_selection(umo: str, provider_id: str, model: str) -> None:
    """写入会话级临时提供商 + 模型覆盖（只存内存，重启后失效）。"""
    provider_id = (provider_id or "").strip()
    model = (model or "").strip()
    if not model:
        raise ValueError("模型名不能为空")
    if provider_id:
        _runtime_provider_overrides[
            (umo, ProviderType.CHAT_COMPLETION.value)
        ] = provider_id
    _runtime_model_overrides[umo] = {"provider_id": provider_id, "model": model}


async def set_session_provider(
    umo: str, provider_type: ProviderType, provider_id: str
) -> None:
    """写入会话级临时提供商覆盖；切到别的提供商时旧模型覆盖不再适用。"""
    provider_id = (provider_id or "").strip()
    if provider_type == ProviderType.CHAT_COMPLETION:
        existing = _normalize_model_override(_runtime_model_overrides.get(umo))
        if (
            existing
            and existing["provider_id"]
            and existing["provider_id"] != provider_id
        ):
            _runtime_model_overrides.pop(umo, None)
    if provider_id:
        _runtime_provider_overrides[(umo, provider_type.value)] = provider_id


async def clear_session_provider(umo: str, provider_type: ProviderType) -> None:
    """清除某一类提供商的会话级临时覆盖（聊天类同时清掉模型覆盖）。"""
    _runtime_provider_overrides.pop((umo, provider_type.value), None)
    if provider_type == ProviderType.CHAT_COMPLETION:
        await clear_session_model_override(umo)


async def clear_session_selection(umo: str) -> None:
    """清除全部会话级临时模型/提供商覆盖（不影响自定义规则）。"""
    for provider_type in (
        ProviderType.CHAT_COMPLETION,
        ProviderType.SPEECH_TO_TEXT,
        ProviderType.TEXT_TO_SPEECH,
    ):
        await clear_session_provider(umo, provider_type)


async def clear_session_model_override(umo: str) -> None:
    _runtime_model_overrides.pop(umo, None)


def clear_all_session_overrides() -> int:
    """清空所有进程内会话级覆盖（等价于重启后的状态）。

    Returns:
        受影响的会话数量。
    """
    affected_umos = set(_runtime_model_overrides)
    affected_umos.update(umo for umo, _ in _runtime_provider_overrides)
    _runtime_model_overrides.clear()
    _runtime_provider_overrides.clear()
    return len(affected_umos)


async def purge_legacy_session_overrides() -> None:
    """清理旧版本持久化在数据库里的会话级覆盖。

    会话级覆盖现在只存在于内存中、重启即失效，旧版本写入的数据库记录
    不再被读取，启动时顺手删除，避免残留数据造成误解。
    """
    try:
        prefs = await sp.range_get_async("umo", None, None)
    except Exception as e:  # noqa: BLE001
        logger.debug("读取旧会话级覆盖失败: %s", e)
        return

    removed = 0
    for pref in prefs or []:
        key = str(getattr(pref, "key", "") or "")
        if key != SESSION_MODEL_OVERRIDE_KEY and not key.startswith(
            SESSION_PROVIDER_OVERRIDE_KEY_PREFIX
        ):
            continue
        scope_id = str(getattr(pref, "scope_id", "") or "")
        if not scope_id:
            continue
        try:
            await sp.remove_async("umo", scope_id, key)
            removed += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("清理旧会话级覆盖 %s/%s 失败: %s", scope_id, key, e)
    if removed:
        logger.info("已清理 %s 条旧版本持久化的会话级模型/提供商覆盖", removed)


async def set_rule_selection(umo: str, provider_id: str, model: str) -> None:
    """把提供商与模型写入该会话的自定义规则，并清除会话级临时覆盖。"""
    cfg = await sp.get_async("umo", umo, SESSION_SERVICE_CONFIG_KEY, {}) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    cfg[RULE_MODEL_KEY] = model
    cfg[RULE_MODEL_PROVIDER_KEY] = provider_id
    await sp.session_put(umo, SESSION_SERVICE_CONFIG_KEY, cfg)
    await sp.session_put(
        umo,
        rule_provider_key(ProviderType.CHAT_COMPLETION),
        provider_id,
    )
    await clear_session_provider(umo, ProviderType.CHAT_COMPLETION)


async def set_rule_provider(
    umo: str, provider_type: ProviderType, provider_id: str
) -> None:
    """把提供商写入自定义规则；切换后旧模型不再适用时一并清掉。"""
    await sp.session_put(umo, rule_provider_key(provider_type), provider_id)
    if provider_type == ProviderType.CHAT_COMPLETION:
        await clear_rule_model_if_bound_elsewhere(umo, provider_id)
    await clear_session_provider(umo, provider_type)


async def clear_rule_model_if_bound_elsewhere(umo: str, provider_id: str) -> None:
    """规则里的模型属于其他提供商时清掉，避免残留误导。"""
    cfg = await sp.get_async("umo", umo, SESSION_SERVICE_CONFIG_KEY, {}) or {}
    if not isinstance(cfg, dict):
        return
    bound_provider = cfg.get(RULE_MODEL_PROVIDER_KEY)
    if (
        isinstance(bound_provider, str)
        and bound_provider.strip()
        and bound_provider.strip() != provider_id
    ):
        cfg.pop(RULE_MODEL_KEY, None)
        cfg.pop(RULE_MODEL_PROVIDER_KEY, None)
        await sp.session_put(umo, SESSION_SERVICE_CONFIG_KEY, cfg)


async def _clear_rule_selection(umo: str, provider_type: ProviderType) -> None:
    """清除自定义规则里的模型/提供商选择，保留规则的其他设置。"""
    try:
        await sp.session_remove(umo, rule_provider_key(provider_type))
    except Exception as e:  # noqa: BLE001
        logger.debug("清除规则提供商选择 %s 失败: %s", provider_type.value, e)

    if provider_type != ProviderType.CHAT_COMPLETION:
        return
    cfg = await sp.get_async("umo", umo, SESSION_SERVICE_CONFIG_KEY, {}) or {}
    if not isinstance(cfg, dict):
        return
    if RULE_MODEL_KEY not in cfg and RULE_MODEL_PROVIDER_KEY not in cfg:
        return
    cfg.pop(RULE_MODEL_KEY, None)
    cfg.pop(RULE_MODEL_PROVIDER_KEY, None)
    if cfg:
        await sp.session_put(umo, SESSION_SERVICE_CONFIG_KEY, cfg)
    else:
        try:
            await sp.session_remove(umo, SESSION_SERVICE_CONFIG_KEY)
        except Exception as e:  # noqa: BLE001
            logger.debug("清除空的会话服务规则失败: %s", e)


async def reset_selection(umo: str, provider_type: ProviderType) -> bool:
    """重置模型/提供商选择，恢复跟随全局配置。

    同时清除会话级临时覆盖与自定义规则里的对应选择；规则里的其他设置
    （人格、插件、知识库、服务开关等）保持不变。

    Returns:
        是否清除了内容；False 表示本来就没有自定义选择。
    """
    removed = get_session_provider_override(umo, provider_type) is not None
    if provider_type == ProviderType.CHAT_COMPLETION and _normalize_model_override(
        _runtime_model_overrides.get(umo)
    ):
        removed = True

    prefs = await get_session_prefs(umo)
    if prefs.get(rule_provider_key(provider_type)) is not None:
        removed = True
    cfg = prefs.get(SESSION_SERVICE_CONFIG_KEY)
    if provider_type == ProviderType.CHAT_COMPLETION and isinstance(cfg, dict):
        if cfg.get(RULE_MODEL_KEY) or cfg.get(RULE_MODEL_PROVIDER_KEY):
            removed = True

    await clear_session_provider(umo, provider_type)
    await _clear_rule_selection(umo, provider_type)
    return removed
