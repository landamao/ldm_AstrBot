"""上游返回的模型上下文/输出限额注册表。

各聊天提供商源的 ``get_models()`` 在解析上游模型列表时，把上游明确给出的
上下文窗口等限额按 ``(来源, 模型 ID)`` 记录到本注册表；运行时与 WebUI
按「提供商手动配置 > 上游限额 > 内置静态表 > 兜底值」的优先级解析。

键的 ``source`` 取该提供商实例的 ``provider_config["id"]``（提供商源场景下
是「源 ID/模型 ID」形式的完整 ID），因此同名的模型 ID 不会跨提供商串数据。
"""

from __future__ import annotations

import threading

from astrbot.core import logger

# 上游模型限额条目：context 为上下文窗口 token 数，output 为最大输出
# token 数，未知字段记 0（表示上游未给出）。
UpstreamModelLimit = dict[str, int]

# {(source_id, model_id): {"context": int, "output": int}}
_UPSTREAM_LIMITS: dict[tuple[str, str], UpstreamModelLimit] = {}
_LOCK = threading.Lock()


def _normalize_limit(context: int | None, output: int | None) -> UpstreamModelLimit | None:
    """归一化上游给的限额，全部缺失或非正数时返回 None（视为上游没给）。"""

    def _clean(value: int | None) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 0
        return value if value > 0 else 0

    limit = {"context": _clean(context), "output": _clean(output)}
    if not any(limit.values()):
        return None
    return limit


def record_model_limits(
    source_id: str,
    limits: dict[str, tuple[int | None, int | None]],
) -> None:
    """批量记录某提供商源下若干模型的上游限额。

    Args:
        source_id: 提供商（源）ID，取 ``provider_config["id"]``。
        limits: ``{模型 ID: (context, output)}``，值为 None 表示上游未给出。
    """
    if not source_id or not limits:
        return
    recorded = 0
    with _LOCK:
        for model_id, (context, output) in limits.items():
            if not model_id:
                continue
            limit = _normalize_limit(context, output)
            if limit is None:
                _UPSTREAM_LIMITS.pop((source_id, model_id), None)
                continue
            _UPSTREAM_LIMITS[(source_id, model_id)] = limit
            recorded += 1
    if recorded:
        logger.debug(
            "已记录 %d 个模型的上游限额（来源 %s）",
            recorded,
            source_id,
        )


def get_model_limits(source_id: str, model_id: str | None) -> UpstreamModelLimit | None:
    """查询某提供商下某模型的上游限额，没有记录返回 None。"""
    if not source_id or not model_id:
        return None
    with _LOCK:
        return _UPSTREAM_LIMITS.get((source_id, model_id))


def all_model_limits_for_source(source_id: str) -> dict[str, UpstreamModelLimit]:
    """取某提供商源下全部已记录的上游限额，``{模型 ID: 限额}``。"""
    with _LOCK:
        return {
            model_id: dict(limit)
            for (sid, model_id), limit in _UPSTREAM_LIMITS.items()
            if sid == source_id
        }


def clear_limits_for_source(source_id: str) -> None:
    """某提供商源重新拉取模型列表前清掉旧记录，避免上游已下线的模型残留。"""
    with _LOCK:
        stale = [key for key in _UPSTREAM_LIMITS if key[0] == source_id]
        for key in stale:
            del _UPSTREAM_LIMITS[key]
