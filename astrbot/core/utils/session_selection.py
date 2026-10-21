"""会话级模型/提供商选择的持久化与解析。

存储分三层，优先级从高到低：

1. 自定义规则（WebUI「会话管理 → 自定义规则」）：
   ``provider_perf_<type>`` 保存提供商，``session_service_config`` 里的
   ``model`` / ``model_provider_id`` 保存该会话的模型。
2. 会话级覆盖（只对当前会话生效，不进入自定义规则列表）：
   ``session_provider_override_<type>`` + ``session_model_override``。
3. 全局配置：提供商配置里的 ``model`` 与 ``provider_settings.default_provider_id``。

``/model``、``/provider`` 通过这里的读写函数落盘，请求构建时用
``resolve_session_model`` 取当前会话真正要用的模型。
"""

from __future__ import annotations

from typing import Any

from astrbot.core import logger, sp
from astrbot.core.provider.entities import ProviderType

SESSION_MODEL_OVERRIDE_KEY = "session_model_override"
"""会话级模型覆盖：{"provider_id": str, "model": str}"""

SESSION_PROVIDER_OVERRIDE_KEY_PREFIX = "session_provider_override_"
"""会话级提供商覆盖：session_provider_override_<provider_type>"""

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


def rule_provider_key(provider_type: ProviderType) -> str:
    return f"provider_perf_{provider_type.value}"


def session_provider_key(provider_type: ProviderType) -> str:
    return f"{SESSION_PROVIDER_OVERRIDE_KEY_PREFIX}{provider_type.value}"


def _pref_value(pref: Any) -> Any:
    value = getattr(pref, "value", None)
    if isinstance(value, dict):
        return value.get("val")
    return value


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
    """读取会话级模型覆盖。"""
    value = await sp.get_async("umo", umo, SESSION_MODEL_OVERRIDE_KEY, None)
    if not isinstance(value, dict):
        return None
    model = value.get("model")
    provider_id = value.get("provider_id")
    model = model.strip() if isinstance(model, str) else ""
    provider_id = provider_id.strip() if isinstance(provider_id, str) else ""
    if not model:
        return None
    return {"model": model, "provider_id": provider_id}


def get_session_provider_override(umo: str, provider_type: ProviderType) -> str | None:
    """同步读取会话级提供商覆盖（供 get_using_provider 使用）。"""
    value = sp.get(
        session_provider_key(provider_type),
        None,
        scope="umo",
        scope_id=umo,
    )
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
    prefs = await get_session_prefs(umo)

    cfg = prefs.get(SESSION_SERVICE_CONFIG_KEY)
    if isinstance(cfg, dict):
        rule_model = cfg.get(RULE_MODEL_KEY)
        rule_provider = cfg.get(RULE_MODEL_PROVIDER_KEY)
        rule_model = rule_model.strip() if isinstance(rule_model, str) else ""
        rule_provider = rule_provider.strip() if isinstance(rule_provider, str) else ""
        if rule_model and (not rule_provider or rule_provider == provider_id):
            return rule_model, "rule"

    session_override = prefs.get(SESSION_MODEL_OVERRIDE_KEY)
    if isinstance(session_override, dict):
        model = session_override.get("model")
        bound_provider = session_override.get("provider_id")
        model = model.strip() if isinstance(model, str) else ""
        bound_provider = (
            bound_provider.strip() if isinstance(bound_provider, str) else ""
        )
        if model and (not bound_provider or bound_provider == provider_id):
            return model, "session"

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
            prefs.get(session_provider_key(ProviderType.CHAT_COMPLETION))
            == provider_id
        ):
            return provider_model, "session"

    return provider_model, "none"


async def set_session_selection(umo: str, provider_id: str, model: str) -> None:
    """写入会话级提供商 + 模型覆盖（不创建自定义规则）。"""
    await sp.session_put(
        umo,
        session_provider_key(ProviderType.CHAT_COMPLETION),
        provider_id,
    )
    await sp.session_put(
        umo,
        SESSION_MODEL_OVERRIDE_KEY,
        {"provider_id": provider_id, "model": model},
    )


async def set_session_provider(
    umo: str, provider_type: ProviderType, provider_id: str
) -> None:
    """写入会话级提供商覆盖；切到别的提供商时旧模型覆盖不再适用。"""
    if provider_type == ProviderType.CHAT_COMPLETION:
        existing = await get_session_model_override(umo)
        if (
            existing
            and existing.get("provider_id")
            and existing["provider_id"] != provider_id
        ):
            await clear_session_model_override(umo)
    await sp.session_put(umo, session_provider_key(provider_type), provider_id)


async def clear_session_provider(umo: str, provider_type: ProviderType) -> None:
    """清除某一类提供商的会话级覆盖（聊天类同时清掉模型覆盖）。"""
    try:
        await sp.session_remove(umo, session_provider_key(provider_type))
    except Exception as e:  # noqa: BLE001
        logger.debug("清除会话提供商覆盖 %s 失败: %s", provider_type.value, e)
    if provider_type == ProviderType.CHAT_COMPLETION:
        await clear_session_model_override(umo)


async def clear_session_selection(umo: str) -> None:
    """清除全部会话级模型/提供商覆盖（不影响自定义规则）。"""
    for provider_type in (
        ProviderType.CHAT_COMPLETION,
        ProviderType.SPEECH_TO_TEXT,
        ProviderType.TEXT_TO_SPEECH,
    ):
        await clear_session_provider(umo, provider_type)


async def clear_session_model_override(umo: str) -> None:
    try:
        await sp.session_remove(umo, SESSION_MODEL_OVERRIDE_KEY)
    except Exception as e:  # noqa: BLE001
        logger.debug("清除会话模型覆盖失败: %s", e)


async def set_rule_selection(umo: str, provider_id: str, model: str) -> None:
    """把提供商与模型写入该会话的自定义规则，并清除会话级覆盖。"""
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
