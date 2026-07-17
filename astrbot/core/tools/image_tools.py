from __future__ import annotations

from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.provider import Provider
from astrbot.core.tools.registry import builtin_tool
from astrbot.core.utils.media_utils import (
    IMAGE_COMPRESS_DEFAULT_MAX_SIZE,
    IMAGE_COMPRESS_DEFAULT_QUALITY,
    compress_image,
)

_DEFAULT_CAPTION_PROMPT = "Please describe the image using Chinese."


def _get_image_compress_args(
    provider_settings: dict[str, Any] | None,
) -> tuple[bool, int, int]:
    if not isinstance(provider_settings, dict):
        return True, IMAGE_COMPRESS_DEFAULT_MAX_SIZE, IMAGE_COMPRESS_DEFAULT_QUALITY

    enabled = provider_settings.get("image_compress_enabled", True)
    if not isinstance(enabled, bool):
        enabled = True

    raw_options = provider_settings.get("image_compress_options", {})
    options = raw_options if isinstance(raw_options, dict) else {}

    max_size = options.get("max_size", IMAGE_COMPRESS_DEFAULT_MAX_SIZE)
    if not isinstance(max_size, int):
        max_size = IMAGE_COMPRESS_DEFAULT_MAX_SIZE
    max_size = max(max_size, 1)

    quality = options.get("quality", IMAGE_COMPRESS_DEFAULT_QUALITY)
    if not isinstance(quality, int):
        quality = IMAGE_COMPRESS_DEFAULT_QUALITY
    quality = min(max(quality, 1), 100)

    return enabled, max_size, quality


async def _compress_image_urls(
    image_urls: list[str],
    provider_settings: dict[str, Any] | None,
) -> list[str]:
    enabled, max_size, quality = _get_image_compress_args(provider_settings)
    if not enabled:
        return list(image_urls)

    compressed: list[str] = []
    for url in image_urls:
        try:
            compressed.append(
                await compress_image(url, max_size=max_size, quality=quality)
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Image compression failed for image caption tool: %s", exc)
            compressed.append(url)
    return compressed


@builtin_tool
@dataclass
class ImageCaptionTool(FunctionTool[AstrAgentContext]):
    """使用已配置的默认图片转述模型识别图片。"""

    name: str = "astrbot_image_caption"
    description: str = (
        "Recognize and describe one or more images using the configured default "
        "image caption model. Use this when you need to understand image content "
        "from local paths, http(s) URLs, or data URIs. "
        "Requires provider_settings.default_image_caption_provider_id to be set."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "image_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Images to recognize. Supports local file paths, http(s) URLs, "
                        "and data:image URIs."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": (
                        "Optional custom recognition prompt. If omitted, uses the "
                        "configured provider_settings.image_caption_prompt."
                    ),
                },
            },
            "required": ["image_urls"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        raw_urls = kwargs.get("image_urls")
        if not isinstance(raw_urls, list) or not raw_urls:
            return "错误：参数 image_urls 必须是非空数组。"

        image_urls: list[str] = []
        for item in raw_urls:
            url = str(item or "").strip()
            if url:
                image_urls.append(url)
        if not image_urls:
            return "错误：image_urls 中没有有效的图片路径或 URL。"

        event = context.context.event
        plugin_context = context.context.context
        cfg = plugin_context.get_config(umo=event.unified_msg_origin)
        provider_settings = cfg.get("provider_settings", {})
        if not isinstance(provider_settings, dict):
            provider_settings = {}

        provider_id = str(
            provider_settings.get("default_image_caption_provider_id") or ""
        ).strip()
        if not provider_id:
            return (
                "错误：未配置默认图片转述模型。"
                "请在配置中设置「默认图片转述模型」"
                "（provider_settings.default_image_caption_provider_id）。"
            )

        prov = plugin_context.get_provider_by_id(provider_id)
        if prov is None:
            return f"错误：找不到 ID 为 `{provider_id}` 的图片转述模型。"
        if not isinstance(prov, Provider):
            return (
                f"错误：`{provider_id}` 不是可用的对话模型提供商，"
                f"实际类型为 {type(prov)}。"
            )

        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            prompt = str(
                provider_settings.get("image_caption_prompt") or _DEFAULT_CAPTION_PROMPT
            ).strip()
            if not prompt:
                prompt = _DEFAULT_CAPTION_PROMPT

        try:
            compressed_urls = await _compress_image_urls(image_urls, provider_settings)
            logger.debug(
                "图片识别工具调用：provider=%s, image_count=%s",
                provider_id,
                len(compressed_urls),
            )
            llm_resp = await prov.text_chat(
                prompt=prompt,
                image_urls=compressed_urls,
            )
            text = (llm_resp.completion_text or "").strip()
            if not text:
                return "图片识别完成，但模型没有返回有效描述。"
            return text
        except Exception as exc:  # noqa: BLE001
            logger.error("图片识别工具执行失败: %s", exc)
            return f"错误：图片识别失败：{exc}"


__all__ = [
    "ImageCaptionTool",
]
