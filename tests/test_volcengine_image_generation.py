"""火山方舟生图协议的离线回归测试，不调用收费接口。"""

import base64
import importlib
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

PRO = "doubao-seedream-5-0-pro-260628"
LITE = "doubao-seedream-5-0-260128"


def 图片数据(format="JPEG"):
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "green").save(buffer, format=format)
    return buffer.getvalue()


def 提供商(model=PRO, **config):
    module = importlib.import_module(
        "astrbot.core.provider.sources.volcengine_image_generation_source"
    )
    return module.ProviderVolcengineImageGeneration({
        "id": "火山/测试", "type": "volcengine_image_generation",
        "model": model, "key": ["offline-test-key"],
        "api_base": "https://example.test/api/v3", **config,
    }, {})


@pytest.mark.asyncio
async def test_pro单图使用方舟JSON协议():
    requests = []
    picture = 图片数据()

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{
            "b64_json": base64.b64encode(picture).decode(),
            "output_format": "jpeg",
        }]})

    provider = 提供商()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider.session = client
        result = await provider.generate_image("请生成一只猫。")
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v3/images/generations"
    assert requests[0].headers["authorization"] == "Bearer offline-test-key"
    assert json.loads(requests[0].content) == {
        "model": PRO, "prompt": "请生成一只猫。",
    }
    assert result[0].mime_type == "image/jpeg"
    assert base64.b64decode(result[0].base64_data) == picture
    assert provider.meta().model == PRO


@pytest.mark.asyncio
@pytest.mark.parametrize("model,count,refs", [(LITE, 3, 2), (PRO, 1, 1)])
async def test_参考图直接走JSON且组图不发送n(model, count, refs):
    requests = []
    picture = 图片数据()
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(picture).decode()}]})
    provider = 提供商(model, ark_image_options={"watermark": False, "response_format": "b64_json"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider.session = client
        await provider.generate_image("参考图生成猫", n=count, size="2K", image=[(picture, "IMAGE/JPEG")] * refs)
    body = json.loads(requests[0].content)
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/images/generations")
    assert body["watermark"] is False
    assert body["size"] == "2K"
    assert "n" not in body
    urls = body["image"] if refs > 1 else [body["image"]]
    assert len(urls) == refs
    assert urls[0].startswith("data:image/jpeg;base64,")
    if count > 1:
        assert body["sequential_image_generation"] == "auto"
        assert body["sequential_image_generation_options"] == {"max_images": count}
    else:
        assert "sequential_image_generation" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("model,n,options,refs,match", [
    (PRO, 2, {}, 0, "组图"),
    (PRO, 1, {"stream": True}, 0, "流式"),
    (PRO, 1, {"sequential_image_generation": "disabled"}, 0, "组图"),
    (PRO, 1, {"tools": [{"type": "web_search"}]}, 0, "搜索"),
    (LITE, 1, {"n": 2}, 0, "参数"),
    (LITE, 1, {"layer_decomposition": True}, 0, "图层"),
    (PRO, 1, {"background": "transparent"}, 0, "透明"),
    (PRO, 1, {}, 11, "参考图"),
    (LITE, 3, {}, 14, "15"),
    (PRO, 1, {"size": "4K"}, 0, "尺寸"),
])
async def test_非法请求本地拒绝不消耗额度(model, n, options, refs, match):
    provider = 提供商(model, ark_image_options=options)
    with pytest.raises(ValueError, match=match):
        await provider.generate_image("猫", n=n, image=[(图片数据(), "image/jpeg")] * refs)


@pytest.mark.asyncio
@pytest.mark.parametrize("model,options,size", [
    (LITE, {"output_format": "png", "stream": False, "size": "2K"}, None),
    (PRO, {"background": "opaque", "size": "2K"}, "1024x1024"),
    ("doubao-seedream-4-0-250828", {}, "1K"),
    ("doubao-seedream-4-5-251128", {}, "2048x2048"),
])
async def test_模型合法选项与精确尺寸(model, options, size):
    requests = []
    picture = 图片数据("PNG")
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(picture).decode()}]})
    provider = 提供商(model, ark_image_options=options)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider.session = client
        await provider.generate_image("猫", size=size)
    assert requests[0]["size"] == (size or options["size"])
    assert "n" not in requests[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"n": 1}, {"seed": 123}, {"model": LITE}, {"prompt": "替换提示词"},
    {"sequential_image_generation_options": {"max_images": 2}},
    {"output_format": "gif"}, {"response_format": "png"},
    {"watermark": "false"}, {"layer_decomposition": True},
])
async def test_pro拒绝不支持的额外参数(options):
    provider = 提供商(ark_image_options=options)
    with pytest.raises(ValueError):
        await provider.generate_image("猫")


@pytest.mark.asyncio
async def test_Endpoint需明确能力族():
    provider = 提供商("ep-test")
    with pytest.raises(ValueError, match="能力"):
        await provider.generate_image("猫")


