from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core.provider.entities import ProviderType
from astrbot.core.provider.provider_ids import (
    expected_provider_id,
    is_auto_generated_provider_id,
)
from astrbot.core.utils.error_redaction import safe_error
from astrbot.core.utils.session_selection import (
    clear_all_session_overrides,
    clear_session_provider,
    has_session_rule,
    reset_selection,
    resolve_effective_model,
    resolve_session_model,
    set_rule_provider,
    set_rule_selection,
    set_session_provider,
    set_session_selection,
)
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词

from .utils.param_utils import 校验非空, 转整数

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider


MODEL_LIST_CACHE_TTL_SECONDS_DEFAULT = 30.0
MODEL_LOOKUP_MAX_CONCURRENCY_DEFAULT = 4
MODEL_LOOKUP_MAX_CONCURRENCY_UPPER_BOUND = 16
MODEL_LIST_CACHE_TTL_KEY = "model_list_cache_ttl_seconds"
MODEL_LOOKUP_MAX_CONCURRENCY_KEY = "model_lookup_max_concurrency"
MODEL_CACHE_MAX_ENTRIES = 512

_FLAG_SAVE = "save"
_FLAG_GLOBAL = "global"
_FLAG_RESET = "reset"
_FLAG_CLEAR = "clear"
_SWITCH_FLAGS = {
    "-save": _FLAG_SAVE,
    "--save": _FLAG_SAVE,
    "-global": _FLAG_GLOBAL,
    "--global": _FLAG_GLOBAL,
    "-reset": _FLAG_RESET,
    "--reset": _FLAG_RESET,
    "-clear": _FLAG_CLEAR,
    "--clear": _FLAG_CLEAR,
}
_MODEL_SOURCE_LABELS = {
    "rule": "自定义规则",
    "session": "当前会话",
    "none": "全局配置",
}


def _split_switch_args(
    raw_args: Sequence[object], *, max_positional: int
) -> tuple[list[str], str, str]:
    """拆分位置参数与 -save/-global 开关。

    Returns:
        (位置参数列表, 模式, 错误提示)。模式为空字符串表示未指定开关。
    """
    positional: list[str] = []
    modes: list[str] = []
    for raw in raw_args:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        flag = _SWITCH_FLAGS.get(text.lower())
        if flag:
            modes.append(flag)
        elif text.startswith("-"):
            return [], "", f"未知参数「{text}」。"
        else:
            positional.append(text)
    if len(modes) > 1:
        return [], "", "参数 -save、-global、-reset、-clear 不能同时使用。"
    if len(positional) > max_positional:
        return [], "", "参数过多，请检查指令格式。"
    return positional, (modes[0] if modes else ""), ""


@dataclass(frozen=True)
class _ModelLookupConfig:
    umo: str | None
    cache_ttl_seconds: float
    max_concurrency: int


class _ModelCache:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str | None], tuple[float, list[str]]] = {}

    def get(self, provider_id: str, umo: str | None, ttl: float) -> list[str] | None:
        if ttl <= 0:
            return None
        entry = self._store.get((provider_id, umo))
        if not entry:
            return None
        timestamp, models = entry
        if time.monotonic() - timestamp > ttl:
            self._store.pop((provider_id, umo), None)
            return None
        return models

    def set(
        self, provider_id: str, umo: str | None, models: list[str], ttl: float
    ) -> None:
        if ttl <= 0:
            return
        self._store[(provider_id, umo)] = (time.monotonic(), list(models))
        self._evict_if_needed()

    def _evict_if_needed(self) -> None:
        if len(self._store) <= MODEL_CACHE_MAX_ENTRIES:
            return
        # Drop oldest entries first when cache grows too large.
        overflow = len(self._store) - MODEL_CACHE_MAX_ENTRIES
        for key, _ in sorted(
            self._store.items(),
            key=lambda item: item[1][0],
        )[:overflow]:
            self._store.pop(key, None)

    def invalidate(
        self, provider_id: str | None = None, *, umo: str | None = None
    ) -> None:
        if provider_id is None:
            self._store.clear()
            return
        if umo is not None:
            self._store.pop((provider_id, umo), None)
            return
        stale_keys = [
            cache_key for cache_key in self._store if cache_key[0] == provider_id
        ]
        for cache_key in stale_keys:
            self._store.pop(cache_key, None)


class ProviderCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context
        self._model_cache = _ModelCache()
        self._register_provider_change_hook()

    def _register_provider_change_hook(self) -> None:
        set_change_callback = getattr(
            self.context.provider_manager,
            "set_provider_change_callback",
            None,
        )
        if callable(set_change_callback):
            set_change_callback(self._on_provider_manager_changed)
            return
        register_change_hook = getattr(
            self.context.provider_manager,
            "register_provider_change_hook",
            None,
        )
        if callable(register_change_hook):
            register_change_hook(self._on_provider_manager_changed)

    def invalidate_provider_models_cache(
        self, provider_id: str | None = None, *, umo: str | None = None
    ) -> None:
        """Public hook for cache invalidation on external provider config changes."""
        self._model_cache.invalidate(provider_id, umo=umo)

    def _on_provider_manager_changed(
        self,
        provider_id: str,
        provider_type: ProviderType,
        umo: str | None,
    ) -> None:
        if provider_type == ProviderType.CHAT_COMPLETION:
            self.invalidate_provider_models_cache(provider_id, umo=umo)

    def _get_provider_settings(self, umo: str | None) -> dict:
        if not umo:
            return {}
        try:
            return self.context.get_config(umo).get("provider_settings", {}) or {}
        except Exception as e:
            logger.debug(
                "读取 provider_settings 失败，使用默认值: %s",
                safe_error("", e),
            )
            return {}

    def _get_model_cache_ttl(self, umo: str | None) -> float:
        settings = self._get_provider_settings(umo)
        raw = settings.get(
            MODEL_LIST_CACHE_TTL_KEY,
            MODEL_LIST_CACHE_TTL_SECONDS_DEFAULT,
        )
        try:
            return max(float(raw), 0.0)
        except Exception as e:
            logger.debug(
                "读取 %s 失败，回退默认值 %r: %s",
                MODEL_LIST_CACHE_TTL_KEY,
                MODEL_LIST_CACHE_TTL_SECONDS_DEFAULT,
                safe_error("", e),
            )
            return MODEL_LIST_CACHE_TTL_SECONDS_DEFAULT

    def _get_model_lookup_concurrency(self, umo: str | None) -> int:
        settings = self._get_provider_settings(umo)
        raw = settings.get(
            MODEL_LOOKUP_MAX_CONCURRENCY_KEY,
            MODEL_LOOKUP_MAX_CONCURRENCY_DEFAULT,
        )
        try:
            value = int(raw)
        except Exception as e:
            logger.debug(
                "读取 %s 失败，回退默认值 %r: %s",
                MODEL_LOOKUP_MAX_CONCURRENCY_KEY,
                MODEL_LOOKUP_MAX_CONCURRENCY_DEFAULT,
                safe_error("", e),
            )
            value = MODEL_LOOKUP_MAX_CONCURRENCY_DEFAULT
        return min(max(value, 1), MODEL_LOOKUP_MAX_CONCURRENCY_UPPER_BOUND)

    def _get_model_lookup_config(self, umo: str | None) -> _ModelLookupConfig:
        return _ModelLookupConfig(
            umo=umo,
            cache_ttl_seconds=self._get_model_cache_ttl(umo),
            max_concurrency=self._get_model_lookup_concurrency(umo),
        )

    def _resolve_model_name(
        self,
        model_name: str,
        models: Sequence[str],
    ) -> str | None:
        """Resolve model name with precedence:
        exact > case-insensitive > provider-qualified suffix.
        """
        requested = model_name.strip()
        if not requested:
            return None

        requested_norm = requested.casefold()

        # exact / case-insensitive match
        for candidate in models:
            if candidate == requested or candidate.casefold() == requested_norm:
                return candidate

        # provider-qualified suffix match:
        # e.g. candidate `openai/gpt-4o` should match requested `gpt-4o`.
        for candidate in models:
            cand_norm = candidate.casefold()
            if cand_norm.endswith(f"/{requested_norm}") or cand_norm.endswith(
                f":{requested_norm}"
            ):
                return candidate

        return None

    def _provider_entry_label(self, provider: Provider) -> str:
        """用户可见的提供商配置项名：完整 ID，来源不同时附注来源。"""
        entry_id = str(provider.meta().id or "")
        provider_config = getattr(provider, "provider_config", {}) or {}
        return self._provider_label_for_config(entry_id, provider_config)

    def _provider_label_for_id(self, provider_id: str) -> str:
        provider_config = (
            self.context.provider_manager.get_provider_config_by_id(provider_id)
            or {}
        )
        return self._provider_label_for_config(provider_id, provider_config)

    @staticmethod
    def _provider_label_for_config(
        provider_id: str, provider_config: dict
    ) -> str:
        source_id = str(provider_config.get("provider_source_id", "") or "")
        if source_id and source_id != provider_id:
            return f"{provider_id}（来源 {source_id}）"
        return provider_id

    async def _sync_provider_entry_id(self, provider_id: str) -> str:
        """把自动生成但已与 model 失配的配置项 ID 修正回来。

        修好后返回新 ID；不需要修或不是自动命名的配置项返回原 ID。
        """
        provider_manager = self.context.provider_manager
        provider_config = provider_manager.get_provider_config_by_id(provider_id)
        if not provider_config or not is_auto_generated_provider_id(
            provider_config
        ):
            return provider_id
        expected_id = expected_provider_id(provider_config)
        if not expected_id or expected_id == provider_id:
            return provider_id
        return await provider_manager.update_provider(
            provider_id, copy.deepcopy(provider_config)
        )

    def _find_provider_entry_by_id(self, raw: str) -> Provider | None:
        """按提供商配置项 ID 精确匹配，例如 /model ldmapi/glm-5.3。"""
        target = raw.strip()
        if not target:
            return None
        for provider in self.context.get_all_providers():
            if str(provider.meta().id or "") == target:
                return provider
        target_lower = target.casefold()
        for provider in self.context.get_all_providers():
            if str(provider.meta().id or "").casefold() == target_lower:
                return provider
        return None

    def _find_provider_entry_by_configured_model(
        self, model_name: str, current_provider_id: str
    ) -> Provider | None:
        """按配置项里的 model 字段精确匹配，优先当前配置项。"""
        target = model_name.strip().casefold()
        if not target:
            return None
        matches: list[Provider] = []
        for provider in self.context.get_all_providers():
            configured = str(provider.get_model() or "").strip().casefold()
            if configured and configured == target:
                matches.append(provider)
        if not matches:
            return None
        for provider in matches:
            if str(provider.meta().id or "") == current_provider_id:
                return provider
        return matches[0]

    async def _get_provider_models(
        self,
        provider: Provider,
        *,
        config: _ModelLookupConfig,
        use_cache: bool = True,
    ) -> list[str]:
        provider_id = provider.meta().id
        ttl_seconds = config.cache_ttl_seconds
        umo = config.umo
        if use_cache:
            cached = self._model_cache.get(provider_id, umo, ttl_seconds)
            if cached is not None:
                return cached

        models = list(await provider.get_models())
        if use_cache:
            self._model_cache.set(provider_id, umo, models, ttl_seconds)
        return models

    async def _get_models_or_reply_error(
        self,
        message: AstrMessageEvent,
        prov: Provider,
        config: _ModelLookupConfig,
        *,
        error_prefix: str,
        disable_t2i: bool = False,
        warning_log: str | None = None,
    ) -> list[str] | None:
        try:
            return await self._get_provider_models(prov, config=config)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if warning_log is not None:
                logger.warning(
                    warning_log,
                    prov.meta().id,
                    safe_error("", e),
                )
            result = MessageEventResult().message(safe_error(error_prefix, e))
            if disable_t2i:
                result = result.use_t2i(False)
            message.set_result(result)
            return None

    def _log_reachability_failure(
        self,
        provider,
        provider_capability_type: ProviderType | None,
        err_code: str,
        err_reason: str,
    ) -> None:
        """记录不可达原因到日志。"""
        meta = provider.meta()
        logger.warning(
            "Provider reachability check failed: id=%s type=%s code=%s reason=%s",
            meta.id,
            provider_capability_type.name if provider_capability_type else "unknown",
            err_code,
            err_reason,
        )

    async def _test_provider_capability(self, provider):
        """测试单个 provider 的可用性"""
        meta = provider.meta()
        provider_capability_type = meta.provider_type

        try:
            await provider.test()
            return True, None, None
        except Exception as e:
            err_code = "TEST_FAILED"
            err_reason = safe_error("", e)
            self._log_reachability_failure(
                provider, provider_capability_type, err_code, err_reason
            )
            return False, err_code, err_reason

    async def _find_provider_for_model(
        self,
        model_name: str,
        *,
        exclude_provider_id: str | None = None,
        config: _ModelLookupConfig,
        use_cache: bool = True,
    ) -> tuple[Provider | None, str | None]:
        all_providers = []
        for provider in self.context.get_all_providers():
            provider_meta = provider.meta()
            if provider_meta.provider_type != ProviderType.CHAT_COMPLETION:
                continue
            if (
                exclude_provider_id is not None
                and provider_meta.id == exclude_provider_id
            ):
                continue
            all_providers.append(provider)
        if not all_providers:
            return None, None

        semaphore = asyncio.Semaphore(config.max_concurrency)

        async def fetch_models(
            provider: Provider,
        ) -> tuple[Provider, list[str] | None, str | None]:
            async with semaphore:
                try:
                    models = await self._get_provider_models(
                        provider,
                        config=config,
                        use_cache=use_cache,
                    )
                    return provider, models, None
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    err = safe_error("", e)
                    logger.debug(
                        "跨提供商查找模型 %s 获取 %s 模型列表失败: %s",
                        model_name,
                        provider.meta().id,
                        err,
                    )
                    return provider, None, err

        results = await asyncio.gather(
            *(fetch_models(provider) for provider in all_providers)
        )
        failed_provider_errors: list[tuple[str, str]] = []
        for provider, models, err in results:
            if err is not None:
                failed_provider_errors.append((provider.meta().id, err))
                continue
            if models is None:
                continue

            matched_model_name = self._resolve_model_name(model_name, models)
            if matched_model_name is not None:
                return provider, matched_model_name

        if failed_provider_errors and len(failed_provider_errors) == len(all_providers):
            failed_ids = ",".join(
                provider_id for provider_id, _ in failed_provider_errors
            )
            logger.error(
                "跨提供商查找模型 %s 时，所有 %d 个提供商的 get_models() 均失败: %s。请检查配置或网络",
                model_name,
                len(all_providers),
                failed_ids,
            )
        elif failed_provider_errors:
            logger.debug(
                "跨提供商查找模型 %s 时有 %d 个提供商获取模型失败: %s",
                model_name,
                len(failed_provider_errors),
                ",".join(
                    f"{provider_id}({error})"
                    for provider_id, error in failed_provider_errors
                ),
            )
        return None, None

    async def provider(
        self,
        event: AstrMessageEvent,
        idx: str | int | None = None,
        idx2: int | None = None,
        flag: str | None = None,
    ) -> None:
        """查看或者切换 LLM Provider"""
        positional, mode, arg_err = _split_switch_args(
            (idx, idx2, flag),
            max_positional=2,
        )
        if arg_err:
            event.set_result(
                MessageEventResult().message(
                    f"{arg_err}\n用法: {获取第一个唤醒词()}provider "
                    "[序号|tts|stt <序号>] [-save|-global|-reset|-clear]"
                ).use_t2i(False)
            )
            return

        umo = event.unified_msg_origin
        # /provider test 时进行可达性检测，/provider 无参数时不检测
        reachability_check_enabled = bool(positional) and positional[0] == "test"

        if not positional or reachability_check_enabled:
            if mode:
                if reachability_check_enabled:
                    event.set_result(
                        MessageEventResult().message(
                            "test 不支持 -save/-global/-reset 参数。"
                        ).use_t2i(False)
                    )
                    return
                if mode == _FLAG_RESET:
                    await self._reset_provider_choice(
                        event, ProviderType.CHAT_COMPLETION
                    )
                    return
                if mode == _FLAG_CLEAR:
                    await self._clear_all_session_choices(event)
                    return
                provider_using = self.context.get_using_provider(umo=umo)
                if provider_using is None:
                    event.set_result(
                        MessageEventResult().message("未找到任何 LLM 提供商。请先配置。")
                    )
                    return
                await self._apply_provider_choice(
                    event,
                    ProviderType.CHAT_COMPLETION,
                    provider_using,
                    mode,
                )
                return

            parts = ["## 载入的 LLM 提供商\n"]

            # 获取所有类型的提供商
            llms = list(self.context.get_all_providers())
            ttss = self.context.get_all_tts_providers()
            stts = self.context.get_all_stt_providers()

            # 构造待检测列表: [(provider, type_label), ...]
            all_providers = []
            all_providers.extend([(p, "llm") for p in llms])
            all_providers.extend([(p, "tts") for p in ttss])
            all_providers.extend([(p, "stt") for p in stts])

            # 并发测试连通性
            if reachability_check_enabled:
                if all_providers:
                    await event.send(
                        MessageEventResult().message(
                            "正在进行提供商可达性测试，请稍候..."
                        )
                    )
                check_results = await asyncio.gather(
                    *[self._test_provider_capability(p) for p, _ in all_providers],
                    return_exceptions=True,
                )
            else:
                # 用 None 表示未检测
                check_results = [None for _ in all_providers]

            # 整合结果
            display_data = []
            for (p, p_type), reachable in zip(all_providers, check_results):
                meta = p.meta()
                error_code = None

                if isinstance(reachable, asyncio.CancelledError):
                    raise reachable
                if isinstance(reachable, Exception):
                    # 异常情况下兜底处理，避免单个 provider 导致列表失败
                    self._log_reachability_failure(
                        p,
                        None,
                        reachable.__class__.__name__,
                        safe_error("", reachable),
                    )
                    reachable_flag = False
                    error_code = reachable.__class__.__name__
                elif isinstance(reachable, tuple):
                    reachable_flag, error_code, _ = reachable
                else:
                    reachable_flag = reachable

                # 显示完整配置项 ID，避免同来源多模型都显示成同一个前缀
                if p_type == "llm":
                    info = (
                        f"{self._provider_entry_label(p)} · "
                        f"模型 {meta.model or '未设置'}"
                    )
                else:
                    info = self._provider_entry_label(p)

                # 确定状态标记
                if reachable_flag is True:
                    mark = " ✅"
                elif reachable_flag is False:
                    if error_code:
                        mark = f" ❌(错误码: {error_code})"
                    else:
                        mark = " ❌"
                else:
                    mark = ""  # 不支持检测时不显示标记

                display_data.append(
                    {
                        "type": p_type,
                        "info": info,
                        "mark": mark,
                        "provider": p,
                    }
                )

            # 分组输出
            # 1. LLM
            llm_data = [d for d in display_data if d["type"] == "llm"]
            for i, d in enumerate(llm_data):
                line = f"{i + 1}. {d['info']}{d['mark']}"
                provider_using = self.context.get_using_provider(umo=umo)
                if (
                    provider_using
                    and provider_using.meta().id == d["provider"].meta().id
                ):
                    line += " (当前使用)"
                parts.append(line + "\n")

            # 2. TTS
            tts_data = [d for d in display_data if d["type"] == "tts"]
            if tts_data:
                parts.append("\n## 载入的 TTS 提供商\n")
                for i, d in enumerate(tts_data):
                    line = f"{i + 1}. {d['info']}{d['mark']}"
                    tts_using = self.context.get_using_tts_provider(umo=umo)
                    if tts_using and tts_using.meta().id == d["provider"].meta().id:
                        line += " (当前使用)"
                    parts.append(line + "\n")

            # 3. STT
            stt_data = [d for d in display_data if d["type"] == "stt"]
            if stt_data:
                parts.append("\n## 载入的 STT 提供商\n")
                for i, d in enumerate(stt_data):
                    line = f"{i + 1}. {d['info']}{d['mark']}"
                    stt_using = self.context.get_using_stt_provider(umo=umo)
                    if stt_using and stt_using.meta().id == d["provider"].meta().id:
                        line += " (当前使用)"
                    parts.append(line + "\n")

            parts.append(
                f"\n使用 {获取第一个唤醒词()}provider <序号|配置项ID> "
                "切换当前会话的 LLM 提供商；"
                "加 -save 保存到自定义规则，加 -global 保存为全局默认，"
                "加 -reset 重置为全局默认，加 -clear 清除内存中所有临时选择。"
            )
            ret = "".join(parts)

            if ttss:
                ret += (
                    f"\n使用 {获取第一个唤醒词()}provider tts <序号> 切换 TTS 提供商，"
                    "加 -reset 重置为全局默认。"
                )
            if stts:
                ret += (
                    f"\n使用 {获取第一个唤醒词()}provider stt <序号> 切换 STT 提供商，"
                    "加 -reset 重置为全局默认。"
                )
            if not reachability_check_enabled:
                ret += f"\n已跳过提供商可达性检测，如需检测请使用 {获取第一个唤醒词()}provider test。"

            event.set_result(MessageEventResult().message(ret))
        elif positional[0] == "tts":
            if mode == _FLAG_RESET:
                if len(positional) > 1:
                    event.set_result(
                        MessageEventResult().message(
                            f"重置不需要指定序号，请使用 {获取第一个唤醒词()}provider tts -reset。"
                        ).use_t2i(False)
                    )
                    return
                await self._reset_provider_choice(
                    event, ProviderType.TEXT_TO_SPEECH
                )
                return
            if mode == _FLAG_CLEAR:
                event.set_result(
                    MessageEventResult().message(
                        "清除会作用于内存中所有临时选择，不需要指定提供商类型，"
                        f"请使用 {获取第一个唤醒词()}provider -clear。"
                    ).use_t2i(False)
                )
                return
            if len(positional) < 2:
                event.set_result(MessageEventResult().message(
                    f"使用方法：{获取第一个唤醒词()}provider tts <序号> [-save|-global]，"
                    f"或 {获取第一个唤醒词()}provider tts -reset 重置为全局默认。"
                ))
                return
            num, err = 转整数(positional[1], "TTS 提供商序号", 最小值=1)
            if err:
                event.set_result(MessageEventResult().message(err))
                return
            tts_providers = self.context.get_all_tts_providers()
            if num > len(tts_providers):
                event.set_result(MessageEventResult().message(
                    f"TTS 提供商序号超出范围，共 {len(tts_providers)} 个。"
                ))
                return
            await self._apply_provider_choice(
                event,
                ProviderType.TEXT_TO_SPEECH,
                tts_providers[num - 1],
                mode,
            )
        elif positional[0] == "stt":
            if mode == _FLAG_RESET:
                if len(positional) > 1:
                    event.set_result(
                        MessageEventResult().message(
                            f"重置不需要指定序号，请使用 {获取第一个唤醒词()}provider stt -reset。"
                        ).use_t2i(False)
                    )
                    return
                await self._reset_provider_choice(
                    event, ProviderType.SPEECH_TO_TEXT
                )
                return
            if mode == _FLAG_CLEAR:
                event.set_result(
                    MessageEventResult().message(
                        "清除会作用于内存中所有临时选择，不需要指定提供商类型，"
                        f"请使用 {获取第一个唤醒词()}provider -clear。"
                    ).use_t2i(False)
                )
                return
            if len(positional) < 2:
                event.set_result(MessageEventResult().message(
                    f"使用方法：{获取第一个唤醒词()}provider stt <序号> [-save|-global]，"
                    f"或 {获取第一个唤醒词()}provider stt -reset 重置为全局默认。"
                ))
                return
            num, err = 转整数(positional[1], "STT 提供商序号", 最小值=1)
            if err:
                event.set_result(MessageEventResult().message(err))
                return
            stt_providers = self.context.get_all_stt_providers()
            if num > len(stt_providers):
                event.set_result(MessageEventResult().message(
                    f"STT 提供商序号超出范围，共 {len(stt_providers)} 个。"
                ))
                return
            await self._apply_provider_choice(
                event,
                ProviderType.SPEECH_TO_TEXT,
                stt_providers[num - 1],
                mode,
            )
        else:
            if mode == _FLAG_RESET:
                event.set_result(
                    MessageEventResult().message(
                        f"重置不需要指定提供商，请使用 {获取第一个唤醒词()}provider -reset。"
                    ).use_t2i(False)
                )
                return
            if mode == _FLAG_CLEAR:
                event.set_result(
                    MessageEventResult().message(
                        f"清除不需要指定提供商，请使用 {获取第一个唤醒词()}provider -clear。"
                    ).use_t2i(False)
                )
                return
            if len(positional) > 1:
                event.set_result(
                    MessageEventResult().message(
                        "参数过多，请检查指令格式。"
                    ).use_t2i(False)
                )
                return
            # 先按配置项 ID 精确匹配：/provider ldmapi/glm-5.3
            entry = self._find_provider_entry_by_id(positional[0])
            if entry is not None:
                await self._apply_provider_choice(
                    event,
                    ProviderType.CHAT_COMPLETION,
                    entry,
                    mode,
                )
                return
            # 再尝试将位置参数转为整数（兼容框架传入 str "3" 或 int 3）
            num, err = 转整数(positional[0], "提供商序号", 最小值=1)
            if err:
                event.set_result(MessageEventResult().message(
                    f"无效的参数「{positional[0]}」。\n"
                    f"使用方法：{获取第一个唤醒词()}provider <序号|配置项ID> [-save|-global|-reset|-clear] "
                    "切换 LLM 提供商，"
                    f"{获取第一个唤醒词()}provider test 测试连通性，"
                    f"{获取第一个唤醒词()}provider tts/stt <序号> 切换 TTS/STT 提供商。"
                ))
                return
            llm_providers = list(self.context.get_all_providers())
            if num > len(llm_providers):
                event.set_result(MessageEventResult().message(
                    f"LLM 提供商序号超出范围，共 {len(llm_providers)} 个。"
                ))
                return
            await self._apply_provider_choice(
                event,
                ProviderType.CHAT_COMPLETION,
                llm_providers[num - 1],
                mode,
            )

    async def _clear_all_session_choices(self, event: AstrMessageEvent) -> None:
        """手动清除内存中所有会话的临时模型/提供商选择。"""
        count = clear_all_session_overrides()
        if count:
            msg = (
                f"已清除内存中所有临时会话选择（共 {count} 个会话），"
                "这些会话已恢复跟随自定义规则或全局配置。"
            )
        else:
            msg = "内存中没有临时会话选择，无需清除。"
        event.set_result(MessageEventResult().message(msg).use_t2i(False))

    async def _reset_provider_choice(
        self,
        event: AstrMessageEvent,
        provider_type: ProviderType,
    ) -> None:
        """清除规则/会话里的提供商选择，恢复跟随全局配置。"""
        umo = event.unified_msg_origin
        try:
            removed = await reset_selection(umo, provider_type)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            event.set_result(
                MessageEventResult().message(
                    safe_error("重置提供商选择失败: ", e)
                ).use_t2i(False)
            )
            return

        type_label = {
            ProviderType.CHAT_COMPLETION: "提供商",
            ProviderType.TEXT_TO_SPEECH: "TTS 提供商",
            ProviderType.SPEECH_TO_TEXT: "STT 提供商",
        }.get(provider_type, "提供商")
        if removed:
            msg = (
                f"已重置当前会话的{type_label}选择（含自定义规则里的选择），"
                "恢复跟随全局配置。"
            )
        else:
            msg = f"当前会话没有自定义的{type_label}选择，已经跟随全局配置。"

        if provider_type == ProviderType.CHAT_COMPLETION:
            prov = self.context.get_using_provider(umo)
            if prov:
                curr_model, source = await resolve_effective_model(
                    umo, prov.meta().id, prov.get_model()
                )
                label = _MODEL_SOURCE_LABELS.get(source, source)
                msg += (
                    f"\n当前提供商: [{self._provider_entry_label(prov)}]"
                    f"\n当前模型: 「{curr_model or '无'}」（来源: {label}）"
                )
        elif provider_type == ProviderType.TEXT_TO_SPEECH:
            prov = self.context.get_using_tts_provider(umo)
            if prov:
                msg += f"\n当前 TTS 提供商: [{self._provider_entry_label(prov)}]"
        elif provider_type == ProviderType.SPEECH_TO_TEXT:
            prov = self.context.get_using_stt_provider(umo)
            if prov:
                msg += f"\n当前 STT 提供商: [{self._provider_entry_label(prov)}]"

        if await has_session_rule(umo):
            msg += "\n自定义规则的其他设置（人格、插件、知识库等）保持不变。"
        event.set_result(MessageEventResult().message(msg).use_t2i(False))

    async def _apply_provider_choice(
        self,
        event: AstrMessageEvent,
        provider_type: ProviderType,
        provider: Provider,
        mode: str,
    ) -> None:
        """按模式落盘提供商选择：规则 / 会话 / 全局。"""
        umo = event.unified_msg_origin
        prefix = 获取第一个唤醒词()
        original_id = str(provider.meta().id or "")
        try:
            provider_id = await self._sync_provider_entry_id(original_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            event.set_result(
                MessageEventResult().message(
                    safe_error("同步提供商配置项 ID 失败: ", e)
                )
            )
            return
        display = self._provider_label_for_id(provider_id)
        sync_note = ""
        if provider_id != original_id:
            sync_note = f"\n已同步配置项 ID: {original_id} -> {provider_id}"

        if mode == _FLAG_GLOBAL:
            try:
                await self._save_global_provider(provider_type, provider_id, umo)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                event.set_result(
                    MessageEventResult().message(
                        safe_error("保存全局提供商失败: ", e)
                    )
                )
                return
            msg = (
                f"已将配置项 [{display}] 设为全局默认。\n"
                "该会话若配置了自定义规则，规则仍然优先。"
            )
        else:
            try:
                if mode == _FLAG_SAVE or await has_session_rule(umo):
                    await set_rule_provider(umo, provider_type, provider_id)
                    msg = (
                        "已将提供商写入当前会话的自定义规则并立即生效。\n"
                        f"当前提供商: [{display}]"
                    )
                else:
                    await set_session_provider(umo, provider_type, provider_id)
                    msg = (
                        "已切换当前会话的提供商（仅本会话生效，重启后失效）。\n"
                        f"当前提供商: [{display}]\n"
                        f"如需持久化为自定义规则请使用 {prefix}provider <序号> -save；"
                        "如需设为全局默认请使用 -global。"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                event.set_result(
                    MessageEventResult().message(
                        safe_error("切换提供商失败: ", e)
                    )
                )
                return

        if provider_type == ProviderType.CHAT_COMPLETION:
            effective_model, _source = await resolve_session_model(
                umo, provider_id
            )
            effective_model = provider.get_model() or effective_model
            if effective_model:
                msg += f"\n当前模型: [{effective_model}]"
        msg += sync_note
        event.set_result(MessageEventResult().message(msg).use_t2i(False))

    async def _save_global_provider(
        self,
        provider_type: ProviderType,
        provider_id: str,
        umo: str,
    ) -> None:
        """把提供商写进全局配置，并清除本会话的会话级覆盖。"""
        config = self.context.get_config()
        if provider_type == ProviderType.CHAT_COMPLETION:
            config.setdefault("provider_settings", {})[
                "default_provider_id"
            ] = provider_id
        elif provider_type == ProviderType.TEXT_TO_SPEECH:
            config.setdefault("provider_tts_settings", {})[
                "provider_id"
            ] = provider_id
        elif provider_type == ProviderType.SPEECH_TO_TEXT:
            config.setdefault("provider_stt_settings", {})[
                "provider_id"
            ] = provider_id
        else:
            raise ValueError(f"不支持的提供商类型: {provider_type}")
        await config.save_config_async()
        await self.context.provider_manager.set_provider(
            provider_id=provider_id,
            provider_type=provider_type,
            umo=None,
        )
        await clear_session_provider(umo, provider_type)

    async def _save_global_model(
        self, provider_id: str, model: str, umo: str
    ) -> tuple[bool, str]:
        """把配置项设为全局默认对话模型；不修改配置项的 model。

        Returns:
            (该配置项是否本来就是全局默认, 配置项里的模型)
        """
        provider_manager = self.context.provider_manager
        provider_config = provider_manager.get_provider_config_by_id(provider_id)
        if not provider_config:
            raise ValueError(f"未找到提供商 [{provider_id}] 的配置")

        configured_model = str(provider_config.get("model", "") or "").strip()
        if configured_model != model:
            raise ValueError(
                f"配置项 [{provider_id}] 的模型是 [{configured_model}]，"
                f"与目标模型 [{model}] 不一致。"
            )
        config = self.context.get_config()
        settings = config.setdefault("provider_settings", {})
        provider_already = settings.get("default_provider_id") == provider_id

        if not provider_already:
            settings["default_provider_id"] = provider_id
            await config.save_config_async()
            await provider_manager.set_provider(
                provider_id=provider_id,
                provider_type=ProviderType.CHAT_COMPLETION,
                umo=None,
            )
        await clear_session_provider(umo, ProviderType.CHAT_COMPLETION)
        return provider_already, configured_model

    async def _reset_model_choice(self, message: AstrMessageEvent) -> None:
        """清除规则/会话里的模型选择，恢复跟随全局配置。"""
        umo = message.unified_msg_origin
        try:
            removed = await reset_selection(umo, ProviderType.CHAT_COMPLETION)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            message.set_result(
                MessageEventResult().message(
                    safe_error("重置模型选择失败: ", e)
                ).use_t2i(False)
            )
            return

        if removed:
            msg = (
                "已重置当前会话的模型选择（含自定义规则里的选择），"
                "恢复跟随全局配置。"
            )
        else:
            msg = "当前会话没有自定义的模型选择，已经跟随全局配置。"

        prov = self.context.get_using_provider(umo)
        if prov:
            curr_model, source = await resolve_effective_model(
                umo, prov.meta().id, prov.get_model()
            )
            label = _MODEL_SOURCE_LABELS.get(source, source)
            msg += f"\n当前模型: 「{curr_model or '无'}」（来源: {label}）"
        if await has_session_rule(umo):
            msg += "\n自定义规则的其他设置（人格、插件、知识库等）保持不变。"
        message.set_result(MessageEventResult().message(msg).use_t2i(False))

    async def _show_model_list(
        self,
        message: AstrMessageEvent,
        prov: Provider,
        config: _ModelLookupConfig,
    ) -> None:
        models = await self._get_models_or_reply_error(
            message,
            prov,
            config,
            error_prefix="获取模型列表失败: ",
            disable_t2i=True,
        )
        if models is None:
            return
        parts = [
            f"当前提供商配置项: 「{self._provider_entry_label(prov)}」",
            "\n下面列出了此配置项可用模型:",
        ]
        for i, model in enumerate(models, 1):
            parts.append(f"\n{i}. {model}")

        curr_model, source = await resolve_effective_model(
            message.unified_msg_origin, prov.meta().id, prov.get_model()
        )
        parts.append(
            f"\n当前模型: 「{curr_model or '无'}」"
            f"（来源: {_MODEL_SOURCE_LABELS.get(source, source)}）"
        )
        prefix = 获取第一个唤醒词()
        parts.append(
            f"\n提示: 按编号只改当前配置项的模型；输入模型名会优先匹配"
            f"ID 或 model 相同的配置项。\n"
            f"使用 {prefix}model <模型名/编号> 切换当前会话"
            "（会话有自定义规则时写入规则，否则仅本会话生效，重启后失效）；"
            "加 -save 保存到自定义规则，加 -global 保存为全局默认，"
            "加 -reset 重置为全局默认，加 -clear 清除内存中所有临时选择。"
        )
        message.set_result(
            MessageEventResult().message("".join(parts)).use_t2i(False)
        )

    async def _resolve_model_target(
        self,
        message: AstrMessageEvent,
        prov: Provider,
        config: _ModelLookupConfig,
        raw_target: str,
    ) -> tuple[Provider | None, str | None]:
        """把用户输入解析成 (目标提供商, 目标模型)。失败时已回执。"""
        num, err = 转整数(raw_target, "模型编号", 最小值=1)
        if not err:
            models = await self._get_models_or_reply_error(
                message,
                prov,
                config,
                error_prefix="获取模型列表失败: ",
            )
            if models is None:
                return None, None
            if num > len(models):
                message.set_result(
                    MessageEventResult().message(
                        f"模型序号超出范围，共 {len(models)} 个。"
                    )
                )
                return None, None
            return await self._ensure_target_entry(
                message, prov, models[num - 1]
            )

        model_name, name_err = 校验非空(raw_target, "模型名")
        if name_err:
            message.set_result(MessageEventResult().message(name_err))
            return None, None

        # 1) 先按提供商配置项 ID 精确匹配：/model ldmapi/glm-5.3
        entry = self._find_provider_entry_by_id(model_name)
        if entry is not None:
            configured_model = str(entry.get_model() or "").strip()
            if not configured_model:
                message.set_result(
                    MessageEventResult().message(
                        f"配置项 [{self._provider_entry_label(entry)}] "
                        "没有设置模型，无法切换。"
                    )
                )
                return None, None
            return entry, configured_model

        # 2) 再按配置项里配置的 model 精确匹配：/model glm-5.3
        #    这样“一个模型一个配置项”的场景会切到对应配置项，而不是改当前项的 model。
        entry = self._find_provider_entry_by_configured_model(
            model_name, str(prov.meta().id or "")
        )
        if entry is not None:
            return entry, str(entry.get_model()).strip()

        models = await self._get_models_or_reply_error(
            message,
            prov,
            config,
            error_prefix="获取当前提供商模型列表失败: ",
            warning_log="获取当前提供商 %s 模型列表失败，停止跨提供商查找: %s",
        )
        if models is None:
            return None, None

        matched_model_name = self._resolve_model_name(model_name, models)
        if matched_model_name is not None:
            return await self._ensure_target_entry(
                message, prov, matched_model_name
            )

        target_prov, matched_target_model_name = await self._find_provider_for_model(
            model_name,
            exclude_provider_id=prov.meta().id,
            config=config,
        )
        if target_prov is None or matched_target_model_name is None:
            message.set_result(
                MessageEventResult().message(
                    f"模型 [{model_name}] 未在任何已配置的提供商中找到，"
                    "或所有提供商模型列表获取失败，请检查配置或网络后重试。"
                )
            )
            return None, None
        return await self._ensure_target_entry(
            message, target_prov, matched_target_model_name
        )

    async def _ensure_target_entry(
        self,
        message: AstrMessageEvent,
        template_prov: Provider,
        model: str,
    ) -> tuple[Provider | None, str | None]:
        """确保目标模型有对应的 ``{来源}/{模型}`` 配置项。

        上游模型列表里的模型不一定已经添加成配置项；没有就按来源模板
        创建一个，保证配置项 ID 始终等于 ``来源/模型``。
        """
        template_id = str(template_prov.meta().id or "")
        configured_model = str(template_prov.get_model() or "").strip()
        if configured_model == model:
            return template_prov, model

        provider_manager = self.context.provider_manager
        try:
            new_id, created = await provider_manager.ensure_provider_entry(
                template_id, model
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            message.set_result(
                MessageEventResult().message(
                    safe_error("创建模型配置项失败: ", e)
                )
            )
            return None, None

        new_prov = provider_manager.inst_map.get(new_id)
        if new_prov is None:
            message.set_result(
                MessageEventResult().message(
                    f"已创建配置项 [{new_id}]，但未加载到提供商实例，"
                    "请检查配置后重试。"
                )
            )
            return None, None
        if created:
            logger.info("已自动创建提供商配置项: %s", new_id)
        return new_prov, model

    async def _apply_model_choice(
        self,
        message: AstrMessageEvent,
        target_prov: Provider,
        model: str,
        mode: str,
    ) -> None:
        """按模式落盘模型选择：自定义规则 / 当前会话 / 全局。"""
        umo = message.unified_msg_origin
        prefix = 获取第一个唤醒词()
        original_id = str(target_prov.meta().id or "")
        try:
            provider_id = await self._sync_provider_entry_id(original_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            message.set_result(
                MessageEventResult().message(
                    safe_error("同步提供商配置项 ID 失败: ", e)
                )
            )
            return
        display = self._provider_label_for_id(provider_id)
        sync_note = ""
        if provider_id != original_id:
            sync_note = f"\n已同步配置项 ID: {original_id} -> {provider_id}"

        if mode == _FLAG_GLOBAL:
            try:
                provider_already, entry_model = await self._save_global_model(
                    provider_id, model, umo
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                message.set_result(
                    MessageEventResult().message(
                        safe_error("保存全局模型失败: ", e)
                    )
                )
                return
            if provider_already:
                msg = (
                    f"配置项 [{display}]（模型 [{entry_model}]）"
                    "已经是全局默认对话模型，配置没有变化。\n"
                    "该会话的会话级覆盖已清除。"
                )
            else:
                msg = (
                    f"已将配置项 [{display}]（模型 [{entry_model}]）"
                    "设为全局默认对话模型。"
                )
            msg += "\n该会话若配置了自定义规则，规则仍然优先。"
        else:
            try:
                if mode == _FLAG_SAVE or await has_session_rule(umo):
                    await set_rule_selection(umo, provider_id, model)
                    msg = (
                        "已将模型写入当前会话的自定义规则并立即生效。\n"
                        f"当前提供商: [{display}] 当前模型: [{model}]"
                    )
                else:
                    await set_session_selection(umo, provider_id, model)
                    msg = (
                        "已切换当前会话的模型（仅本会话生效，重启后失效）。\n"
                        f"当前提供商: [{display}] 当前模型: [{model}]\n"
                        f"如需持久化为自定义规则请使用 {prefix}model <模型> -save；"
                        "如需设为全局默认请使用 -global。"
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                message.set_result(
                    MessageEventResult().message(
                        safe_error("切换模型失败: ", e)
                    )
                )
                return
        msg += sync_note
        message.set_result(MessageEventResult().message(msg).use_t2i(False))

    async def model_ls(
        self,
        message: AstrMessageEvent,
        idx_or_name: int | str | None = None,
        flag: str | None = None,
    ) -> None:
        """查看或者切换模型"""
        positional, mode, arg_err = _split_switch_args(
            (idx_or_name, flag),
            max_positional=1,
        )
        if arg_err:
            message.set_result(
                MessageEventResult().message(
                    f"{arg_err}\n用法: {获取第一个唤醒词()}model "
                    "[模型名/编号] [-save|-global|-reset|-clear]"
                ).use_t2i(False)
            )
            return

        umo = message.unified_msg_origin
        if mode == _FLAG_CLEAR:
            if positional:
                message.set_result(
                    MessageEventResult().message(
                        f"清除不需要指定模型，请使用 {获取第一个唤醒词()}model -clear。"
                    ).use_t2i(False)
                )
                return
            await self._clear_all_session_choices(message)
            return

        prov = self.context.get_using_provider(umo)
        if not prov:
            message.set_result(
                MessageEventResult().message("未找到任何 LLM 提供商。请先配置。"),
            )
            return
        config = self._get_model_lookup_config(umo)

        if not positional:
            if mode == _FLAG_RESET:
                await self._reset_model_choice(message)
                return
            if mode:
                # 无目标时把当前生效的模型保存到规则/全局
                curr_model, _source = await resolve_session_model(
                    umo, prov.meta().id
                )
                curr_model = prov.get_model() or curr_model
                if not curr_model:
                    message.set_result(
                        MessageEventResult().message(
                            "当前提供商没有可用模型，无法保存。"
                        )
                    )
                    return
                await self._apply_model_choice(message, prov, curr_model, mode)
                return
            await self._show_model_list(message, prov, config)
            return

        if mode == _FLAG_RESET:
            message.set_result(
                MessageEventResult().message(
                    f"重置不需要指定模型，请使用 {获取第一个唤醒词()}model -reset。"
                ).use_t2i(False)
            )
            return

        target_prov, target_model = await self._resolve_model_target(
            message, prov, config, positional[0]
        )
        if target_prov is None or target_model is None:
            return
        await self._apply_model_choice(message, target_prov, target_model, mode)

    async def key(self, message: AstrMessageEvent, index: int | None = None) -> None:
        prov = self.context.get_using_provider(message.unified_msg_origin)
        if not prov:
            message.set_result(
                MessageEventResult().message("未找到任何 LLM 提供商。请先配置。"),
            )
            return

        if index is None:
            keys_data = prov.get_keys()
            curr_key = prov.get_current_key()
            parts = ["Key:"]
            for i, k in enumerate(keys_data, 1):
                parts.append(f"\n{i}. {k[:8]}")

            parts.append(f"\n当前 Key: {curr_key[:8]}")
            curr_model, _source = await resolve_session_model(
                message.unified_msg_origin, prov.meta().id
            )
            parts.append("\n当前模型: " + (prov.get_model() or curr_model or "无"))
            parts.append(f"\n使用 {获取第一个唤醒词()}key <序号> 切换 Key。")

            ret = "".join(parts)
            message.set_result(MessageEventResult().message(ret).use_t2i(False))
        else:
            num, err = 转整数(index, "Key 序号", 最小值=1)
            if err:
                message.set_result(MessageEventResult().message(err))
                return
            keys_data = prov.get_keys()
            if num > len(keys_data):
                message.set_result(
                    MessageEventResult().message(
                        f"Key 序号超出范围，共 {len(keys_data)} 个。"
                    )
                )
                return
            try:
                new_key = keys_data[num - 1]
                prov.set_key(new_key)
                self.invalidate_provider_models_cache(
                    prov.meta().id,
                    umo=message.unified_msg_origin,
                )
                message.set_result(MessageEventResult().message("切换 Key 成功。"))
            except Exception as e:
                message.set_result(
                    MessageEventResult().message(
                        safe_error("切换 Key 未知错误: ", e)
                    ),
                )
                return
