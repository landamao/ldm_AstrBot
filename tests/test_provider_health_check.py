"""模型测试必须执行真实能力调用，并保留可读的回复日志。"""

import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from PIL import Image

from astrbot.core.provider.sources import openai_image_generation_source as image_source


def _图片数据():
    缓冲 = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(缓冲, format="PNG")
    return 缓冲.getvalue()


@pytest.mark.asyncio
async def test_生图测试实际请求猫并保存图片和记录回复(tmp_path, monkeypatch):
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(tmp_path))
    日志 = Mock()
    monkeypatch.setattr(image_source, "logger", 日志)
    图片数据 = _图片数据()
    请求列表 = []

    def 响应(request):
        请求列表.append(request)
        return httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(图片数据).decode(),
            "revised_prompt": "一只可爱的猫",
        }]})

    提供商 = image_source.ProviderOpenAIImageGeneration({
        "id": "测试生图", "type": "openai_image_generation",
        "model": "cat-model", "api_base": "https://example.test/v1",
        "key": [], "timeout": 120,
    }, {})
    async with httpx.AsyncClient(transport=httpx.MockTransport(响应)) as 会话:
        提供商.session = 会话
        await 提供商.test()

    assert 请求列表[0].method == "POST"
    assert 请求列表[0].url.path == "/v1/images/generations"
    import json
    请求体 = json.loads(请求列表[0].content)
    assert 请求体["prompt"] == "请生成一只猫。"
    assert 请求体["model"] == "cat-model"
    assert 请求体["n"] == 1
    文件列表 = list(tmp_path.rglob("*.png"))
    assert len(文件列表) == 1
    assert 文件列表[0].read_bytes() == 图片数据
    日志文本 = str(日志.info.call_args_list)
    assert str(文件列表[0].resolve()) in 日志文本
    assert "一只可爱的猫" in 日志文本
    assert base64.b64encode(图片数据).decode() not in 日志文本
    assert "提供商: 「测试生图」" in 日志文本
    assert "模型ID: 「测试生图」" in 日志文本
    assert "模型: 「cat-model」" in 日志文本


@pytest.mark.asyncio
async def test_对话测试打印模型正文和思考回复(monkeypatch):
    from astrbot.core.provider import provider as provider_module
    from astrbot.core.provider.entities import LLMResponse

    日志 = Mock()
    monkeypatch.setattr(provider_module, "logger", 日志, raising=False)
    提供商 = SimpleNamespace(
        provider_config={"id": "ldmst/测试对话"},
        get_model=lambda: "chat-model",
        text_chat=AsyncMock(return_value=LLMResponse(
            role="assistant", completion_text="PONG", reasoning_content="这是连通性测试。",
        )),
    )
    await provider_module.Provider.test(提供商)
    日志文本 = str(日志.info.call_args_list)
    assert "PONG" in 日志文本
    assert "这是连通性测试。" in 日志文本
    assert "测试对话" in 日志文本
    assert "提供商: 「ldmst」" in 日志文本
    assert "模型ID: 「ldmst/测试对话」" in 日志文本
    assert "模型: 「chat-model」" in 日志文本


def test_生图元数据模型与请求配置一致():
    提供商 = image_source.ProviderOpenAIImageGeneration({
        "id": "ldmst/懒大猫生图", "type": "openai_image_generation",
        "model": "cat-model", "key": [],
    }, {})
    assert 提供商.meta().model == "cat-model"
    提供商.provider_config["model"] = "new-cat-model"
    assert 提供商.meta().model == "new-cat-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("失败", [False, True])
@pytest.mark.parametrize("模型", ["cat-model", ""])
async def test_测试结果日志区分提供商模型ID和模型(monkeypatch, 失败, 模型):
    from astrbot.dashboard.services import config_service

    日志 = Mock()
    monkeypatch.setattr(config_service, "logger", 日志)
    提供商 = image_source.ProviderOpenAIImageGeneration({
        "id": "ldmst/懒大猫生图", "type": "openai_image_generation",
        "model": 模型, "key": [],
    }, {})
    提供商.test = AsyncMock(side_effect=RuntimeError("请求失败") if 失败 else None)
    服务 = SimpleNamespace(provider_manager=SimpleNamespace(
        inst_map={"ldmst/懒大猫生图": 提供商},
    ))
    结果 = await config_service.ProviderConfigService.test_provider(服务, "ldmst/懒大猫生图")
    assert 结果["model"] == 模型
    assert 结果["id"] == "ldmst/懒大猫生图"
    assert 结果["status"] == ("unavailable" if 失败 else "available")
    日志文本 = str(日志.method_calls)
    assert "提供商: 「ldmst」" in 日志文本
    assert "模型ID: 「ldmst/懒大猫生图」" in 日志文本
    assert f"模型: 「{模型 or '未知'}」" in 日志文本
