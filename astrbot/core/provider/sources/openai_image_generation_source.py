from __future__ import annotations

import base64

import httpx

from astrbot import logger
from astrbot.api.provider import Provider
from astrbot.core.provider.entities import GeneratedImage, ProviderType
from astrbot.core.utils.network_utils import create_proxy_client

from ..register import register_provider_adapter


@register_provider_adapter(
    "openai_image_generation",
    "OpenAI 兼容生图 API 提供商适配器",
    provider_type=ProviderType.IMAGE_GENERATION,
    default_config_tmpl={
        "id": "openai_image",
        "provider": "openai",
        "type": "openai_image_generation",
        "provider_type": "image_generation",
        "enable": False,
        "key": [],
        "api_base": "https://api.openai.com/v1",
        "timeout": 300,
        "use_global_proxy": True,
        "proxy": "",
        "custom_extra_body": {},
    },
)
class ProviderOpenAIImageGeneration(Provider):
    """调用 OpenAI 兼容的 ``/images/generations`` 接口生成图片。

    适用于 OpenAI 官方（dall-e / gpt-image）、火山引擎 Ark（Seedream）、
    阿里云百炼兼容模式等所有兼容该接口的生图服务。
    """

    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.api_keys: list[str] = super().get_keys()
        self.chosen_api_key: str = self.api_keys[0] if self.api_keys else ""
        self.timeout = provider_config.get("timeout", 300)
        try:
            self.timeout = int(self.timeout)
        except (TypeError, ValueError):
            self.timeout = 300
        self.api_base = str(provider_config.get("api_base", "")).rstrip("/")
        self.proxy = self.get_proxy()

        self.session: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        self.session = create_proxy_client(
            "OpenAI-ImageGeneration",
            self.proxy,
        )

    async def terminate(self) -> None:
        if self.session:
            await self.session.aclose()
            self.session = None

    def _build_url(self, path: str) -> str:
        return f"{self.api_base}{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.chosen_api_key}"}
        return headers

    @staticmethod
    def _mime_from_response(response: httpx.Response, default: str) -> str:
        content_type = (response.headers.get("content-type") or "").split(";")[0]
        content_type = content_type.strip().lower()
        return content_type if content_type.startswith("image/") else default

    async def _download_image(
        self,
        url: str,
        default_mime: str = "image/png",
    ) -> tuple[str, str]:
        """下载 URL 图片并返回 (base64, mime_type)。"""
        assert self.session is not None
        response = await self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        mime = self._mime_from_response(response, default_mime)
        return (
            base64.b64encode(response.content).decode("utf-8"),
            mime,
        )

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        n: int = 1,
        size: str | None = None,
        image: list[tuple[bytes, str]] | None = None,
    ) -> list[GeneratedImage]:
        """生成图片，返回图片列表（URL 结果会被自动下载为 base64）。

        ``image`` 传入 ``(bytes, mime_type)`` 列表时走参考图生图：优先请求
        OpenAI 兼容的 ``/images/edits``（multipart），网关不支持（404/405）
        则回退 ``/images/generations`` + ``image`` 字段（火山 Ark Seedream 的
        兼容写法）。
        """
        if not self.api_base:
            raise RuntimeError("生图提供商未配置 api_base")

        model_name = model or self.provider_config.get("model", "")
        count = max(1, min(int(n or 1), 6))
        clean_size = str(size).strip() if size else ""
        refs = [
            (data, (mime or "image/png").lower())
            for data, mime in (image or [])
            if data
        ]

        extra_body = self.provider_config.get("custom_extra_body")
        extra = extra_body if isinstance(extra_body, dict) and extra_body else {}

        assert self.session is not None
        if refs:
            response = await self._request_image_edits(
                prompt,
                model_name,
                count,
                clean_size,
                refs,
            )
            if response.status_code in (404, 405):
                logger.info(
                    "生图网关不支持 /images/edits(HTTP %s)，回退 /images/generations + image 字段",
                    response.status_code,
                )
                payload = self._build_generation_payload(
                    prompt,
                    model_name,
                    count,
                    clean_size,
                    extra,
                )
                data_urls = [
                    f"data:{mime};base64,{base64.b64encode(data).decode('utf-8')}"
                    for data, mime in refs
                ]
                payload["image"] = data_urls[0] if len(data_urls) == 1 else data_urls
                response = await self.session.post(
                    self._build_url("/images/generations"),
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
        else:
            payload = self._build_generation_payload(
                prompt,
                model_name,
                count,
                clean_size,
                extra,
            )
            response = await self.session.post(
                self._build_url("/images/generations"),
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )

        return await self._parse_images_response(response)

    def _build_generation_payload(
        self,
        prompt: str,
        model_name: str,
        count: int,
        size: str,
        extra: dict,
    ) -> dict:
        payload: dict = {
            "model": model_name,
            "prompt": prompt,
            "n": count,
        }
        if size:
            payload["size"] = size
        payload.update(extra)
        return payload

    async def _request_image_edits(
        self,
        prompt: str,
        model_name: str,
        count: int,
        size: str,
        refs: list[tuple[bytes, str]],
    ) -> httpx.Response:
        assert self.session is not None
        data: dict[str, str] = {
            "model": model_name,
            "prompt": prompt,
            "n": str(count),
        }
        if size:
            data["size"] = size
        files: list[tuple[str, tuple[str, bytes, str]]] = []
        for index, (image_bytes, mime) in enumerate(refs):
            ext = mime.split("/")[-1] or "png"
            field = "image" if len(refs) == 1 else "image[]"
            files.append((field, (f"image-{index}.{ext}", image_bytes, mime)))
        return await self.session.post(
            self._build_url("/images/edits"),
            headers=self._headers(),
            data=data,
            files=files,
            timeout=self.timeout,
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """从错误响应里提取可读信息：优先解析 JSON 的 error.message。"""
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return response.text[:300]
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "").strip()
                code = str(err.get("code") or "").strip()
                if message:
                    if len(message) > 500:
                        message = message[:500] + "…"
                    return f"{message}（{code}）" if code else message
            if isinstance(data.get("message"), str) and data["message"]:
                return data["message"][:300]
        return response.text[:300]

    async def _parse_images_response(
        self,
        response: httpx.Response,
    ) -> list[GeneratedImage]:
        if response.status_code >= 400:
            detail = self._error_detail(response).strip()
            if not detail:
                detail = response.reason_phrase or "服务无返回内容"
            logger.error(
                "生图请求失败(%s)：HTTP %s - %s",
                self.provider_config.get("id"),
                response.status_code,
                detail,
            )
            raise RuntimeError(f"生图 API 返回 HTTP {response.status_code}: {detail}")

        try:
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"生图 API 返回了非 JSON 响应：{exc}") from exc

        items = data.get("data")
        if not isinstance(items, list) or not items:
            raise RuntimeError("生图 API 未返回任何图片数据")

        images: list[GeneratedImage] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            revised_prompt = item.get("revised_prompt") or None
            if item.get("b64_json"):
                images.append(
                    GeneratedImage(
                        base64_data=item["b64_json"],
                        mime_type="image/png",
                        revised_prompt=revised_prompt,
                    )
                )
            elif item.get("url"):
                base64_data, mime = await self._download_image(item["url"])
                images.append(
                    GeneratedImage(
                        base64_data=base64_data,
                        mime_type=mime,
                        revised_prompt=revised_prompt,
                    )
                )
        if not images:
            raise RuntimeError("生图 API 返回的数据中没有可用的图片")
        return images

    async def get_models(self) -> list[str]:
        if not self.api_base:
            raise RuntimeError("获取模型列表失败：未配置 api_base")
        try:
            assert self.session is not None
            response = await self.session.get(
                self._build_url("/models"),
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "获取模型列表失败(%s, %s)：%s",
                self.provider_config.get("id"),
                self.api_base,
                exc,
            )
            raise RuntimeError(f"获取模型列表失败：{exc}") from exc
        models = data.get("data") if isinstance(data, dict) else None
        result: list[str] = []
        if isinstance(models, list):
            for item in models:
                if isinstance(item, dict) and item.get("id"):
                    result.append(str(item["id"]))
        return result

    def get_current_key(self) -> str:
        return self.chosen_api_key

    def get_keys(self) -> list[str]:
        return self.api_keys

    def set_key(self, key: str) -> None:
        self.chosen_api_key = key

    async def text_chat(self, *args, **kwargs):
        raise NotImplementedError("生图模型不支持文本对话，请使用 generate_image。")

    async def test(self, timeout: float = 45.0) -> None:
        """测试生图提供商连通性：请求模型列表而非真实生图，避免产生费用。"""
        import asyncio

        if not self.api_base:
            raise RuntimeError("未配置 api_base")
        await asyncio.wait_for(self.get_models(), timeout=timeout)
