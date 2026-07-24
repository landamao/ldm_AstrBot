import asyncio
import datetime
import hashlib
import random
import re
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from pathlib import Path

from astrbot import logger
from astrbot.api import star
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import (
    At,
    AtAll,
    Face,
    File,
    Forward,
    Image,
    Plain,
    Record,
    Reply,
    Video,
)
from astrbot.api.platform import MessageType
from astrbot.api.provider import Provider, ProviderRequest
from astrbot.core.agent.message import TextPart
from astrbot.core.astrbot_config_mgr import AstrBotConfigManager
from astrbot.core.utils.media_utils import file_uri_to_path

"""
Group chat context awareness.
"""

GROUP_HISTORY_HEADER = (
    "<system_reminder>"
    "You are in a group chat. "
    "Belows are group chat context after your last reply:\n"
    "--- BEGIN CONTEXT---\n"
)
GROUP_HISTORY_FOOTER = "\n--- END CONTEXT ---\n</system_reminder>"
DEFAULT_GROUP_MESSAGE_MAX_CNT = 300
# 黑白名单通配符，与插件 Tools.py 解析黑白名单一致
_ACCESS_WILDCARDS = ("*", "all")
# 延迟图片转述占位： [Image:__LAZY__:<token>]
_LAZY_IMAGE_RE = re.compile(r"\[Image:__LAZY__:([0-9a-fA-F]+)\]")
# MD5 转述缓存上限（按内容复用）
_CAPTION_CACHE_MAX = 512
DEFAULT_IMAGE_CAPTION_CONCURRENCY = 2


