from __future__ import annotations

import base64
from typing import Any

from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.provider import Provider
from astrbot.core.provider.entities import GeneratedImage
from astrbot.core.provider.sources.openai_image_generation_source import (
    ProviderOpenAIImageGeneration,
)
from astrbot.core.tools.registry import builtin_tool
from astrbot.core.utils.media_utils import MediaResolver

_MAX_REFERENCE_IMAGES = 10


async def _load_reference_images(
    media_refs: list[str],
) -> tuple[list[tuple[bytes, str]], str]:
    """把参考图引用（本地路径 / URL）读成 (bytes, mime) 列表。

    返回 (refs, error)，error 非空时 refs 为空。
    """
    loaded: list[tuple[bytes, str]] = []
    for ref in media_refs:
        try:
            media = await MediaResolver(ref, media_type="image").to_base64_data(
                strict=True,
            )
        except Exception as exc:  # noqa: BLE001
            return [], f"错误：读取参考图失败 `{ref}`：{exc}"
        if media is None or not media.mime_type.startswith("image/"):
            return [], f"错误：参考图 `{ref}` 不是有效的图片文件。"
        loaded.append((base64.b64decode(media.base64_data), media.mime_type))
    return loaded, ""


def _iter_image_generation_providers(
    plugin_context,
) -> list[ProviderOpenAIImageGeneration]:
    providers: list[ProviderOpenAIImageGeneration] = []
    for prov in plugin_context.get_all_image_generation_providers():
        if isinstance(prov, ProviderOpenAIImageGeneration):
            providers.append(prov)
    return providers


def _select_image_generation_provider(
    plugin_context,
    model: str,
) -> tuple[ProviderOpenAIImageGeneration | None, str]:
    """根据 model 参数选择生图提供商。

    返回 (provider, error)。model 支持提供商实例 ID 精确匹配或模型名子串匹配；
    为空时使用默认生图提供商（用户配置的，未配置则为第一个）。
    """
    providers = _iter_image_generation_providers(plugin_context)
    if not providers:
        return None, "错误：没有可用的生图模型。请先在 WebUI「模型提供商-生图」中配置并启用生图模型。"

    if not model:
        default = plugin_context.get_using_image_generation_provider()
        if isinstance(default, ProviderOpenAIImageGeneration):
            return default, ""
        return providers[0], ""

    needle = model.strip()
    for prov in providers:
        if prov.provider_config.get("id") == needle:
            return prov, ""
    for prov in providers:
        prov_model = str(prov.provider_config.get("model", ""))
        if needle.lower() in prov_model.lower():
            return prov, ""

    available = "\n".join(
        f"- 模型ID: {prov.provider_config.get('id', '')} | 模型: {prov.provider_config.get('model', '')}"
        for prov in providers
    )
    return None, f"错误：找不到生图模型 `{needle}`。可用的生图模型有：\n{available}"


def _images_to_call_tool_result(
    images: list[GeneratedImage],
    summary: str,
) -> CallToolResult:
    content: list[Any] = [TextContent(type="text", text=summary)]
    for image in images:
        content.append(
            ImageContent(
                type="image",
                data=image.base64_data,
                mimeType=image.mime_type,
            )
        )
    return CallToolResult(content=content)


