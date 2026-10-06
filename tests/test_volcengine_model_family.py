"""火山能力族按模型实例配置，不能继承提供商源的旧值。"""
from types import SimpleNamespace

import pytest

from astrbot.core.config.default import CONFIG_METADATA_2
from astrbot.core.provider.sources.volcengine_image_generation_source import ProviderVolcengineImageGeneration
from astrbot.core.provider.manager import ProviderManager

PRO = "doubao-seedream-5-0-pro-260628"
LITE = "doubao-seedream-5-0-260128"


def test_提供商模板不含模型级能力族():
    template = CONFIG_METADATA_2["provider_group"]["metadata"]["provider"]["config_template"]["火山引擎生图"]
    assert "ark_model_family" not in template


@pytest.mark.parametrize("choice,expected", [(None, "pro"), ("auto", "pro"), ("lite", "lite")])
def test_合并只接受模型自己的能力族(choice, expected):
    source = {"id": "火山", "type": "volcengine_image_generation", "ark_model_family": "4.5", "ark_image_options": {"watermark": False}}
    manager = SimpleNamespace(provider_sources_config=[source])
    model = {"id": "火山/模型", "provider_source_id": "火山", "model": PRO}
    if choice is not None:
        model["ark_model_family"] = choice
    merged = ProviderManager.get_merged_provider_config(manager, model)
    provider = ProviderVolcengineImageGeneration(merged, {})
    assert provider._模型族(PRO) == expected
    assert merged.get("ark_model_family", "auto") == (choice or "auto")
    assert merged["ark_image_options"] == {"watermark": False}
    assert source["ark_model_family"] == "4.5"


def test_手动选择优先且切回自动后按当前模型识别():
    config = {"type": "volcengine_image_generation", "model": PRO, "ark_model_family": "lite"}
    provider = ProviderVolcengineImageGeneration(config, {})
    assert provider._模型族(PRO) == "lite"
    config["ark_model_family"] = "auto"
    assert provider._模型族(PRO) == "pro"
    assert provider._模型族(LITE) == "lite"
    config["ark_model_family"] = "invalid"
    with pytest.raises(ValueError, match="能力族"):
        provider._模型族(PRO)
