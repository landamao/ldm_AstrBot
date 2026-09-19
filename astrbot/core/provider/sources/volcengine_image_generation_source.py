from __future__ import annotations

import base64
import copy
import io
import re

import httpx
from PIL import Image

from astrbot import logger
from astrbot.core.provider.entities import (
    GeneratedImage,
    ProviderType,
    format_provider_test_label,
)

from ..register import register_provider_adapter
from .openai_image_generation_source import ProviderOpenAIImageGeneration

# 来自方舟 API 的模型协议限制，不是应用层并发或数量配置。
_模型能力 = {
    "pro": ({"1K", "1.5K", "2K"}, 921600, 4624220, 10),
    "lite": ({"2K", "3K", "4K"}, 3686400, 16777216, 14),
    "4.5": ({"2K", "4K"}, 3686400, 16777216, 14),
    "4.0": ({"1K", "2K", "4K"}, 921600, 16777216, 14),
}
_可选参数 = {
    "size",
    "response_format",
    "watermark",
    "sequential_image_generation",
    "sequential_image_generation_options",
    "stream",
    "output_format",
    "background",
    "layer_decomposition",
    "optimize_prompt_options",
    "tools",
}


@register_provider_adapter(
    "volcengine_image_generation",
    "火山方舟 Seedream 生图 API 提供商适配器",
    provider_type=ProviderType.IMAGE_GENERATION,
    provider_display_name="火山引擎生图",
)
class ProviderVolcengineImageGeneration(ProviderOpenAIImageGeneration):
    """复用会话、密钥及健康检查，所有生图均直接调用方舟 JSON 接口。"""

    def _模型族(self, model: str) -> str:
        family = self.provider_config.get("ark_model_family", "auto")
        if family != "auto":
            if family not in _模型能力:
                raise ValueError("火山模型能力族无效，请选择自动识别或已支持的能力族")
            return family
        for prefix, family in (
            ("doubao-seedream-5-0-pro-", "pro"),
            ("doubao-seedream-5-0-lite-", "lite"),
            ("doubao-seedream-5-0-", "lite"),
            ("doubao-seedream-4-5-", "4.5"),
            ("doubao-seedream-4-0-", "4.0"),
        ):
            if model.startswith(prefix):
                return family
        raise ValueError("无法识别模型能力，请在该模型的配置中手动选择火山模型能力族")

    def _构造请求(self, prompt, model, n, size, image) -> dict:
        if not model:
            raise ValueError("火山生图未配置模型 ID")
        family = self._模型族(model)
        pro = family == "pro"
        if type(n) is not int or not 1 <= n <= 15:
            raise ValueError("生成数量必须是 1～15 的整数")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("火山生图提示词不能为空")
        options = self.provider_config.get("ark_image_options", {})
        if not isinstance(options, dict):
            raise ValueError("火山生图参数必须是对象")
        extra = copy.deepcopy(options)
        if extra.keys() - _可选参数:
            raise ValueError("火山生图包含不支持的参数，请按方舟接口文档配置")
        # 不能通过自定义请求体绕过能力校验或覆盖 model/prompt/image。
        if self.provider_config.get("custom_extra_body"):
            raise ValueError("火山生图请使用「火山生图参数」，不支持通用自定义请求体")
        for key in ("watermark", "stream", "layer_decomposition"):
            if key in extra and type(extra[key]) is not bool:
                raise ValueError(f"火山生图参数「{key}」必须是布尔值")
        if extra.get("stream") or (pro and "stream" in extra):
            raise ValueError("当前火山适配器不支持流式生图；pro 不支持 stream 参数")
        if "layer_decomposition" in extra:
            raise ValueError("当前火山适配器尚未接入图层拆分")
        for key, choices in (
            ("response_format", ("url", "b64_json")),
            ("output_format", ("png", "jpeg")),
            ("background", ("opaque", "transparent")),
            ("sequential_image_generation", ("auto", "disabled")),
        ):
            if key in extra and extra[key] not in choices:
                raise ValueError(f"火山生图参数「{key}」取值无效")
        if "output_format" in extra and family not in ("pro", "lite"):
            raise ValueError("仅 Seedream 5.0 pro/lite 支持 output_format")
        if "background" in extra and not pro:
            raise ValueError("仅 Seedream 5.0 pro 支持透明背景配置")
        if "tools" in extra:
            if family != "lite":
                raise ValueError("仅 Seedream 5.0 lite 支持联网搜索")
            if extra["tools"] != [{"type": "web_search"}]:
                raise ValueError('联网搜索工具参数应为 [{"type": "web_search"}]')
        if "optimize_prompt_options" in extra:
            opt = extra["optimize_prompt_options"]
            if not isinstance(opt, dict) or opt.keys() - {"mode"}:
                raise ValueError("提示词优化参数仅支持 mode")
            mode = opt.get("mode", "standard")
            if mode not in ("standard", "fast") or (
                mode == "fast" and family in ("lite", "4.5")
            ):
                raise ValueError("当前模型不支持该提示词优化模式")

        refs = image or []
        tiers, min_pixels, max_pixels, max_refs = _模型能力[family]
        if len(refs) > max_refs:
            raise ValueError(f"当前模型最多支持 {max_refs} 张参考图")
        group_keys = {
            "sequential_image_generation",
            "sequential_image_generation_options",
        }
        if pro and (n > 1 or group_keys & extra.keys()):
            raise ValueError("Seedream 5.0 pro 不支持组图及组图参数")
        if not pro:
            mode = extra.get(
                "sequential_image_generation", "auto" if n > 1 else "disabled"
            )
            if n > 1 and mode == "disabled":
                raise ValueError("生成多张图片时不能禁用组图")
            group_options = extra.get("sequential_image_generation_options", {})
            if not isinstance(group_options, dict) or group_options.keys() - {
                "max_images"
            }:
                raise ValueError("组图参数仅支持 max_images")
            if mode != "auto" and "sequential_image_generation_options" in extra:
                raise ValueError("组图 max_images 仅在 auto 模式生效")
            count = group_options.get("max_images", n)
            if type(count) is not int or not 1 <= count <= 15:
                raise ValueError("组图 max_images 必须是 1～15 的整数")
            if len(refs) + count > 15:
                raise ValueError("参考图与最多生成图片合计不能超过 15 张")
            if mode == "auto":
                extra["sequential_image_generation"] = "auto"
                extra["sequential_image_generation_options"] = {"max_images": count}

        clean_size = size if size is not None else extra.get("size")
        clean_size = str(clean_size).strip() if clean_size is not None else ""
        extra.pop("size", None)
        if clean_size:
            if clean_size not in tiers:
                match = re.fullmatch(r"([1-9]\d*)x([1-9]\d*)", clean_size)
                if not match:
                    raise ValueError("当前模型不支持该尺寸档位")
                width, height = map(int, match.groups())
                if not (
                    min_pixels <= width * height <= max_pixels
                    and 1 / 16 <= width / height <= 16
                ):
                    raise ValueError("图片尺寸的总像素或宽高比超出当前模型范围")
            extra["size"] = clean_size

        transparent = extra.get("background") == "transparent"
        if transparent and (len(refs) != 1 or extra.get("output_format") == "jpeg"):
            raise ValueError("透明背景需要单张带透明通道的参考图，不能输出 JPEG")
        data_urls = []
        for raw, mime in refs:
            if not raw or len(raw) > 30 * 1024 * 1024:
                raise ValueError("参考图不能为空且每张不能超过 30MB")
            mime = mime.lower()
            if mime not in {
                "image/jpeg",
                "image/png",
                "image/webp",
                "image/bmp",
                "image/tiff",
                "image/gif",
                "image/heic",
                "image/heif",
            }:
                raise ValueError("参考图格式不受方舟支持")
            if mime not in ("image/heic", "image/heif"):
                try:
                    with Image.open(io.BytesIO(raw)) as picture:
                        width, height = picture.size
                        has_alpha = (
                            "A" in picture.getbands() or "transparency" in picture.info
                        )
                        if transparent and (not has_alpha or picture.format == "JPEG"):
                            raise ValueError("透明背景需要带透明通道的非 JPEG 参考图")
                        if not (
                            width > 14
                            and height > 14
                            and 196 <= width * height <= 36000000
                            and 1 / 16 <= width / height <= 16
                        ):
                            raise ValueError("参考图尺寸、总像素或宽高比不符合方舟要求")
                        picture.verify()
                except (OSError, SyntaxError) as exc:
                    raise ValueError("参考图无法解码") from exc
            data_urls.append(f"data:{mime};base64,{base64.b64encode(raw).decode()}")
        payload = {"model": model, "prompt": prompt, **extra}
        if data_urls:
            payload["image"] = data_urls[0] if len(data_urls) == 1 else data_urls
        return payload

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        n: int = 1,
        size: str | None = None,
        image: list[tuple[bytes, str]] | None = None,
    ) -> list[GeneratedImage]:
        if not self.api_base:
            raise ValueError("火山生图未配置接口地址")
        payload = self._构造请求(prompt, model or self.get_model(), n, size, image)
        assert self.session is not None
        response = await self.session.post(
            self._build_url("/images/generations"),
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        return await self._parse_images_response(response)

    async def _parse_images_response(
        self, response: httpx.Response
    ) -> list[GeneratedImage]:
        if response.status_code >= 400:
            detail = self._error_detail(response).strip()
            raise RuntimeError(
                f"火山生图 API 返回 HTTP {response.status_code}: {detail}"
            )
        try:
            data = response.json()
        except Exception as exc:
            raise RuntimeError("火山生图返回非 JSON 响应") from exc
        if not isinstance(data, dict):
            raise RuntimeError("火山生图响应结构无效")
        if data.get("error"):
            raise RuntimeError(f"火山生图失败: {self._错误文本(data['error'])}")
        items = data.get("data")
        if not isinstance(items, list):
            raise RuntimeError("火山生图响应结构无效: 缺少图片数组")
        images: list[GeneratedImage] = []
        failures = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                failures.append(f"图片 {index + 1}: 数据格式错误")
                continue
            if item.get("error"):
                failures.append(f"图片 {index + 1}: {self._错误文本(item['error'])}")
                continue
            try:
                if item.get("b64_json"):
                    encoded = item["b64_json"]
                elif item.get("url"):
                    encoded, _ = await self._download_image(item["url"])
                else:
                    raise ValueError("缺少图片内容")
                raw = base64.b64decode(encoded, validate=True)
                with Image.open(io.BytesIO(raw)) as picture:
                    picture.verify()
                with Image.open(io.BytesIO(raw)) as picture:
                    mime = Image.MIME[picture.format]
                    picture.load()
                images.append(GeneratedImage(base64_data=encoded, mime_type=mime))
            except Exception as exc:
                # 不把异常原文中的签名 URL、Base64 或凭据写入日志。
                failures.append(
                    f"图片 {index + 1}: 下载或解码失败（{type(exc).__name__}）"
                )
        label = format_provider_test_label(
            self.provider_config.get("id"), self.get_model()
        )
        if failures:
            logger.warning("火山生图结果不完整: %s: %s", label, "；".join(failures))
        if not images:
            raise RuntimeError(
                "火山生图未返回可用图片: " + ("；".join(failures) or "结果为空")
            )
        logger.info(
            "火山生图完成: %s: 成功图片: %s: 失败图片: %s",
            label,
            len(images),
            len(failures),
        )
        return images

    @staticmethod
    def _错误文本(error: object) -> str:
        if not isinstance(error, dict):
            return "接口返回未知错误"
        code = str(error.get("code") or "未知错误码")[:120]
        message = str(error.get("message") or "未提供错误说明")[:500]
        return f"{message}（{code}）"