@pytest.mark.asyncio
async def test_组图配置上限仍受参考图总量限制():
    provider = 提供商(LITE, ark_image_options={
        "sequential_image_generation": "auto",
        "sequential_image_generation_options": {"max_images": 15},
    })
    with pytest.raises(ValueError, match="15"):
        await provider.generate_image("猫", image=[(图片数据(), "image/jpeg")])


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,match", [
    (200, {"error": {"code": "NoQuota", "message": "余额不足"}}, "NoQuota"),
    (403, {"error": {"code": "AccessDenied", "message": "无调用权限"}}, "AccessDenied"),
    (200, {"data": [{"error": {"code": "Blocked", "message": "审核失败"}}]}, "Blocked"),
    (200, [], "响应结构"),
])
async def test_完整错误信息与不自动重试(status, body, match):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body)
    provider = 提供商()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider.session = client
        with pytest.raises(RuntimeError, match=match):
            await provider.generate_image("猫")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_部分失败仍下载保留成功图片并记录失败(monkeypatch):
    from unittest.mock import Mock
    module = importlib.import_module("astrbot.core.provider.sources.volcengine_image_generation_source")
    log = Mock()
    monkeypatch.setattr(module, "logger", log)
    picture = 图片数据()
    requests = []
    def respond(request):
        requests.append(request)
        if request.method == "GET":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=picture, headers={"content-type": "application/octet-stream"})
        return httpx.Response(200, json={"data": [
            {"error": {"code": "Blocked", "message": "审核失败"}},
            {"url": "https://images.example.test/cat"},
            {"b64_json": "invalid-image"},
        ]})
    provider = 提供商(LITE)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider.session = client
        result = await provider.generate_image("猫", n=3)
    assert len(result) == 1
    assert result[0].mime_type == "image/jpeg"
    assert len(requests) == 2
    assert "Blocked" in str(log.warning.call_args_list)
    assert "invalid-image" not in str(log.method_calls)


def test_配置入口及生图工具可选择火山():
    from astrbot.core.config.default import CONFIG_METADATA_2
    from astrbot.core.provider.manager import ProviderManager
    from astrbot.core.tools.image_generation_tools import _select_image_generation_provider
    from astrbot.core.provider.register import provider_cls_map
    ProviderManager.dynamic_import_provider(None, "volcengine_image_generation")
    schema = CONFIG_METADATA_2["provider_group"]["metadata"]["provider"]
    template = schema["config_template"]["火山引擎生图"]
    assert template["type"] == "volcengine_image_generation"
    assert template["provider_type"] == "image_generation"
    assert template["key"] == []
    assert "ark_image_options" in schema["items"]
    provider = provider_cls_map[template["type"]].cls_type({**template, "model": PRO}, {})
    context = SimpleNamespace(get_all_image_generation_providers=lambda: [provider], get_using_image_generation_provider=lambda: provider)
    assert _select_image_generation_provider(context, "") == (provider, "")


@pytest.mark.asyncio
async def test_健康检查请求猫且保存有效图片(tmp_path, monkeypatch):
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(tmp_path))
    requests = []
    picture = 图片数据("PNG")
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(picture).decode()}]})
    provider = 提供商()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider.session = client
        await provider.test()
    assert requests == [{"model": PRO, "prompt": "请生成一只猫。"}]
    assert next(tmp_path.rglob("provider_test_*.png")).read_bytes() == picture


def test_配置参数实时读取且不修改原配置():
    options = {"watermark": False, "size": "1K"}
    provider = 提供商(ark_image_options=options)
    body = provider._构造请求("猫", PRO, 1, "2K", None)
    assert body["size"] == "2K"
    assert options == {"watermark": False, "size": "1K"}
    options["watermark"] = True
    assert provider._构造请求("猫", PRO, 1, None, None)["watermark"] is True


def test_Endpoint显式能力保持请求模型ID():
    provider = 提供商("ep-test", ark_model_family="pro")
    assert provider._构造请求("猫", "ep-test", 1, None, None)["model"] == "ep-test"
    with pytest.raises(ValueError, match="组图"):
        provider._构造请求("猫", "ep-test", 2, None, None)


def test_透明输入与lite联网搜索合法请求():
    buffer = io.BytesIO()
    Image.new("RGBA", (32, 32), (0, 0, 0, 0)).save(buffer, format="PNG")
    provider = 提供商(ark_image_options={"background": "transparent", "output_format": "png"})
    assert provider._构造请求("猫", PRO, 1, None, [(buffer.getvalue(), "image/png")])["background"] == "transparent"
    provider = 提供商(LITE, ark_image_options={"tools": [{"type": "web_search"}], "output_format": "png"})
    assert provider._构造请求("猫", LITE, 1, None, None)["tools"] == [{"type": "web_search"}]


@pytest.mark.parametrize("model,options,size,n", [
    (LITE, {}, "1024x1024", 1),
    (PRO, {}, "2048x2048", 0),
    (PRO, {}, "2048x2048", 16),
    (PRO, {"optimize_prompt_options": {"mode": "bad"}}, None, 1),
    (LITE, {"optimize_prompt_options": {"mode": "fast"}}, None, 1),
    ("doubao-seedream-4-5-251128", {"output_format": "png"}, None, 1),
    (LITE, {"sequential_image_generation": "disabled"}, None, 2),
    (LITE, {"sequential_image_generation": "auto", "sequential_image_generation_options": {"max_images": 16}}, None, 1),
])
def test_更多本地能力校验(model, options, size, n):
    with pytest.raises(ValueError):
        提供商(model, ark_image_options=options)._构造请求("猫", model, n, size, None)