@builtin_tool
@dataclass
class GenerateImageTool(FunctionTool[AstrAgentContext]):
    """调用配置的生图模型生成图片。"""

    name: str = "ldmbot_generate_image"
    description: str = (
        "Generate images from a text prompt using the configured image generation "
        "models (e.g. Seedream, GPT-Image, DALL-E compatible APIs). "
        "Pass local file paths or image URLs in `image` for image-to-image "
        "generation (edit, restyle or reference existing images, e.g. the path "
        "from an `[Image Attachment: path ...]` in the conversation). "
        "Use the `model` argument to pick a specific image model; omit it to use "
        "the user's default image generation model. "
        "Use `ldmbot_list_image_generation_models` first if unsure which models exist."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "The image generation prompt. Describe the subject, style, "
                        "composition and details as clearly as possible."
                    ),
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Optional. The image generation model to use: a provider id "
                        "(e.g. `provider_id`) or a model name. If omitted, uses the "
                        "user's default image generation model (or the first available)."
                    ),
                },
                "image": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional reference image(s) for image-to-image: local "
                        "file paths or image URLs, at most "
                        f"{_MAX_REFERENCE_IMAGES}. Omit for pure text-to-image."
                    ),
                },
                "size": {
                    "type": "string",
                    "description": (
                        "Optional image size, e.g. `1024x1024`, `1024x1792`. "
                        "When omitted the size parameter is not sent and the "
                        "image model decides the size itself; only pass it when "
                        "the user explicitly asks for a specific size."
                    ),
                },
                "count": {
                    "type": "integer",
                    "description": (
                        "Optional number of images to generate (1-4). Default 1."
                    ),
                },
            },
            "required": ["prompt"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            return "错误：参数 prompt 不能为空。"

        plugin_context = context.context.context
        provider, error = _select_image_generation_provider(
            plugin_context,
            str(kwargs.get("model") or "").strip(),
        )
        if provider is None:
            return error

        size = str(kwargs.get("size") or "").strip() or None

        raw_refs = kwargs.get("image")
        if isinstance(raw_refs, str):
            raw_refs = [raw_refs]
        if not isinstance(raw_refs, list):
            raw_refs = []
        media_refs = [str(ref).strip() for ref in raw_refs if str(ref or "").strip()]
        if len(media_refs) > _MAX_REFERENCE_IMAGES:
            return (
                f"错误：参考图数量超过上限（最多 {_MAX_REFERENCE_IMAGES} 张），"
                f"当前传入了 {len(media_refs)} 个。"
            )
        reference_images: list[tuple[bytes, str]] = []
        if media_refs:
            reference_images, error = await _load_reference_images(media_refs)
            if error:
                return error

        try:
            count = int(kwargs.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, 4))

        provider_id = provider.provider_config.get("id", "")
        provider_model = provider.provider_config.get("model", "")
        logger.info(
            "生图工具调用：provider=%s, model=%s, count=%s, ref_images=%s, prompt=%s",
            provider_id,
            provider_model,
            count,
            len(reference_images),
            prompt[:80],
        )
        try:
            images = await provider.generate_image(
                prompt,
                n=count,
                size=size,
                image=reference_images,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("生图工具执行失败: %s", exc)
            return f"错误：生图失败：{exc}"

        if reference_images:
            summary = (
                f"已使用生图模型 {provider_id}（{provider_model}）"
                f"基于 {len(reference_images)} 张参考图生成 {len(images)} 张图片。"
                "图片已在下方，请检查后用 send_message_to_user（type='image'）发送给用户。"
            )
        else:
            summary = (
                f"已使用生图模型 {provider_id}（{provider_model}）生成 {len(images)} 张图片。"
                "图片已在下方，请检查后用 send_message_to_user（type='image'）发送给用户。"
            )
        return _images_to_call_tool_result(images, summary)


@builtin_tool
@dataclass
class ListImageGenerationModelsTool(FunctionTool[AstrAgentContext]):
    """列出当前可用的生图模型。"""

    name: str = "ldmbot_list_image_generation_models"
    description: str = (
        "List the available image generation models configured by the user. "
        "Call this before ldmbot_generate_image when the user asks which image "
        "models exist or wants to pick one."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        plugin_context = context.context.context
        providers = _iter_image_generation_providers(plugin_context)
        if not providers:
            return (
                "错误：没有可用的生图模型。"
                "请提示用户在 WebUI「模型提供商-生图」中配置并启用生图模型。"
            )

        default = plugin_context.get_using_image_generation_provider()
        default_id = (
            default.provider_config.get("id", "")
            if isinstance(default, Provider)
            else ""
        )

        lines = ["当前可用的生图模型："]
        for prov in providers:
            provider_id = prov.provider_config.get("id", "")
            model = prov.provider_config.get("model", "")
            marker = "（默认）" if provider_id == default_id else ""
            lines.append(f"- 模型ID: {provider_id} | 模型: {model}{marker}")
        lines.append(
            "生成图片时，把上面列出的「模型ID」作为 ldmbot_generate_image 的 "
            "`model` 参数传入即可；不传则使用默认生图模型。"
        )
        return "\n".join(lines)


__all__ = [
    "GenerateImageTool",
    "ListImageGenerationModelsTool",
]