class GroupChatContext:
    def __init__(self, acm: AstrBotConfigManager, context: star.Context) -> None:
        self.acm = acm
        self.context = context
        self._locks: dict[str, asyncio.Lock] = {}
        self.raw_records: dict[str, deque[str]] = defaultdict(deque)
        self._record_ids: dict[str, deque[str]] = defaultdict(deque)
        # 各会话最近一次「标记待转述图片」的时间戳（monotonic 秒）
        self._image_caption_last_at: dict[str, float] = {}
        # token -> 图片 URL/路径（记录阶段写入，唤醒 LLM 时解析）
        self._pending_images: dict[str, str] = {}
        # md5 -> 转述文本（跨会话复用相同图片）
        self._caption_cache: OrderedDict[str, str] = OrderedDict()
        # md5 -> 进行中的转述 Future，避免同图并发重复请求
        self._caption_inflight: dict[str, asyncio.Future] = {}
        # 保护 cache/inflight 注册的竞态（check-then-set）
        self._caption_cache_lock = asyncio.Lock()
        # 全局并发信号量（按配置上限创建）
        self._caption_sem: asyncio.Semaphore | None = None
        self._caption_sem_limit: int = 0

    def _get_lock(self, umo: str) -> asyncio.Lock:
        lock = self._locks.get(umo)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[umo] = lock
        return lock

    def _get_caption_semaphore(self, concurrency: int) -> asyncio.Semaphore:
        limit = max(1, int(concurrency or DEFAULT_IMAGE_CAPTION_CONCURRENCY))
        if self._caption_sem is None or self._caption_sem_limit != limit:
            self._caption_sem = asyncio.Semaphore(limit)
            self._caption_sem_limit = limit
        return self._caption_sem

    def cfg(self, event: AstrMessageEvent):
        cfg = self.context.get_config(umo=event.unified_msg_origin)
        group_context_cfg = cfg["provider_ltm_settings"]
        image_caption_prompt = cfg["provider_settings"]["image_caption_prompt"]
        image_caption_provider_id = group_context_cfg.get("image_caption_provider_id")
        image_caption = group_context_cfg["image_caption"] and bool(
            image_caption_provider_id
        )
        image_caption_group_list = group_context_cfg.get("image_caption_group_list") or []
        image_caption_min_interval = _non_negative_float(
            group_context_cfg.get("image_caption_min_interval", 0),
            0.0,
        )
        # 延迟转述：默认开启。关闭则恢复「收到消息立刻转述」旧行为。
        image_caption_lazy = bool(group_context_cfg.get("image_caption_lazy", True))
        image_caption_concurrency = _positive_int(
            group_context_cfg.get(
                "image_caption_concurrency",
                DEFAULT_IMAGE_CAPTION_CONCURRENCY,
            ),
            DEFAULT_IMAGE_CAPTION_CONCURRENCY,
        )
        active_reply = group_context_cfg["active_reply"]
        enable_active_reply = active_reply.get("enable", False)
        ar_method = active_reply["method"]
        ar_possibility = active_reply["possibility_reply"]
        ar_prompt = active_reply.get("prompt", "")
        ar_whitelist = active_reply.get("whitelist", [])
        return {
            "group_message_max_cnt": _positive_int(
                group_context_cfg.get(
                    "group_message_max_cnt",
                    DEFAULT_GROUP_MESSAGE_MAX_CNT,
                ),
                DEFAULT_GROUP_MESSAGE_MAX_CNT,
            ),
            "image_caption": image_caption,
            "image_caption_prompt": image_caption_prompt,
            "image_caption_provider_id": image_caption_provider_id,
            "image_caption_group_list": image_caption_group_list,
            "image_caption_min_interval": image_caption_min_interval,
            "image_caption_lazy": image_caption_lazy,
            "image_caption_concurrency": image_caption_concurrency,
            "enable_active_reply": enable_active_reply,
            "ar_method": ar_method,
            "ar_possibility": ar_possibility,
            "ar_prompt": ar_prompt,
            "ar_whitelist": ar_whitelist,
        }

    def _should_caption_image(self, event: AstrMessageEvent, cfg: dict) -> bool:
        """判断当前消息是否应进行自动理解图片（群黑白名单 + 最小间隔）。

        通过检查后立刻占用时间戳，避免并发消息都在请求 VLM 前通过间隔检查。
        延迟模式下：占用表示「标记为待转述」，真正请求 VLM 推迟到唤醒 LLM 时。
        """
        if not cfg["image_caption"]:
            return False

        umo = event.unified_msg_origin
        group_id = str(event.get_group_id() or "").strip()
        group_list = cfg.get("image_caption_group_list") or []
        # 列表为空：不限制群；非空：使用与插件一致的黑白名单规则
        if group_list:
            黑白名单 = 解析黑白名单(group_list)
            # 同时允许群号、会话 ID 命中（兼容不同填写习惯）
            允许 = 检测黑白名单(group_id, 黑白名单) or 检测黑白名单(umo, 黑白名单)
            if not 允许:
                logger.info(
                    f"群聊上下文:自动理解图片:群黑白名单拦截 | "
                    f"会话={umo} | 群={group_id or '-'} | 已跳过"
                )
                return False

        min_interval = float(cfg.get("image_caption_min_interval") or 0)
        now = time.monotonic()
        if min_interval > 0:
            last_at = self._image_caption_last_at.get(umo)
            if last_at is not None and (now - last_at) < min_interval:
                剩余 = max(0.0, min_interval - (now - last_at))
                logger.info(
                    f"群聊上下文:自动理解图片:间隔限制 | "
                    f"会话={umo} | 群={group_id or '-'} | "
                    f"最小间隔={min_interval:g}秒 | 剩余约={剩余:.2f}秒 | 已跳过"
                )
                return False
            # 请求 VLM / 标记待转述前立刻占用，防止并发刷图全部放行
            self._image_caption_last_at[umo] = now
            return True

        # 无间隔限制时也记录最近一次，便于后续改配置后立即生效
        self._image_caption_last_at[umo] = now
        return True

    def _cache_get(self, md5: str) -> str | None:
        caption = self._caption_cache.get(md5)
        if caption is None:
            return None
        # LRU：命中后移到末尾
        self._caption_cache.move_to_end(md5)
        return caption

    def _cache_put(self, md5: str, caption: str) -> None:
        if not md5 or not caption:
            return
        self._caption_cache[md5] = caption
        self._caption_cache.move_to_end(md5)
        while len(self._caption_cache) > _CAPTION_CACHE_MAX:
            self._caption_cache.popitem(last=False)

    async def _calc_image_md5(self, image_url: str) -> str | None:
        """下载/读取图片字节并计算 MD5；失败返回 None。"""
        try:
            # 已是本地路径时直接读，避免再走 MediaResolver 产生新临时文件
            path = image_url
            if path.startswith("file://"):
                path = file_uri_to_path(path)
            if not (path and Path(path).is_file()):
                path = await Image(file=image_url).convert_to_file_path()
            # 分块读，避免超大图占满内存
            h = hashlib.md5()
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.warning(f"群聊上下文:自动理解图片:计算MD5失败 | {e}")
            return None

    async def get_image_caption(
        self,
        image_url: str,
        image_caption_provider_id: str,
        image_caption_prompt: str,
    ) -> str:
        if not image_caption_provider_id:
            provider = self.context.get_using_provider()
        else:
            provider = self.context.get_provider_by_id(image_caption_provider_id)
            if not provider:
                raise Exception(f"没有找到 ID 为 {image_caption_provider_id} 的提供商")
        if not isinstance(provider, Provider):
            raise Exception(f"提供商类型错误({type(provider)})，无法获取图片描述")
        response = await provider.text_chat(
            prompt=image_caption_prompt,
            session_id=uuid.uuid4().hex,
            image_urls=[image_url],
            persist=False,
        )
        return response.completion_text

    async def _caption_with_md5_cache(
        self,
        image_url: str,
        cfg: dict,
        *,
        concurrency: int,
        统计: dict | None = None,
        预计算md5: str | None = None,
        记入md5: bool = True,
    ) -> str:
        """按 MD5 复用转述；相同 MD5 并发时只请求一次 VLM。

        统计（可选，就地累加）字段：
        - 缓存命中：已有缓存，直接复用
        - 在途复用：等待同 MD5 进行中的请求
        - 实际请求：真正调用视觉模型的次数
        - md5列表：本批成功算出的 md5（含重复）
        预计算md5：上游已算好时传入，避免重复读盘。
        记入md5：在途失败递归重试时传 False，避免同一张图双计 md5。
        """
        md5 = 预计算md5 if 预计算md5 is not None else await self._calc_image_md5(image_url)
        fut: asyncio.Future | None = None
        if md5:
            if 统计 is not None and 记入md5:
                统计.setdefault("md5列表", []).append(md5)
            async with self._caption_cache_lock:
                cached = self._cache_get(md5)
                if cached is not None:
                    if 统计 is not None:
                        统计["缓存命中"] = int(统计.get("缓存命中", 0)) + 1
                    logger.info(
                        f"群聊上下文:自动理解图片:MD5复用 | md5={md5[:12]}… | 跳过VLM"
                    )
                    return cached

                inflight = self._caption_inflight.get(md5)
                if inflight is not None and not inflight.done():
                    wait_fut = inflight
                else:
                    wait_fut = None
                    loop = asyncio.get_running_loop()
                    fut = loop.create_future()
                    self._caption_inflight[md5] = fut

            if wait_fut is not None:
                try:
                    if 统计 is not None:
                        统计["在途复用"] = int(统计.get("在途复用", 0)) + 1
                    return await asyncio.shield(wait_fut)
                except Exception:
                    # 在途失败则本请求自行再试；md5 已记过，递归不再双计
                    return await self._caption_with_md5_cache(
                        image_url,
                        cfg,
                        concurrency=concurrency,
                        统计=统计,
                        预计算md5=md5,
                        记入md5=False,
                    )
        else:
            md5 = ""

        sem = self._get_caption_semaphore(concurrency)
        try:
            async with sem:
                if 统计 is not None:
                    统计["实际请求"] = int(统计.get("实际请求", 0)) + 1
                caption = await self.get_image_caption(
                    image_url,
                    cfg["image_caption_provider_id"],
                    cfg["image_caption_prompt"],
                )
            if md5 and caption:
                async with self._caption_cache_lock:
                    self._cache_put(md5, caption)
            if fut is not None and not fut.done():
                fut.set_result(caption)
            return caption
        except Exception as e:
            if fut is not None and not fut.done():
                fut.set_exception(e)
            raise
        finally:
            if md5 and fut is not None:
                async with self._caption_cache_lock:
                    if self._caption_inflight.get(md5) is fut:
                        self._caption_inflight.pop(md5, None)

    @staticmethod
    def _format_caption_stats(统计: dict, 待转述: int) -> str:
        """把转述统计整理成 info 日志片段。"""
        md5列表: list[str] = list(统计.get("md5列表") or [])
        实际请求 = int(统计.get("实际请求", 0))
        不同md5 = len(set(md5列表)) if md5列表 else 0
        return (
            f"转述完成={待转述} | "
            f"不同MD5={不同md5} | "
            f"实际请求VLM={实际请求}次"
        )

    async def _resolve_lazy_captions(
        self,
        records: list[str],
        cfg: dict,
        event: AstrMessageEvent,
    ) -> list[str]:
        """唤醒 LLM 时：把待注入上下文里的延迟转述占位替换为真实描述。"""
        if not records or not cfg.get("image_caption"):
            return records

        # 收集本批待解析 token（保持出现顺序，去重）
        tokens: list[str] = []
        seen: set[str] = set()
        for text in records:
            for token in _LAZY_IMAGE_RE.findall(text):
                if token not in seen:
                    seen.add(token)
                    tokens.append(token)

        if not tokens:
            return records

        concurrency = int(
            cfg.get("image_caption_concurrency") or DEFAULT_IMAGE_CAPTION_CONCURRENCY
        )
        统计: dict = {
            "缓存命中": 0,
            "在途复用": 0,
            "实际请求": 0,
            "md5列表": [],
        }

        # 1) 先整批算 MD5（不调 VLM），用于请求前预统计
        token_urls: dict[str, str] = {}
        token_md5: dict[str, str | None] = {}
        md5_jobs: list[tuple[str, str]] = []
        for token in tokens:
            url = self._pending_images.get(token)
            if not url:
                token_urls[token] = ""
                token_md5[token] = None
                continue
            token_urls[token] = url
            md5_jobs.append((token, url))

        if md5_jobs:
            md5_results = await asyncio.gather(
                *[self._calc_image_md5(url) for _, url in md5_jobs]
            )
            for (token, _), md5 in zip(md5_jobs, md5_results):
                token_md5[token] = md5
                if md5:
                    统计.setdefault("md5列表", []).append(md5)

        md5列表 = list(统计.get("md5列表") or [])
        不同md5 = len(set(md5列表)) if md5列表 else 0
        # 算不出 MD5 的图无法复用，每张各自算一次预计请求
        无md5张数 = sum(1 for t in tokens if token_urls.get(t) and not token_md5.get(t))
        实际需请求 = 0
        async with self._caption_cache_lock:
            for md5 in set(md5列表):
                if self._cache_get(md5) is None:
                    实际需请求 += 1
        实际需请求 += 无md5张数

        logger.info(
            f"群聊上下文:自动理解图片:延迟转述 | "
            f"会话={event.unified_msg_origin} | "
            f"待转述={len(tokens)} | "
            f"不同MD5={不同md5} | "
            f"实际需请求VLM={实际需请求}次 | "
            f"并发={max(1, concurrency)}"
        )

        async def _one(token: str) -> tuple[str, str]:
            url = token_urls.get(token) or ""
            if not url:
                return token, "[Image]"
            try:
                caption = await self._caption_with_md5_cache(
                    url,
                    cfg,
                    concurrency=concurrency,
                    统计=统计,
                    预计算md5=token_md5.get(token),
                    记入md5=False,  # md5 已在预统计阶段记入
                )
                if caption:
                    return token, f"[Image: {caption}]"
            except Exception as e:
                logger.error(f"群聊上下文:自动理解图片:延迟转述失败 | token={token} | {e}")
            return token, "[Image]"

        results = await asyncio.gather(*[_one(t) for t in tokens])
        replace_map = {token: text for token, text in results}

        # 清理已解析 pending（成功或失败都不再保留 URL）
        for token in tokens:
            self._pending_images.pop(token, None)

        logger.info(
            f"群聊上下文:自动理解图片:延迟转述完成 | "
            f"会话={event.unified_msg_origin} | "
            f"{self._format_caption_stats(统计, len(tokens))}"
        )

        def _sub(match: re.Match) -> str:
            token = match.group(1)
            return replace_map.get(token, "[Image]")

        return [_LAZY_IMAGE_RE.sub(_sub, text) for text in records]

    async def need_active_reply(self, event: AstrMessageEvent) -> bool:
        cfg = self.cfg(event)
        if not cfg["enable_active_reply"]:
            return False
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return False
        if event.is_at_or_wake_command:
            return False
        if cfg["ar_whitelist"] and (
            event.unified_msg_origin not in cfg["ar_whitelist"]
            and (
                event.get_group_id() and event.get_group_id() not in cfg["ar_whitelist"]
            )
        ):
            return False
        match cfg["ar_method"]:
            case "possibility_reply":
                return random.random() < cfg["ar_possibility"]
        return False

    async def remove_session(self, event: AstrMessageEvent) -> int:
        umo = event.unified_msg_origin
        lock = self._get_lock(umo)
        async with lock:
            records = self.raw_records.get(umo, deque())
            # 清理本会话尚未解析的延迟转述 token
            for text in records:
                for token in _LAZY_IMAGE_RE.findall(text):
                    self._pending_images.pop(token, None)
            cnt = len(records)
            self.raw_records.pop(umo, None)
            self._record_ids.pop(umo, None)
        self._locks.pop(umo, None)
        self._image_caption_last_at.pop(umo, None)
        return cnt

    async def handle_message(self, event: AstrMessageEvent) -> None:
        if event.get_message_type() != MessageType.GROUP_MESSAGE:
            return

        umo = event.unified_msg_origin
        cfg = self.cfg(event)
        final_message = await self._format_message(event, cfg)

        async with self._get_lock(umo):
            records = self.raw_records[umo]
            record_ids = self._record_ids[umo]
            record_id = uuid.uuid4().hex
            records.append(final_message)
            record_ids.append(record_id)
            # 超出上限时同步清理被挤掉记录上的 pending 图片
            while len(records) > cfg["group_message_max_cnt"]:
                dropped = records.popleft()
                if record_ids:
                    record_ids.popleft()
                for token in _LAZY_IMAGE_RE.findall(dropped):
                    self._pending_images.pop(token, None)
            event.set_extra("_group_context_record_id", record_id)
            event.set_extra("_group_context_raw_idx", len(records) - 1)

        logger.debug(f"group_chat_context | {umo} | {final_message}")

    async def on_req_llm(self, event: AstrMessageEvent, req: ProviderRequest) -> None:
        umo = event.unified_msg_origin
        record_id = event.get_extra("_group_context_record_id", None)
        prompt_idx = event.get_extra("_group_context_raw_idx", -1)
        if not isinstance(record_id, str) and (
            not isinstance(prompt_idx, int) or prompt_idx < 0
        ):
            return

        cfg = self.cfg(event)

        async with self._get_lock(umo):
            records = self.raw_records.get(umo)
            if not records:
                return

            raw_list = list(records)
            id_list = list(self._record_ids.get(umo, deque()))
            if isinstance(record_id, str) and record_id in id_list:
                prompt_idx = id_list.index(record_id)

            if prompt_idx >= len(raw_list):
                return

            records_to_inject = raw_list[:prompt_idx]
            # 当前触发消息不会注入群聊上下文（它走主 prompt），清理其上的延迟占位，避免 pending 泄漏
            current_record = raw_list[prompt_idx] if 0 <= prompt_idx < len(raw_list) else ""
            remaining = raw_list[prompt_idx + 1 :]
            remaining_ids = id_list[prompt_idx + 1 :] if id_list else []
            records.clear()
            records.extend(remaining)
            if id_list:
                record_ids = self._record_ids[umo]
                record_ids.clear()
                record_ids.extend(remaining_ids)

        if current_record:
            for token in _LAZY_IMAGE_RE.findall(current_record):
                self._pending_images.pop(token, None)

        if not records_to_inject:
            return

        # 真正唤醒 LLM 时，再对即将注入的上下文做图片转述
        if cfg.get("image_caption") and cfg.get("image_caption_lazy", True):
            records_to_inject = await self._resolve_lazy_captions(
                records_to_inject,
                cfg,
                event,
            )

        req.extra_user_content_parts.append(
            TextPart(text=_format_group_history_block(records_to_inject))
        )

    async def _format_message(self, event: AstrMessageEvent, cfg: dict) -> str:
        datetime_str = datetime.datetime.now().strftime("%H:%M:%S")
        parts = [f"[{event.message_obj.sender.nickname}/{datetime_str}]: "]
        # 是否允许自动理解图片：通过后会立刻占用间隔时间戳（请求 VLM / 标记待转述之前）
        do_caption = False
        caption_claimed = False
        lazy = bool(cfg.get("image_caption_lazy", True))

        for comp in event.get_messages():
            if isinstance(comp, Plain):
                parts.append(f" {comp.text}")
            elif isinstance(comp, Image):
                # 本条消息内多张图只 claim 一次；并发消息各自 claim，后到的会被间隔挡住
                if not caption_claimed:
                    do_caption = self._should_caption_image(event, cfg)
                    caption_claimed = True
                if do_caption:
                    try:
                        url = comp.url if comp.url else comp.file
                        if not url:
                            raise Exception("图片 URL 为空")
                        if lazy:
                            # 延迟模式：只登记待转述，唤醒 LLM 时再请求 VLM
                            token = uuid.uuid4().hex
                            self._pending_images[token] = url
                            parts.append(f" [Image:__LAZY__:{token}]")
                        else:
                            # 即时模式：收到即转述（旧行为）
                            caption = await self._caption_with_md5_cache(
                                url,
                                cfg,
                                concurrency=int(
                                    cfg.get("image_caption_concurrency")
                                    or DEFAULT_IMAGE_CAPTION_CONCURRENCY
                                ),
                            )
                            parts.append(f" [Image: {caption}]")
                    except Exception as e:
                        logger.error(f"获取图片描述失败: {e}")
                        parts.append(" [Image]")
                else:
                    parts.append(" [Image]")
            elif isinstance(comp, At):
                is_at_self = str(comp.qq) in (
                    event.get_self_id(),
                    "all",
                )
                if is_at_self:
                    parts.insert(1, "⚠️[DIRECTED AT YOU] ")
                parts.append(f" [At: {comp.name}]")
            elif isinstance(comp, Reply):
                if comp.message_str:
                    parts.append(
                        f" [Quote({comp.sender_nickname}: {_truncate_reply_text(comp.message_str)})]"
                    )
                elif comp.chain:
                    chain_desc = _describe_chain(comp.chain)
                    parts.append(f" [Quote({comp.sender_nickname}: {chain_desc})]")
                else:
                    parts.append(" [Quote]")

        return "".join(parts)


