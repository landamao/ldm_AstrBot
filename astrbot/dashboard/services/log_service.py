from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator

from astrbot.core import LogBroker, logger
from astrbot.core.config.astrbot_config import AstrBotConfig


class LogServiceError(Exception):
    pass


class LogService:
    def __init__(self, log_broker: LogBroker, config: AstrBotConfig) -> None:
        self.log_broker = log_broker
        self.config = config

    @staticmethod
    def format_log_sse(log: dict, ts: float) -> str:
        payload = {
            "type": "log",
            **log,
        }
        return f"id: {ts}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def stream_log_events(
        self, last_event_id: str | None
    ) -> AsyncGenerator[str, None]:
        queue = None
        # 补发水位线：队列中 time <= 水位线的消息都已被补发，跳过以避免重复
        watermark: float | None = None
        try:
            # 先注册队列再补发：补发期间产生的新日志会积压在队列中不会丢失；
            # 旧实现先 replay 后 register，两步之间产生的日志会丢失
            queue = self.log_broker.register()

            if last_event_id:
                try:
                    watermark = float(last_event_id)
                except ValueError:
                    watermark = None
                if watermark is not None:
                    for log_item in list(self.log_broker.log_cache):
                        try:
                            log_ts = float(log_item.get("time", 0))
                        except (TypeError, ValueError):
                            continue
                        if log_ts > watermark:
                            watermark = log_ts
                            yield self.format_log_sse(log_item, log_ts)

            while True:
                message = await queue.get()
                current_ts = message.get("time", time.time())
                if watermark is not None and current_ts <= watermark:
                    continue
                yield self.format_log_sse(message, current_ts)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"Log SSE 连接错误: {exc}")
        finally:
            if queue:
                self.log_broker.unregister(queue)

    def get_log_history(self) -> dict:
        try:
            return {"logs": list(self.log_broker.log_cache)}
        except Exception as exc:
            logger.error(f"获取日志历史失败: {exc}")
            raise LogServiceError(f"获取日志历史失败: {exc}") from exc

    def get_trace_settings(self) -> dict:
        try:
            return {"trace_enable": self.config.get("trace_enable", True)}
        except Exception as exc:
            logger.error(f"获取 Trace 设置失败: {exc}")
            raise LogServiceError(f"获取 Trace 设置失败: {exc}") from exc

    def update_trace_settings(self, payload: dict | None) -> str:
        try:
            if payload is None:
                raise LogServiceError("请求数据为空")

            trace_enable = payload.get("trace_enable")
            if trace_enable is not None:
                self.config["trace_enable"] = bool(trace_enable)
                self.config.save_config()

            return "Trace 设置已更新"
        except LogServiceError:
            raise
        except Exception as exc:
            logger.error(f"更新 Trace 设置失败: {exc}")
            raise LogServiceError(f"更新 Trace 设置失败: {exc}") from exc
