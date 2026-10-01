"""尺寸缺省交由方舟处理，不注入应用层预设。"""
import json

import httpx
import pytest

from astrbot.core.config.default import CONFIG_METADATA_2
from astrbot.core.provider.sources.volcengine_image_generation_source import ProviderVolcengineImageGeneration


def test_编码格式预设使用PNG但不自动写入请求():
    section = CONFIG_METADATA_2["provider_group"]["metadata"]["provider"]
    assert section["items"]["ark_image_options"]["template_schema"]["output_format"]["default"] == "png"
    assert "output_format" not in section["config_template"]["火山引擎生图"]["ark_image_options"]


def test_尺寸模板不预填固定值():
    section = CONFIG_METADATA_2["provider_group"]["metadata"]["provider"]
    assert "default" not in section["items"]["ark_image_options"]["template_schema"]["size"]
    assert "size" not in section["config_template"]["火山引擎生图"]["ark_image_options"]


@pytest.mark.asyncio
@pytest.mark.parametrize("options,size,expected", [
    ({}, None, None),
    ({"size": ""}, None, None),
    ({"size": "   "}, None, None),
    ({"size": None}, None, None),
    ({"size": "2K"}, "", None),
    ({"size": "1K"}, None, "1K"),
    ({"size": "2K"}, "1024x1024", "1024x1024"),
])
async def test_实际请求尺寸空值省略且显式值不被固定(options, size, expected):
    bodies = []
    provider = ProviderVolcengineImageGeneration({
        "type": "volcengine_image_generation", "model": "doubao-seedream-5-0-pro-260628",
        "api_base": "https://example.test/api/v3", "ark_image_options": options,
    }, {})
    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"code": "OfflineStop", "message": "离线协议断言"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider.session = client
        with pytest.raises(RuntimeError, match="OfflineStop"):
            await provider.generate_image("猫", size=size)
    assert len(bodies) == 1
    if expected is None:
        assert "size" not in bodies[0]
    else:
        assert bodies[0]["size"] == expected