_MAX_REPLY_TEXT_LENGTH = 200


def _describe_chain(chain: list) -> str:
    """Summarize message chain content for quoted reply display."""
    desc = []
    for c in chain:
        if isinstance(c, Plain) and getattr(c, "text", None):
            desc.append(c.text)
        elif isinstance(c, Image):
            desc.append("[Image]")
        elif isinstance(c, At):
            name = getattr(c, "name", "") or getattr(c, "qq", "")
            desc.append(f"[At: {name}]")
        elif isinstance(c, Record):
            desc.append("[Voice]")
        elif isinstance(c, Video):
            desc.append("[Video]")
        elif isinstance(c, File):
            desc.append(f"[File: {getattr(c, 'name', '') or ''}]")
        elif isinstance(c, Forward):
            desc.append("[Forward]")
        elif isinstance(c, AtAll):
            desc.append("[At: All]")
        elif isinstance(c, Face):
            desc.append(f"[Sticker: {getattr(c, 'id', '')}]")
        elif isinstance(c, Reply):
            desc.append("[Quote]")
        else:
            desc.append(f"[{c.__class__.__name__}]")
    return "".join(desc) or "[Unknown]"


def _truncate_reply_text(text: str) -> str:
    """Truncate overly long quoted reply text."""
    if len(text) <= _MAX_REPLY_TEXT_LENGTH:
        return text
    return text[:_MAX_REPLY_TEXT_LENGTH] + "..."


