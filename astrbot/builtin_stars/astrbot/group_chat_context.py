import asyncio
import datetime
import random
import time
import uuid
from collections import defaultdict, deque

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


class GroupChatContext:
    def __init__(self, acm: AstrBotConfigManager, context: star.Context) -> None:
        self.acm = acm
        self.context = context
        self._locks: dict[str, asyncio.Lock] = {}
        self.raw_records: dict[str, deque[str]] = defaultdict(deque)
        self._record_ids: dict[str, deque[str]] = defaultdict(deque)
        # 各会话最近一次自动理解图片的时间戳（monotonic 秒）
        self._image_caption_last_at: dict[str, float] = {}

    def _get_lock(self, umo: str) -> asyncio.Lock:
        lock = self._locks.get(umo)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[umo] = lock
        return lock

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
            "enable_active_reply": enable_active_reply,
            "ar_method": ar_method,
            "ar_possibility": ar_possibility,
            "ar_prompt": ar_prompt,
            "ar_whitelist": ar_whitelist,
        }

    def _should_caption_image(self, event: AstrMessageEvent, cfg: dict) -> bool:
        """判断当前消息是否应进行自动理解图片（群黑白名单 + 最小间隔）。

        通过检查后立刻占用时间戳，避免并发消息都在请求 VLM 前通过间隔检查。
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
            # 请求 VLM 前立刻占用，防止并发刷图全部放行
            self._image_caption_last_at[umo] = now
            return True

        # 无间隔限制时也记录最近一次，便于后续改配置后立即生效
        self._image_caption_last_at[umo] = now
        return True

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
            cnt = len(self.raw_records.get(umo, deque()))
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
            _trim_left(records, cfg["group_message_max_cnt"], record_ids)
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
            remaining = raw_list[prompt_idx + 1 :]
            remaining_ids = id_list[prompt_idx + 1 :] if id_list else []
            records.clear()
            records.extend(remaining)
            if id_list:
                record_ids = self._record_ids[umo]
                record_ids.clear()
                record_ids.extend(remaining_ids)

        if records_to_inject:
            req.extra_user_content_parts.append(
                TextPart(text=_format_group_history_block(records_to_inject))
            )

    async def _format_message(self, event: AstrMessageEvent, cfg: dict) -> str:
        datetime_str = datetime.datetime.now().strftime("%H:%M:%S")
        parts = [f"[{event.message_obj.sender.nickname}/{datetime_str}]: "]
        # 是否允许自动理解图片：通过后会立刻占用间隔时间戳（请求 VLM 之前）
        do_caption = False
        caption_claimed = False

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
                        caption = await self.get_image_caption(
                            url,
                            cfg["image_caption_provider_id"],
                            cfg["image_caption_prompt"],
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
