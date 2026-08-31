"""生图工具：列表必须标明「模型ID」，供 ldmbot_generate_image 的 model 参数使用。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.core.tools import image_generation_tools as image_tools  # noqa: E402
from astrbot.core.tools.image_generation_tools import (  # noqa: E402
    ListImageGenerationModelsTool,
)


def _provider(provider_id: str, model: str) -> SimpleNamespace:
    return SimpleNamespace(provider_config={"id": provider_id, "model": model})


def test_list_image_generation_models_labels_model_id():
    async def _run():
        providers = [
            _provider("seedream-4", "doubao-seedream-4-0"),
            _provider("gpt-image", "gpt-image-1"),
        ]
        plugin_context = MagicMock()
        plugin_context.get_using_image_generation_provider.return_value = None
        context = MagicMock()
        context.context.context = plugin_context

        with patch.object(
            image_tools,
            "_iter_image_generation_providers",
            return_value=providers,
        ):
            result = await ListImageGenerationModelsTool().call(context)

        assert "模型ID: seedream-4 | 模型: doubao-seedream-4-0" in result
        assert "模型ID: gpt-image | 模型: gpt-image-1" in result
        assert "「模型ID」" in result
        assert "- seedream-4 | 模型:" not in result

    asyncio.run(_run())


def test_select_missing_model_error_labels_model_id():
    plugin_context = MagicMock()
    providers = [_provider("seedream-4", "doubao-seedream-4-0")]
    with patch.object(
        image_tools,
        "_iter_image_generation_providers",
        return_value=providers,
    ):
        provider, error = image_tools._select_image_generation_provider(
            plugin_context,
            "不存在的模型",
        )

    assert provider is None
    assert "模型ID: seedream-4 | 模型: doubao-seedream-4-0" in error
