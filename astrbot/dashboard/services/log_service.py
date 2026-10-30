from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, quote_plus

from astrbot.core import LogBroker, logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.log import LogManager
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

REDACTED = "[REDACTED]"
_MAX_SECRET_LEN = 512

# 与官方 diagnostic_service 同源的脱敏正则（按纯文本日志场景裁剪）
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SECRET_KEY = re.compile(
    r"password|passwd|secret|token$|api[_-]?key|access[_-]?key|private[_-]?key|aes[_-]?key|"
    r"authorization|cookie|credential|(?:client|app|signing|encrypt(?:ion)?)[_-]?key|"
    r"recovery[_-]?code|(?:^|\.)key$|^headers$|^env$",
    re.IGNORECASE,
)
_AUTH = re.compile(r"\b(Bearer|Basic|ApiKey)\s+[^\s\"'<>]+", re.IGNORECASE)
_COOKIE = re.compile(r"(\b(?:set-cookie|cookie)\s*:\s*)[^\r\n]+", re.IGNORECASE)
_URL_AUTH = re.compile(r"(https?://)[^/\s@]+@", re.IGNORECASE)
_ASSIGNMENT = re.compile(r"(?<![\w.-])([\w.-]+)[\"']?\s*[:=]\s*")
_ASSIGNMENT_KEY = re.compile(
    r"token|secret|password|passwd|api[_-]?key|access[_-]?key|aes[_-]?key|"
    r"(?:client|app|signing|encrypt(?:ion)?)[_-]?key|recovery[_-]?code|"
    r"(?:authorization|cookie|\bkey)$",
    re.IGNORECASE,
)
_ASSIGNMENT_VALUE = re.compile(r"\"[^\"]*\"|'[^']*'|[^\s&,;}]+")
_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [\w ]*PRIVATE KEY-----.*?(?:-----END [\w ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
# 文件日志行首时间戳：[YYYY-MM-DD HH:mm:ss(.SSS)]
_LOG_TIME = re.compile(
    r"^\[(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:[.,](\d+))?\]"
)


class _LogRedactor:
    """按配置文件收集密钥并结合常见凭证格式对日志文本脱敏。"""

    def __init__(self, config) -> None:
        self.secrets: set[str] = set()
        self._sorted_secrets: list[str] | None = None
        if config is not None:
            self._collect(config)

    def _collect(self, value, sensitive: bool = False) -> None:
        try:
            if isinstance(value, dict):
                for key, item in value.items():
                    self._collect(item, sensitive or bool(_SECRET_KEY.search(str(key))))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    self._collect(item, sensitive)
            elif (
                sensitive
                and isinstance(value, str)
                and 0 < len(value) <= _MAX_SECRET_LEN
                and "\n" not in value
            ):
                self.secrets.update(
                    (value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1])
                )
                self._sorted_secrets = None
        except Exception:
            # 脱敏收集失败不应阻塞导出
            pass

    def text(self, value: str) -> str:
        if "\x1b[" in value:
            value = _ANSI.sub("", value)
        if "PRIVATE KEY-----" in value:
            value = _PRIVATE_KEY.sub(REDACTED, value)
        if self.secrets:
            if self._sorted_secrets is None:
                self._sorted_secrets = sorted(self.secrets, key=len, reverse=True)
            for secret in self._sorted_secrets:
                if secret in value:
                    value = value.replace(secret, REDACTED)
        lowered = value.lower()
        ascii_only = value.isascii()
        if not ascii_only or any(
            prefix in lowered for prefix in ("bearer", "basic", "apikey")
        ):
            value = _AUTH.sub(lambda match: f"{match[1]} {REDACTED}", value)
        if not ascii_only or "cookie" in lowered:
            value = _COOKIE.sub(lambda match: f"{match[1]}{REDACTED}", value)
        if "://" in value and "@" in value:
            value = _URL_AUTH.sub(lambda match: f"{match[1]}{REDACTED}@", value)
        if not ascii_only or any(
            hint in lowered
            for hint in (
                "token",
                "secret",
                "password",
                "passwd",
                "key",
                "code",
                "authorization",
                "cookie",
            )
        ):
            parts = []
            end = 0
            for match in _ASSIGNMENT.finditer(value):
                if match.start() < end or not _ASSIGNMENT_KEY.search(match[1]):
                    continue
                assigned = _ASSIGNMENT_VALUE.match(value, match.end())
                if assigned is not None:
                    parts.extend((value[end : match.end()], REDACTED))
                    end = assigned.end()
            parts.append(value[end:])
            value = "".join(parts)
        if any(
            prefix in value
            for prefix in ("sk-", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "eyJ")
        ):
            value = _TOKEN.sub(REDACTED, value)
        return value


class LogServiceError(Exception):
    pass


class LogService:
    def __init__(self, log_broker: LogBroker, config: AstrBotConfig) -> None:
        self.log_broker = log_broker
        self.config = config
        # 导出涉及全量日志文件的扫描与写盘，串行执行避免重复开销
        self._export_lock = threading.Lock()

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

    # ---------- 日志导出 ----------

    @staticmethod
    def _parse_file_conf(config: AstrBotConfig) -> tuple[bool, str | None]:
        """与 LogManager.configure_logger 一致地解析文件日志配置。"""
        if "log_file" in config:
            file_conf = config.get("log_file") or {}
            return bool(file_conf.get("enable", False)), file_conf.get("path")
        return bool(config.get("log_file_enable", False)), config.get("log_file_path")

    def _collect_log_files(self) -> list[Path]:
        """按时间顺序收集主日志文件与轮转文件（旧 → 新）。"""
        enable, file_path = self._parse_file_conf(self.config)
        if not enable:
            return []
        try:
            main = Path(LogManager._resolve_log_path(file_path))
        except Exception:
            return []
        if not main.is_file():
            return []

        files: list[Path] = []
        directory = main.parent
        seen: set[Path] = {main}
        # loguru 按大小轮转的命名：ldmbot.2026-10-30_10-00-00_123.log；
        # 兼容 .log.1 等传统轮转后缀
        for pattern in (
            f"{main.stem}.*{main.suffix}",
            f"{main.name}.*",
        ):
            for candidate in directory.glob(pattern):
                if candidate.is_file() and candidate not in seen:
                    seen.add(candidate)
                    files.append(candidate)
        files.append(main)
        try:
            files.sort(key=lambda p: p.stat().st_mtime)
        except OSError:
            pass
        return files

    @staticmethod
    def _line_timestamp(line: str) -> float | None:
        match = _LOG_TIME.match(line)
        if not match:
            return None
        year, month, day, hour, minute, second = (int(match[i]) for i in range(1, 7))
        frac = match[7] or "0"
        microsecond = int((frac + "000000")[:6])
        try:
            return datetime(
                year, month, day, hour, minute, second, microsecond
            ).timestamp()
        except ValueError:
            return None

    def _iter_export_lines(
        self,
        start_time: float | None,
        end_time: float | None,
    ):
        """产出导出文本行：优先读文件日志，未开启时回退 broker 缓存。"""
        files = self._collect_log_files()
        if files:
            for path in files:
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    mtime = None
                # 整文件都早于起始时间时直接跳过
                if start_time is not None and mtime is not None and mtime < start_time:
                    continue
                try:
                    with path.open("r", encoding="utf-8", errors="replace") as f:
                        keep = True
                        for line in f:
                            line = line.rstrip("\r\n")
                            ts = self._line_timestamp(line)
                            if ts is not None:
                                keep = (
                                    (start_time is None or ts >= start_time)
                                    and (end_time is None or ts <= end_time)
                                )
                            if keep:
                                yield line
                except OSError as exc:
                    logger.warning(f"导出日志时读取文件失败 {path}: {exc}")
            return

        # 回退：文件日志未开启（或文件缺失）时，导出内存缓存的最近日志
        for entry in list(self.log_broker.log_cache):
            try:
                ts = float(entry.get("time", 0))
            except (TypeError, ValueError):
                ts = None
            if ts is not None:
                if start_time is not None and ts < start_time:
                    continue
                if end_time is not None and ts > end_time:
                    continue
            data = str(entry.get("data", ""))
            yield _ANSI.sub("", data)

    def export_logs(
        self,
        *,
        redact: bool = True,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> Path:
        """按时间范围导出日志为文本文件，返回临时文件路径（响应后删除）。

        Args:
            redact: 是否对配置中的密钥及常见凭证格式脱敏。
            start_time: 起始时间（Unix 秒，含）。
            end_time: 结束时间（Unix 秒，含）。

        Returns:
            临时导出文件 Path。

        Raises:
            LogServiceError: 已有导出任务进行中，或时间范围非法。
        """
        if (
            start_time is not None
            and end_time is not None
            and start_time > end_time
        ):
            raise LogServiceError("开始时间不能晚于结束时间")
        if not self._export_lock.acquire(blocking=False):
            raise LogServiceError("已有导出任务在进行中，请稍后再试")
        try:
            redactor = _LogRedactor(self.config) if redact else None
            out_dir = Path(get_astrbot_temp_path())
            out_dir.mkdir(parents=True, exist_ok=True)
            suffix = "raw-" if not redact else ""
            out_path = (
                out_dir / f"ldmbot-logs-{suffix}{time.strftime('%Y%m%d-%H%M%S')}.txt"
            )
            with out_path.open("w", encoding="utf-8", newline="\n") as out:
                for line in self._iter_export_lines(start_time, end_time):
                    if redactor is not None:
                        line = redactor.text(line)
                    out.write(line + "\n")
            return out_path
        except LogServiceError:
            raise
        except Exception as exc:
            logger.error(f"导出日志失败: {exc}")
            raise LogServiceError(f"导出日志失败: {exc}") from exc
        finally:
            self._export_lock.release()

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
