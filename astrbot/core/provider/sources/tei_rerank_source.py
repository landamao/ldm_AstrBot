import aiohttp

from astrbot import logger

from ..entities import ProviderType, RerankResult
from ..provider import RerankProvider
from ..register import register_provider_adapter


@register_provider_adapter(
    "tei_rerank",
    "HuggingFace TEI Rerank 适配器",
    provider_type=ProviderType.RERANK,
)
class TEIRerankProvider(RerankProvider):
    """HuggingFace Text Embeddings Inference (TEI) 重排序适配器。

    TEI 不是商业云平台，而是 HuggingFace 的自托管/本地推理服务。
    典型场景：本机或内网部署 TEI 后，对知识库检索结果做 rerank。
    """

    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.provider_config = provider_config
        self.provider_settings = provider_settings
        self.api_key = provider_config.get("rerank_api_key", "")
        self.base_url = provider_config.get(
            "rerank_api_base", "http://127.0.0.1:8080"
        ).rstrip("/")
        self.timeout = provider_config.get("timeout", 20)
        self.truncate = provider_config.get("tei_rerank_truncate", False)
        self.truncation_direction = provider_config.get(
            "tei_rerank_truncation_direction", "right"
        ).lower()
        self.raw_scores = provider_config.get("tei_rerank_raw_scores", False)
        self.return_text = provider_config.get("tei_rerank_return_text", False)

        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        self.client = aiohttp.ClientSession(
            headers=h,
            timeout=aiohttp.ClientTimeout(total=self.timeout),
        )

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int | None = None,
    ) -> list[RerankResult]:
        if not self.client or self.client.closed:
            logger.error("[TEI Rerank] 客户端未初始化或已关闭")
            return []
        if not documents:
            logger.warning("[TEI Rerank] 文档列表为空，返回空结果")
            return []
        if not query.strip():
            logger.warning("[TEI Rerank] 查询为空，返回空结果")
            return []
        payload: dict = {
            "query": query,
            "texts": documents,
        }

        if self.truncate:
            payload["truncate"] = True
            payload["truncation_direction"] = self.truncation_direction
        if self.raw_scores:
            payload["raw_scores"] = True
        if self.return_text:
            payload["return_text"] = True

        try:
            rerank_url = f"{self.base_url}/rerank"
            logger.debug(
                f"[TEI Rerank] 请求: query='{query[:50]}...', "
                f"doc_count={len(documents)}"
            )
            async with self.client.post(rerank_url, json=payload) as response:
                if response.status != 200:
                    try:
                        error_data = await response.json()
                        error_msg = error_data.get(
                            "error", error_data.get("message", "Unknown error")
                        )
                    except Exception:
                        error_msg = await response.text()
                    logger.error(
                        f"[TEI Rerank] API 返回 HTTP {response.status}: {error_msg}"
                    )
                    raise Exception(f"TEI Rerank HTTP {response.status}: {error_msg}")

                response_data = await response.json()

                if not response_data:
                    logger.warning(
                        f"[TEI Rerank] API 返回空结果。Response: {response_data}"
                    )
                    return []

                results = []
                for rank_item in response_data:
                    results.append(
                        RerankResult(
                            index=rank_item["index"],
                            relevance_score=rank_item["score"],
                        )
                    )

                if top_n is not None and top_n > 0:
                    results = results[:top_n]

                logger.debug(f"[TEI Rerank] 成功返回 {len(results)} 条结果")
                return results

        except aiohttp.ClientError as e:
            logger.error(f"[TEI Rerank] 网络错误: {e}")
            raise Exception(f"TEI Rerank network error: {e}") from e
        except Exception as e:
            logger.error(f"[TEI Rerank] 错误: {e}")
            raise

    async def test(self) -> None:
        if not self.client or self.client.closed:
            raise Exception("TEI Rerank 客户端未初始化")

        health_url = f"{self.base_url}/health"
        try:
            async with self.client.get(health_url) as response:
                if response.status != 200:
                    raise Exception(
                        f"TEI 健康检查失败 {self.base_url}: HTTP {response.status}"
                    )
        except aiohttp.ClientError as e:
            raise Exception(f"无法连接 TEI 服务 {self.base_url}: {e}") from e
        result = await self.rerank("Apple", documents=["apple", "banana"])
        if not result:
            raise Exception(
                "TEI Rerank 测试失败：未返回结果。请确认 TEI 已加载 reranker 模型。"
            )

    async def terminate(self) -> None:
        if self.client and not self.client.closed:
            await self.client.close()
            self.client = None
