"""模型元数据拼装：内置静态表 + 上游限额注册表合并。

WebUI 各接口返回的 ``model_metadata`` 由此统一生成：以内置静态表
（LLM_METADATAS）为基础，叠加该提供商上游明确返回的限额
（``upstream_model_limits`` 注册表）。上游给出的 ``limit.context`` /
``limit.output`` 优先于静态表——上游数据是对所配置网关的实际额度，
静态表可能过期或不含小众模型。
"""

from __future__ import annotations

import copy

from astrbot.core.provider.upstream_model_limits import all_model_limits_for_source
from astrbot.core.utils.llm_metadata import LLM_METADATAS


def _apply_upstream_limit(metadata: dict, limit: dict) -> None:
    limit_view = metadata.setdefault("limit", {"context": 0, "output": 0})
    if limit.get("context"):
        limit_view["context"] = limit["context"]
    if limit.get("output"):
        limit_view["output"] = limit["output"]


def build_model_metadata(
    model_ids: list[str | None],
    source_id: str | None = None,
) -> dict[str, dict]:
    """按模型 ID 列表拼装元数据映射。

    Args:
        model_ids: 需要附元数据的模型 ID（自动去重跳过非字符串）。
        source_id: 提供商（源）ID；给定时叠加该源的上游限额记录。
            调用方拿不到源 ID 时传 None，仅返回静态表数据。

    Returns:
        ``{模型 ID: 元数据}``。静态表没有该模型但上游有限额时，生成只含
        ``limit`` 的部分元数据（前端按缺省处理 modalities 等字段）。
    """
    metadata: dict[str, dict] = {}
    seen: set[str] = set()
    for model_id in model_ids:
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            continue
        seen.add(model_id)
        if model_id in LLM_METADATAS:
            metadata[model_id] = copy.deepcopy(dict(LLM_METADATAS[model_id]))

    if not source_id:
        return metadata

    for model_id, limit in all_model_limits_for_source(source_id).items():
        if model_id in metadata:
            _apply_upstream_limit(metadata[model_id], limit)
        else:
            entry: dict = {"limit": {"context": 0, "output": 0}}
            _apply_upstream_limit(entry, limit)
            metadata[model_id] = entry
    return metadata
