"""/model、/provider 的规则/会话/全局三档作用域测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.builtin_stars.builtin_commands.commands import provider as provider_cmd
from astrbot.builtin_stars.builtin_commands.commands.provider import (
    ProviderCommands,
    _split_switch_args,
)
from astrbot.core.provider.entities import ProviderType
from astrbot.core.utils import session_selection
from astrbot.core.utils.session_selection import resolve_session_model


class FakeEvent:
    def __init__(self, umo: str = "test:FriendMessage:10001") -> None:
        self.unified_msg_origin = umo
        self.result = None

    def set_result(self, result) -> None:
        self.result = result


def _text(event: FakeEvent) -> str:
    return event.result.get_plain_text()


class FakeProvider:
    def __init__(
        self,
        provider_id: str = "p1",
        model: str = "global-model",
        models: list[str] | None = None,
        provider_source_id: str = "src",
    ) -> None:
        self.provider_id = provider_id
        self.model = model
        self.models = models or ["model-a", "model-b", "model-c"]
        self.set_model_calls: list[str] = []
        self.provider_config = {
            "id": provider_id,
            "model": model,
            "provider_source_id": provider_source_id,
        }

    def meta(self) -> SimpleNamespace:
        return SimpleNamespace(id=self.provider_id)

    def display_provider_id(self) -> str:
        return self.provider_id.split("/")[0]

    def get_model(self) -> str:
        return self.model

    def set_model(self, model: str) -> None:
        self.set_model_calls.append(model)
        self.model = model

    async def get_models(self) -> list[str]:
        return list(self.models)


class FakeProviderManager:
    """只实现指令用到的提供商管理能力。"""

    def __init__(self, providers: list[FakeProvider]) -> None:
        self.providers = providers
        self.inst_map = {provider.provider_id: provider for provider in providers}
        self.set_provider_change_callback = MagicMock()
        self.set_provider = AsyncMock()
        self.update_provider = AsyncMock()

    async def ensure_provider_entry(
        self, template_id: str, model: str
    ) -> tuple[str, bool]:
        template = self.inst_map[template_id]
        source_id = template.provider_config.get("provider_source_id", "")
        if not source_id:
            raise ValueError("当前配置项没有 provider_source_id")
        new_id = f"{source_id}/{model}"
        if new_id in self.inst_map:
            return new_id, False
        new_provider = FakeProvider(
            new_id,
            model=model,
            provider_source_id=source_id,
        )
        self.inst_map[new_id] = new_provider
        self.providers.append(new_provider)
        return new_id, True

    def get_provider_config_by_id(self, provider_id: str, merged: bool = False):
        provider = self.inst_map.get(provider_id)
        return dict(provider.provider_config) if provider else None


class FakeContext:
    def __init__(
        self,
        providers: list[FakeProvider],
        config: dict | None = None,
    ) -> None:
        self.providers = providers
        self.provider_manager = FakeProviderManager(providers)
        self.config = config or {"provider_settings": {}}

    def get_using_provider(self, umo: str | None = None) -> FakeProvider:
        return self.providers[0]

    def get_all_providers(self) -> list[FakeProvider]:
        return self.providers

    def get_config(self, umo: str | None = None) -> dict:
        return self.config


def _commands(providers: list[FakeProvider] | None = None) -> ProviderCommands:
    return ProviderCommands(FakeContext(providers or [FakeProvider()]))


def _patch_scope(monkeypatch, *, rule_exists: bool = False):
    rule_mock = AsyncMock(return_value=rule_exists)
    session_mock = AsyncMock()
    save_mock = AsyncMock()
    monkeypatch.setattr(provider_cmd, "has_session_rule", rule_mock)
    monkeypatch.setattr(provider_cmd, "set_session_selection", session_mock)
    monkeypatch.setattr(provider_cmd, "set_rule_selection", save_mock)
    monkeypatch.setattr(
        provider_cmd,
        "resolve_session_model",
        AsyncMock(return_value=(None, "none")),
    )
    monkeypatch.setattr(
        provider_cmd,
        "resolve_effective_model",
        AsyncMock(return_value=(None, "none")),
    )
    return rule_mock, session_mock, save_mock


# ==== 参数解析 ====


def test_拆分参数支持前后置开关():
    assert _split_switch_args(("3", "-save"), max_positional=1) == (
        ["3"],
        "save",
        "",
    )
    assert _split_switch_args(("-global", "3"), max_positional=1) == (
        ["3"],
        "global",
        "",
    )
    assert _split_switch_args((None, "--save"), max_positional=1) == (
        [],
        "save",
        "",
    )


def test_拆分参数拒绝冲突与未知开关():
    assert "不能同时使用" in _split_switch_args(
        ("3", "-save", "-global"), max_positional=1
    )[2]
    assert "未知参数" in _split_switch_args(("3", "-x"), max_positional=1)[2]


# ==== /model 三档 ====


@pytest.mark.asyncio
async def test_model无规则只写会话级且不改共享实例(monkeypatch):
    prov = FakeProvider()
    cmds = _commands([prov])
    _, session_mock, rule_mock = _patch_scope(monkeypatch, rule_exists=False)

    event = FakeEvent()
    await cmds.model_ls(event, "2")

    session_mock.assert_awaited_once_with(
        event.unified_msg_origin, "src/model-b", "model-b"
    )
    rule_mock.assert_not_awaited()
    assert prov.set_model_calls == []
    assert "仅本会话生效" in _text(event)


@pytest.mark.asyncio
async def test_model有自定义规则时写入规则并提示(monkeypatch):
    prov = FakeProvider()
    cmds = _commands([prov])
    _, session_mock, rule_mock = _patch_scope(monkeypatch, rule_exists=True)

    event = FakeEvent()
    await cmds.model_ls(event, "2")

    rule_mock.assert_awaited_once_with(
        event.unified_msg_origin, "src/model-b", "model-b"
    )
    session_mock.assert_not_awaited()
    assert prov.set_model_calls == []
    assert "自定义规则" in _text(event)


@pytest.mark.asyncio
async def test_model_save参数强制创建规则(monkeypatch):
    prov = FakeProvider()
    cmds = _commands([prov])
    _, session_mock, rule_mock = _patch_scope(monkeypatch, rule_exists=False)

    event = FakeEvent()
    await cmds.model_ls(event, "2", "-save")

    rule_mock.assert_awaited_once_with(
        event.unified_msg_origin, "src/model-b", "model-b"
    )
    session_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_global参数只设默认项不改model(monkeypatch):
    prov = FakeProvider()
    cmds = _commands([prov])
    _, session_mock, rule_mock = _patch_scope(monkeypatch, rule_exists=True)
    global_mock = AsyncMock(return_value=(False, "model-b"))
    monkeypatch.setattr(cmds, "_save_global_model", global_mock)

    event = FakeEvent()
    await cmds.model_ls(event, "2", "-global")

    global_mock.assert_awaited_once_with(
        "src/model-b", "model-b", event.unified_msg_origin
    )
    rule_mock.assert_not_awaited()
    session_mock.assert_not_awaited()
    assert "全局默认对话模型" in _text(event)


@pytest.mark.asyncio
async def test_model_global本来就是该配置项时提示没有变化(monkeypatch):
    prov = FakeProvider()
    cmds = _commands([prov])
    _patch_scope(monkeypatch, rule_exists=False)
    monkeypatch.setattr(
        cmds, "_save_global_model", AsyncMock(return_value=(True, "model-b"))
    )

    event = FakeEvent()
    await cmds.model_ls(event, "2", "-global")

    assert "没有变化" in _text(event)


@pytest.mark.asyncio
async def test_model列表显示生效模型与来源(monkeypatch):
    # 规则选中 p1 这个配置项；实际模型以配置项的 model 为准
    prov = FakeProvider(model="rule-model")
    cmds = _commands([prov])
    _patch_scope(monkeypatch, rule_exists=True)
    monkeypatch.setattr(
        provider_cmd,
        "resolve_effective_model",
        AsyncMock(return_value=("rule-model", "rule")),
    )

    event = FakeEvent()
    await cmds.model_ls(event)

    text = _text(event)
    assert "rule-model" in text
    assert "自定义规则" in text


@pytest.mark.asyncio
async def test_model列表规则只选提供商时来源仍是自定义规则(monkeypatch):
    prov = FakeProvider("p1", model="entry-model")
    cmds = _commands([prov])
    _patch_scope(monkeypatch, rule_exists=True)
    fake_sp = FakeSP({"provider_perf_chat_completion": "p1"})
    monkeypatch.setattr(session_selection, "sp", fake_sp)
    # 用真实解析函数，验证“规则只选了提供商”时来源不被误判
    monkeypatch.setattr(
        provider_cmd,
        "resolve_effective_model",
        session_selection.resolve_effective_model,
    )

    event = FakeEvent()
    await cmds.model_ls(event)

    text = _text(event)
    assert "entry-model" in text
    assert "自定义规则" in text
    assert "全局配置" not in text


@pytest.mark.asyncio
async def test_model输入配置项ID直接切到该配置项(monkeypatch):
    p1 = FakeProvider("p1", model="global-model", models=["a", "b"])
    p2 = FakeProvider(
        "p2", model="other-model", models=["x", "y"], provider_source_id="src2"
    )
    cmds = _commands([p1, p2])
    _, session_mock, rule_mock = _patch_scope(monkeypatch, rule_exists=False)

    event = FakeEvent()
    await cmds.model_ls(event, "p2")

    session_mock.assert_awaited_once_with(
        event.unified_msg_origin, "p2", "other-model"
    )
    rule_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_model按配置项model精确匹配优先于当前列表(monkeypatch):
    # p1 的模型列表也包含 other-model，但仍应切到 model=other-model 的 p2 配置项
    p1 = FakeProvider("p1", model="global-model", models=["a", "other-model"])
    p2 = FakeProvider(
        "p2", model="other-model", models=["x", "y"], provider_source_id="src2"
    )
    cmds = _commands([p1, p2])
    _, session_mock, rule_mock = _patch_scope(monkeypatch, rule_exists=False)

    event = FakeEvent()
    await cmds.model_ls(event, "other-model")

    session_mock.assert_awaited_once_with(
        event.unified_msg_origin, "p2", "other-model"
    )
    rule_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_model选择上游模型时自动创建对应配置项(monkeypatch):
    p1 = FakeProvider("p1", model="global-model", models=["a", "b"])
    cmds = _commands([p1])
    _, session_mock, _ = _patch_scope(monkeypatch, rule_exists=False)

    event = FakeEvent()
    await cmds.model_ls(event, "b")

    session_mock.assert_awaited_once_with(event.unified_msg_origin, "src/b", "b")
    assert cmds.context.provider_manager.inst_map["src/b"].get_model() == "b"
    assert p1.set_model_calls == []


@pytest.mark.asyncio
async def test_model列表显示完整配置项ID(monkeypatch):
    prov = FakeProvider(
        "ldmapi/glm-5.2",
        model="glm-5.2",
        provider_source_id="ldmapi",
    )
    cmds = _commands([prov])
    _patch_scope(monkeypatch, rule_exists=False)

    event = FakeEvent()
    await cmds.model_ls(event)

    text = _text(event)
    assert "ldmapi/glm-5.2" in text
    assert "来源 ldmapi" in text


# ==== /provider 三档 ====


@pytest.mark.asyncio
async def test_provider无规则只写会话级(monkeypatch):
    p1, p2 = FakeProvider("p1"), FakeProvider("p2")
    cmds = _commands([p1, p2])
    _patch_scope(monkeypatch, rule_exists=False)
    session_provider_mock = AsyncMock()
    rule_provider_mock = AsyncMock()
    monkeypatch.setattr(provider_cmd, "set_session_provider", session_provider_mock)
    monkeypatch.setattr(provider_cmd, "set_rule_provider", rule_provider_mock)

    event = FakeEvent()
    await cmds.provider(event, "2")

    session_provider_mock.assert_awaited_once_with(
        event.unified_msg_origin, ProviderType.CHAT_COMPLETION, "p2"
    )
    rule_provider_mock.assert_not_awaited()
    assert "仅本会话生效" in _text(event)


@pytest.mark.asyncio
async def test_provider支持直接用配置项ID(monkeypatch):
    p1 = FakeProvider("p1")
    p2 = FakeProvider(
        "ldmapi/glm-5.3", model="glm-5.3", provider_source_id="ldmapi"
    )
    cmds = _commands([p1, p2])
    _patch_scope(monkeypatch, rule_exists=False)
    session_provider_mock = AsyncMock()
    monkeypatch.setattr(provider_cmd, "set_session_provider", session_provider_mock)

    event = FakeEvent()
    await cmds.provider(event, "ldmapi/glm-5.3")

    session_provider_mock.assert_awaited_once_with(
        event.unified_msg_origin,
        ProviderType.CHAT_COMPLETION,
        "ldmapi/glm-5.3",
    )


@pytest.mark.asyncio
async def test_provider有规则时写入规则(monkeypatch):
    p1, p2 = FakeProvider("p1"), FakeProvider("p2")
    cmds = _commands([p1, p2])
    _patch_scope(monkeypatch, rule_exists=True)
    session_provider_mock = AsyncMock()
    rule_provider_mock = AsyncMock()
    monkeypatch.setattr(provider_cmd, "set_session_provider", session_provider_mock)
    monkeypatch.setattr(provider_cmd, "set_rule_provider", rule_provider_mock)

    event = FakeEvent()
    await cmds.provider(event, "2")

    rule_provider_mock.assert_awaited_once_with(
        event.unified_msg_origin, ProviderType.CHAT_COMPLETION, "p2"
    )
    session_provider_mock.assert_not_awaited()
    assert "自定义规则" in _text(event)


@pytest.mark.asyncio
async def test_provider_global参数走全局保存(monkeypatch):
    p1, p2 = FakeProvider("p1"), FakeProvider("p2")
    cmds = _commands([p1, p2])
    _patch_scope(monkeypatch, rule_exists=True)
    global_mock = AsyncMock()
    monkeypatch.setattr(cmds, "_save_global_provider", global_mock)

    event = FakeEvent()
    await cmds.provider(event, "2", None, "-global")

    global_mock.assert_awaited_once_with(
        ProviderType.CHAT_COMPLETION, "p2", event.unified_msg_origin
    )
    assert "全局默认" in _text(event)


# ==== 会话模型解析优先级 ====


class FakeSP:
    def __init__(self, prefs: dict) -> None:
        self.prefs = prefs

    async def range_get_async(self, scope: str, scope_id: str | None = None, key=None):
        return [
            SimpleNamespace(key=key, value={"val": value})
            for key, value in self.prefs.items()
        ]

    async def get_async(self, scope: str, scope_id: str, key: str, default=None):
        return self.prefs.get(key, default)


@pytest.mark.asyncio
async def test_规则模型优先且按提供商绑定(monkeypatch):
    fake_sp = FakeSP(
        {
            "session_service_config": {
                "model": "rule-model",
                "model_provider_id": "p1",
            },
            "session_model_override": {"provider_id": "p1", "model": "session-model"},
        }
    )
    monkeypatch.setattr(session_selection, "sp", fake_sp)

    assert await resolve_session_model("umo", "p1") == ("rule-model", "rule")
    # 规则模型绑定 p1，换到 p2 后不适用，落到会话覆盖（同样绑定 p1，也不适用）
    assert await resolve_session_model("umo", "p2") == (None, "none")


@pytest.mark.asyncio
async def test_会话模型覆盖在无规则时生效(monkeypatch):
    fake_sp = FakeSP(
        {"session_model_override": {"provider_id": "p1", "model": "session-model"}}
    )
    monkeypatch.setattr(session_selection, "sp", fake_sp)

    assert await resolve_session_model("umo", "p1") == ("session-model", "session")
    assert await resolve_session_model("umo", "p2") == (None, "none")


@pytest.mark.asyncio
async def test_规则只选提供商时生效模型来源是自定义规则(monkeypatch):
    fake_sp = FakeSP({"provider_perf_chat_completion": "p1"})
    monkeypatch.setattr(session_selection, "sp", fake_sp)

    # 规则没有单独存模型，模型来自配置项本身，来源仍应算规则
    assert await session_selection.resolve_effective_model(
        "umo", "p1", "entry-model"
    ) == ("entry-model", "rule")
    assert await session_selection.resolve_effective_model(
        "umo", "p2", "other-model"
    ) == ("other-model", "none")


@pytest.mark.asyncio
async def test_会话只选提供商时生效模型来源是当前会话(monkeypatch):
    fake_sp = FakeSP({"session_provider_override_chat_completion": "p1"})
    monkeypatch.setattr(session_selection, "sp", fake_sp)

    assert await session_selection.resolve_effective_model(
        "umo", "p1", "entry-model"
    ) == ("entry-model", "session")


@pytest.mark.asyncio
async def test_规则模型来源且模型以配置项为准(monkeypatch):
    fake_sp = FakeSP(
        {
            "session_service_config": {
                "model": "stale-model",
                "model_provider_id": "p1",
            }
        }
    )
    monkeypatch.setattr(session_selection, "sp", fake_sp)

    # 规则里的模型名漂移时，展示以配置项当前 model 为准
    assert await session_selection.resolve_effective_model(
        "umo", "p1", "entry-model"
    ) == ("entry-model", "rule")


@pytest.mark.asyncio
async def test_has_session_rule只认规则键(monkeypatch):
    fake_sp = FakeSP(
        {"session_model_override": {"provider_id": "p1", "model": "x"}}
    )
    monkeypatch.setattr(session_selection, "sp", fake_sp)
    assert await session_selection.has_session_rule("umo") is False

    fake_sp.prefs["provider_perf_chat_completion"] = "p1"
    assert await session_selection.has_session_rule("umo") is True


# ==== get_using_provider 会话级提供商 ====


def _bare_provider_manager(providers: dict, default_provider_id: str):
    from astrbot.core.provider.manager import ProviderManager

    pm = object.__new__(ProviderManager)
    pm.inst_map = providers
    pm.provider_insts = list(providers.values())
    pm.provider_settings = {}
    pm.acm = SimpleNamespace(
        get_conf=lambda umo: {
            "provider_settings": {"default_provider_id": default_provider_id}
        }
    )
    return pm


def test_get_using_provider会话覆盖优先于全局(monkeypatch):
    p1, p2 = FakeProvider("p1"), FakeProvider("p2")
    pm = _bare_provider_manager({"p1": p1, "p2": p2}, "p1")

    with (
        patch("astrbot.core.provider.manager.sp.get", return_value=None),
        patch(
            "astrbot.core.provider.manager.get_session_provider_override",
            return_value="p2",
        ),
    ):
        assert pm.get_using_provider(ProviderType.CHAT_COMPLETION, "umo") is p2


def test_get_using_provider规则提供商优先于会话覆盖(monkeypatch):
    p1, p2 = FakeProvider("p1"), FakeProvider("p2")
    pm = _bare_provider_manager({"p1": p1, "p2": p2}, "p2")

    with (
        patch("astrbot.core.provider.manager.sp.get", return_value="p1"),
        patch(
            "astrbot.core.provider.manager.get_session_provider_override",
            return_value="p2",
        ),
    ):
        assert pm.get_using_provider(ProviderType.CHAT_COMPLETION, "umo") is p1


# ==== 配置项 ID 与 model 同步 ====


class FakeConfig(dict):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self) -> None:
        self.saved += 1

    async def save_config_async(self) -> bool:
        self.saved += 1
        return True


class FakePref:
    def __init__(self, scope_id: str, key: str, value) -> None:
        self.scope_id = scope_id
        self.key = key
        self.value = {"val": value}


class FakePrefSP:
    def __init__(self, prefs: list[FakePref]) -> None:
        self.prefs = prefs
        self.puts: list[tuple] = []

    async def range_get_async(self, scope, scope_id=None, key=None):
        return list(self.prefs)

    async def get_async(self, scope, scope_id, key, default=None):
        return default

    async def put_async(self, scope, scope_id, key, value):
        self.puts.append((scope, scope_id, key, value))


def _sync_manager(config: FakeConfig, sp_obj: FakePrefSP):
    from astrbot.core.provider.manager import ProviderManager

    pm = object.__new__(ProviderManager)
    pm.acm = SimpleNamespace(default_conf=config, confs={"default": config}, sp=sp_obj)
    pm.providers_config = config["provider"]
    pm.provider_sources_config = []
    return pm


@pytest.mark.asyncio
async def test_启动时按model同步失配的配置项ID并更新引用():
    config = FakeConfig(
        {
            "provider": [
                {
                    "id": "懒大猫模型/test",
                    "provider_source_id": "懒大猫模型",
                    "model": "喜欢懒大猫",
                    "enable": True,
                },
                {
                    "id": "懒大猫模型/testpro",
                    "provider_source_id": "懒大猫模型",
                    "model": "testpro",
                    "enable": True,
                },
            ],
            "provider_settings": {
                "default_provider_id": "懒大猫模型/test",
                "fallback_chat_models": ["懒大猫模型/test"],
            },
            "provider_tts_settings": {"provider_id": "懒大猫模型/test"},
        }
    )
    sp_obj = FakePrefSP(
        [
            FakePref("umo1", "provider_perf_chat_completion", "懒大猫模型/test"),
            FakePref(
                "umo1",
                "session_service_config",
                {
                    "model": "喜欢懒大猫",
                    "model_provider_id": "懒大猫模型/test",
                },
            ),
            FakePref(
                "umo2",
                "session_model_override",
                {"provider_id": "懒大猫模型/test", "model": "喜欢懒大猫"},
            ),
        ]
    )
    pm = _sync_manager(config, sp_obj)

    await pm._sync_provider_ids()

    assert config["provider"][0]["id"] == "懒大猫模型/喜欢懒大猫"
    assert config["provider"][1]["id"] == "懒大猫模型/testpro"
    assert (
        config["provider_settings"]["default_provider_id"]
        == "懒大猫模型/喜欢懒大猫"
    )
    assert config["provider_settings"]["fallback_chat_models"] == [
        "懒大猫模型/喜欢懒大猫"
    ]
    assert config["provider_tts_settings"]["provider_id"] == "懒大猫模型/喜欢懒大猫"
    assert config.saved == 1

    updated = {put[2]: put[3] for put in sp_obj.puts}
    assert updated["provider_perf_chat_completion"] == "懒大猫模型/喜欢懒大猫"
    assert updated["session_service_config"]["model_provider_id"] == (
        "懒大猫模型/喜欢懒大猫"
    )
    assert updated["session_model_override"]["provider_id"] == (
        "懒大猫模型/喜欢懒大猫"
    )


@pytest.mark.asyncio
async def test_手填ID的配置项不自动改名():
    config = FakeConfig(
        {
            "provider": [
                {
                    "id": "my-gateway",
                    "provider_source_id": "src",
                    "model": "gpt-4o",
                    "enable": True,
                }
            ],
            "provider_settings": {"default_provider_id": "my-gateway"},
        }
    )
    sp_obj = FakePrefSP([])
    pm = _sync_manager(config, sp_obj)

    await pm._sync_provider_ids()

    assert config["provider"][0]["id"] == "my-gateway"
    assert config.saved == 0


@pytest.mark.asyncio
async def test_目标ID已存在时跳过改名():
    config = FakeConfig(
        {
            "provider": [
                {
                    "id": "src/old",
                    "provider_source_id": "src",
                    "model": "new",
                    "enable": True,
                },
                {
                    "id": "src/new",
                    "provider_source_id": "src",
                    "model": "new",
                    "enable": True,
                },
            ],
            "provider_settings": {"default_provider_id": "src/old"},
        }
    )
    sp_obj = FakePrefSP([])
    pm = _sync_manager(config, sp_obj)

    await pm._sync_provider_ids()

    assert config["provider"][0]["id"] == "src/old"
    assert config.saved == 0


@pytest.mark.asyncio
async def test_update_provider按model同步ID并更新引用():
    config = FakeConfig(
        {
            "provider": [
                {
                    "id": "src/old",
                    "provider_source_id": "src",
                    "model": "old",
                    "enable": True,
                }
            ],
            "provider_settings": {"default_provider_id": "src/old"},
        }
    )
    sp_obj = FakePrefSP([])
    pm = _sync_manager(config, sp_obj)
    pm.resource_lock = asyncio.Lock()
    pm.reload = AsyncMock()

    new_config = {
        "id": "src/old",
        "provider_source_id": "src",
        "model": "new",
        "enable": True,
    }
    effective_id = await pm.update_provider("src/old", new_config)

    assert effective_id == "src/new"
    assert config["provider"][0]["id"] == "src/new"
    assert config["provider_settings"]["default_provider_id"] == "src/new"
    pm.reload.assert_awaited_once_with(new_config)


@pytest.mark.asyncio
async def test_ensure_provider_entry按来源和模型生成ID():
    config = FakeConfig(
        {
            "provider": [
                {
                    "id": "src/old",
                    "provider_source_id": "src",
                    "model": "old",
                    "enable": True,
                }
            ]
        }
    )
    sp_obj = FakePrefSP([])
    pm = _sync_manager(config, sp_obj)
    pm.create_provider = AsyncMock()

    new_id, created = await pm.ensure_provider_entry("src/old", "new")

    assert new_id == "src/new"
    assert created is True
    pm.create_provider.assert_awaited_once()
    created_config = pm.create_provider.await_args.args[0]
    assert created_config["id"] == "src/new"
    assert created_config["model"] == "new"
    assert created_config["max_context_tokens"] == 0


@pytest.mark.asyncio
async def test_ensure_provider_entry无来源时拒绝():
    config = FakeConfig(
        {
            "provider": [
                {
                    "id": "manual",
                    "model": "old",
                    "enable": True,
                }
            ]
        }
    )
    sp_obj = FakePrefSP([])
    pm = _sync_manager(config, sp_obj)

    with pytest.raises(ValueError, match="provider_source_id"):
        await pm.ensure_provider_entry("manual", "new")


# ==== WebUI 规则保存不抹掉模型字段 ====


@pytest.mark.asyncio
async def test_规则编辑保存保留模型字段(monkeypatch):
    from astrbot.dashboard.services import session_management_service as sms

    stored: dict = {}

    async def fake_get_async(scope, scope_id, key, default=None):
        return {
            "model": "rule-model",
            "model_provider_id": "p1",
            "persona_id": "old",
        }

    async def fake_put(umo, key, value):
        stored["value"] = value

    fake_sp = SimpleNamespace(
        get_async=fake_get_async,
        session_put=fake_put,
    )
    monkeypatch.setattr(sms, "sp", fake_sp)

    service = object.__new__(sms.SessionManagementService)
    await service.update_session_rule(
        {
            "umo": "umo",
            "rule_key": "session_service_config",
            "rule_value": {"custom_name": "新备注", "persona_id": "old"},
        }
    )

    assert stored["value"]["model"] == "rule-model"
    assert stored["value"]["model_provider_id"] == "p1"
    assert stored["value"]["custom_name"] == "新备注"