def _positive_int(value, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _non_negative_float(value, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def 解析黑白名单(
    原列表: list[str] | set[str],
    通配符=None,
) -> tuple[set[str], set[str]]:
    """
    解析原始访问控制列表，返回标准化的黑名单和白名单。

    规则与 ldmbot-plugins/每日老婆/Tools.py 一致：
    - 以 / 开头：黑名单
    - 否则：白名单
    - all/* 为通配符；命中黑名单通配符时拒绝全部；命中白名单通配符时允许全部（再扣黑名单）

    Returns:
        tuple[set, set]: (黑名单, 白名单)
    """
    if 通配符 is None:
        通配符 = list(_ACCESS_WILDCARDS)
    # 跳过非字符串和空字符串
    原列表 = [i.strip() for i in 原列表 if isinstance(i, str) and i.strip()]
    黑名单: list[str] = []
    白名单: list[str] = []
    if 通配符:
        if isinstance(通配符, (list, tuple)):
            t = 通配符[0]
            tl = list(通配符)
        elif isinstance(通配符, str):
            t = 通配符
            tl = [通配符]
        else:
            raise ValueError("通配符类型错误，应为 list 或 str")
    else:
        t = ""
        tl = []

    for i in 原列表:
        # 黑名单判断（以 / 开头）
        if i.startswith("/"):
            i = i[1:]  # 去掉前缀 /
            if not i:
                continue
            if i in tl:
                return {t}, set()
            黑名单.append(i)
        else:
            白名单.append(i)

    # 规范化白名单
    if any(i in 白名单 for i in tl):
        白名单 = [t]

    白名单 = [i for i in 白名单 if i not in 黑名单]

    return set(黑名单), set(白名单)


def 检测黑白名单(
    值: str,
    黑白名单: tuple[set[str], set[str]],
    通配符=None,
) -> bool:
    """检测值是否通过黑白名单。规则与 Tools.py 检测黑白名单一致。"""
    if 通配符 is None:
        通配符 = list(_ACCESS_WILDCARDS)
    黑名单 = 黑白名单[0]
    白名单 = 黑白名单[1]
    if 通配符:
        if isinstance(通配符, (list, tuple)):
            t = 通配符[0]
        elif isinstance(通配符, str):
            t = 通配符
        else:
            raise ValueError("通配符类型错误，应为 list 或 str")
    else:
        t = ""
    if not (黑名单 or 白名单):
        return False
    if 黑名单:
        if t in 黑名单:
            return False
        if 值 in 黑名单:
            return False
    if 白名单:
        if t in 白名单:
            return True
        if 值 in 白名单:
            return True
    # 规范使用通配符，为空则拒绝
    return False


def _trim_left(
    records: deque[str],
    max_records: int,
    record_ids: deque[str] | None = None,
) -> None:
    while len(records) > max_records:
        records.popleft()
        if record_ids:
            record_ids.popleft()


def _format_group_history_block(records: list[str]) -> str:
    return GROUP_HISTORY_HEADER + "\n".join(records) + GROUP_HISTORY_FOOTER
