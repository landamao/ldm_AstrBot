"""模型引用管理(ModelUsageService)回归测试。

覆盖:
- 主配置扫描:CONFIG_METADATA_3 的 _special 模型选择项被识别,对话模型链合并主模型与回退列表
- 插件扫描:schema 带 _special 的模型选择项、无标记时按「值命中提供商 ID」的疑似引用兜底
- 批量替换:单值/列表/对话模型链的 from→to 替换,主模型变更后回退列表去重
- 保存回调:插件走 plugin_manager.reload,主配置走 reload_pipeline_scheduler
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from astrbot.core.config.default import CONFIG_METADATA_3
from astrbot.core.star.star import StarMetadata, star_registry
from astrbot.dashboard.services.model_usage_service import ModelUsageService


class _FakeAstrBotConfig(dict):
    """带 save_config 记录的配置替身，避免测试触盘。"""

    def __init__(self, data: dict):
        super().__init__(data)
        self.schema: dict = {}
        self.config_path: str = ""
        self.saved = 0

    def save_config(self, *args, **kwargs) -> None:
        self.saved += 1


def _make_core_lifecycle(core_conf: dict, providers: list[dict]):
    """构造不含真实运行时的最小 core_lifecycle 替身。

    用 MagicMock 兜底 dashboard 各服务构造期的零散属性访问，
    被测路径（acm/provider_manager/plugin_manager/重载回调）用真实替身。
    """
    reloaded: list[str] = []
    scheduler_reloaded: list[str] = []

    async def _reload(name):
        reloaded.append(name)
        return True, None

    async def _reload_scheduler(conf_id):
        scheduler_reloaded.append(conf_id)

    acm = SimpleNamespace(
        confs={"default": core_conf},
        get_conf_list=lambda: [
            {"id": "default", "name": "default", "path": "cmd_config.json"}
        ],
    )
    provider_manager = SimpleNamespace(
        providers_config=providers,
        provider_sources_config=[],
        llm_tools=MagicMock(),
    )
    plugin_manager = SimpleNamespace(reload=_reload, llm_tools=[])

    lifecycle = MagicMock()
    lifecycle.astrbot_config_mgr = acm
    lifecycle.provider_manager = provider_manager
    lifecycle.plugin_manager = plugin_manager
    lifecycle.reload_pipeline_scheduler = _reload_scheduler
    return lifecycle, reloaded, scheduler_reloaded


def _sample_core_conf():
    return _FakeAstrBotConfig(
        {
            "provider_settings": {
                "enable": True,
                "default_provider_id": "openai-main",
                "fallback_chat_models": ["gemini-backup", "deepseek-2"],
                "request_max_retries": 3,
            },
            "provider_tts_settings": {"enable": True, "provider_id": "tts-edge"},
            "dashboard": {},
        }
    )


def _register_fake_plugin(conf: dict, schema: dict, name="fake_llm_plugin"):
    config = _FakeAstrBotConfig(conf)
    config.schema = schema
    config.config_path = f"data/config/{name}_config.json"
    md = StarMetadata(
        name=name,
        display_name="假 LLM 插件",
        config=config,
        activated=True,
    )
    star_registry.append(md)
    return md


def teardown_function(_):
    star_registry.clear()


def test_scan_识别主配置模型选择项():
    providers = [
        {"id": "openai-main", "model": "gpt-4o", "enable": True},
        {"id": "gemini-backup", "model": "gemini-pro", "enable": True},
        {"id": "tts-edge", "model": "edge-tts", "enable": True},
    ]
    core_conf = _sample_core_conf()
    lifecycle, _, _ = _make_core_lifecycle(core_conf, providers)
    service = ModelUsageService(lifecycle)

    data = service.scan()
    ids = {(u["scope"], u["key_path"], tuple(u["value"])) for u in data["usages"]}

    core_keys = {k for (scope, k, _) in ids if scope == "core"}
    assert "provider_settings.default_provider_id" in core_keys
    assert "provider_tts_settings.provider_id" in core_keys

    chain = next(
        u
        for u in data["usages"]
        if u["key_path"] == "provider_settings.default_provider_id"
    )
    assert chain["value"] == ["openai-main", "gemini-backup", "deepseek-2"]
    assert chain["value_type"] == "chain"
    # deepseek-2 不在提供商列表中，属于失效引用
    assert chain["matched_provider_ids"] == ["openai-main", "gemini-backup"]
    assert chain["invalid_provider_ids"] == ["deepseek-2"]
    # 回退列表条目已合并进链条目，不重复出现
    assert "provider_settings.fallback_chat_models" not in core_keys


def test_scan_插件schema标记与值命中兜底():
    providers = [{"id": "openai-main", "model": "gpt-4o", "enable": True}]
    core_conf = _sample_core_conf()
    lifecycle, _, _ = _make_core_lifecycle(core_conf, providers)

    schema = {
        "模型设置": {
            "type": "object",
            "items": {
                "对话提供商": {
                    "type": "string",
                    "description": "对话使用的提供商",
                    "_special": "select_provider",
                },
                "备注": {"type": "string"},
                "提供商列表": {"type": "list", "items": {"type": "string"}},
            },
        },
    }
    plugin_conf = {
        "模型设置": {
            "对话提供商": "openai-main",
            "备注": "openai-main 不可删",
            "提供商列表": ["openai-main", "不存在的模型"],
        },
    }
    _register_fake_plugin(plugin_conf, schema)
    service = ModelUsageService(lifecycle)

    data = service.scan()
    plugin_usages = [u for u in data["usages"] if u["scope"] == "plugin"]
    by_key = {u["key_path"]: u for u in plugin_usages}

    marked = by_key["模型设置.对话提供商"]
    assert marked["match_type"] == "schema"
    assert marked["plugin_display_name"] == "假 LLM 插件"

    # 「备注」是长文本不命中;列表值按值匹配兜底
    assert "模型设置.备注" not in by_key
    value_matched = by_key["模型设置.提供商列表"]
    assert value_matched["match_type"] == "value"
    assert value_matched["matched_provider_ids"] == ["openai-main"]
    assert value_matched["invalid_provider_ids"] == ["不存在的模型"]


def test_apply_直接写入单值():
    providers = [
        {"id": "openai-main", "model": "gpt-4o", "enable": True},
        {"id": "new-model", "model": "new", "enable": True},
    ]
    core_conf = _sample_core_conf()
    lifecycle, reloaded, scheduler_reloaded = _make_core_lifecycle(
        core_conf, providers
    )
    service = ModelUsageService(lifecycle)

    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_tts_settings.provider_id",
                    "value_type": "single",
                    "new_value": ["new-model"],
                },
            ]
        )
    )
    assert result["replaced"] == 1
    assert core_conf["provider_tts_settings"]["provider_id"] == "new-model"
    assert "default" in scheduler_reloaded

    # 空列表表示清空
    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_tts_settings.provider_id",
                    "value_type": "single",
                    "new_value": [],
                },
            ]
        )
    )
    assert result["replaced"] == 1
    assert core_conf["provider_tts_settings"]["provider_id"] == ""


def test_apply_直接写入对话模型链():
    providers = [
        {"id": "openai-main", "enable": True},
        {"id": "gemini-backup", "enable": True},
        {"id": "new-model", "enable": True},
    ]
    core_conf = _sample_core_conf()
    lifecycle, _, _ = _make_core_lifecycle(core_conf, providers)
    service = ModelUsageService(lifecycle)

    # 整条链重写：第一项主模型 + 其余回退
    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_settings.default_provider_id",
                    "special": "select_chat_model_chain",
                    "value_type": "chain",
                    "new_value": ["new-model", "gemini-backup", "deepseek-2"],
                },
            ]
        )
    )
    assert result["replaced"] == 1
    assert core_conf["provider_settings"]["default_provider_id"] == "new-model"
    assert core_conf["provider_settings"]["fallback_chat_models"] == [
        "gemini-backup",
        "deepseek-2",
    ]

    # 链里含重复主模型时，回退列表自动去重
    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_settings.default_provider_id",
                    "special": "select_chat_model_chain",
                    "value_type": "chain",
                    "new_value": ["new-model", "new-model", "deepseek-2"],
                },
            ]
        )
    )
    assert result["replaced"] == 1
    assert core_conf["provider_settings"]["fallback_chat_models"] == ["deepseek-2"]


def test_apply_批量整行替换为目标模型():
    """批量语义：勾选的行不论原值是什么，直接替换为目标模型。"""
    providers = [
        {"id": "openai-main", "enable": True},
        {"id": "gemini-backup", "enable": True},
        {"id": "target", "enable": True},
    ]
    core_conf = _sample_core_conf()
    lifecycle, _, _ = _make_core_lifecycle(core_conf, providers)
    service = ModelUsageService(lifecycle)

    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_settings.default_provider_id",
                    "special": "select_chat_model_chain",
                    "value_type": "chain",
                    "new_value": ["target"],
                },
            ]
        )
    )
    assert result["replaced"] == 1
    assert core_conf["provider_settings"]["default_provider_id"] == "target"
    assert core_conf["provider_settings"]["fallback_chat_models"] == []


def test_apply_插件保存后触发重载():
    providers = [{"id": "openai-main", "enable": True}, {"id": "new-model", "enable": True}]
    core_conf = _sample_core_conf()
    lifecycle, reloaded, _ = _make_core_lifecycle(core_conf, providers)
    service = ModelUsageService(lifecycle)

    schema = {"对话提供商": {"type": "string", "_special": "select_provider"}}
    plugin_conf = {"对话提供商": "openai-main"}
    md = _register_fake_plugin(plugin_conf, schema)

    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "plugin",
                    "plugin_name": "fake_llm_plugin",
                    "key_path": "对话提供商",
                    "new_value": ["new-model"],
                },
            ]
        )
    )
    assert result["replaced"] == 1
    assert md.config["对话提供商"] == "new-model"
    assert reloaded == ["fake_llm_plugin"]
    assert md.config.saved == 1


def test_apply_新值与旧值相同时不落盘():
    providers = [{"id": "openai-main", "enable": True}]
    core_conf = _sample_core_conf()
    lifecycle, reloaded, scheduler_reloaded = _make_core_lifecycle(
        core_conf, providers
    )
    service = ModelUsageService(lifecycle)

    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_settings.default_provider_id",
                    "special": "select_chat_model_chain",
                    "value_type": "chain",
                    "new_value": ["openai-main", "gemini-backup", "deepseek-2"],
                },
            ]
        )
    )
    assert result["unchanged"] == 1
    assert result["replaced"] == 0
    assert core_conf.saved == 0
    assert scheduler_reloaded == []


def test_apply_允许保留失效引用():
    """失效 ID 允许写回（如临时下线先保留引用），由扫描界面持续标红提示。"""
    providers = [{"id": "openai-main", "enable": True}]
    core_conf = _sample_core_conf()
    lifecycle, _, _ = _make_core_lifecycle(core_conf, providers)
    service = ModelUsageService(lifecycle)

    result = asyncio.run(
        service.apply(
            [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_tts_settings.provider_id",
                    "value_type": "single",
                    "new_value": ["ldm-api/doubao/seed-2-0-code"],
                },
            ]
        )
    )
    assert result["replaced"] == 1
    assert core_conf["provider_tts_settings"]["provider_id"] == "ldm-api/doubao/seed-2-0-code"


def test_scan_插件嵌套object二层配置():
    """二层（嵌套 object 内）的模型选择项也要被扫到，key_path 带层级前缀。"""
    providers = [{"id": "openai-main", "enable": True}]
    core_conf = _sample_core_conf()
    lifecycle, _, _ = _make_core_lifecycle(core_conf, providers)
    service = ModelUsageService(lifecycle)

    schema = {
        "模型配置": {
            "type": "object",
            "description": "模型配置",
            "items": {
                "视觉模型": {
                    "type": "string",
                    "description": "视觉模型",
                    "_special": "select_provider",
                },
            },
        },
    }
    plugin_conf = {"模型配置": {"视觉模型": "openai-main"}}
    _register_fake_plugin(plugin_conf, schema)

    data = service.scan()
    nested = [
        u
        for u in data["usages"]
        if u["scope"] == "plugin" and u["key_path"] == "模型配置.视觉模型"
    ]
    assert len(nested) == 1
    assert nested[0]["value"] == ["openai-main"]
    assert nested[0]["match_type"] == "schema"


def test_主配置metadata确实含模型选择标记():
    """守护测试:上游若改动 metadata 结构(如改名/挪走 _special),扫描要能被发现。"""
    specials = []
    for group in CONFIG_METADATA_3.values():
        for section in (group.get("metadata") or {}).values():
            for item in (section.get("items") or {}).values():
                special = item.get("_special") if isinstance(item, dict) else None
                if isinstance(special, str) and special.startswith("select_"):
                    specials.append(special.split(":")[0])
    assert "select_chat_model_chain" in specials
    assert "select_provider" in specials
    assert "select_provider_tts" in specials
