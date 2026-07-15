import asyncio
import math
import random
from collections.abc import AsyncGenerator

import astrbot.core.message.components as Comp
from astrbot.core import logger
from astrbot.core.message.components import BaseMessageComponent, ComponentType
from astrbot.core.message.message_event_result import MessageChain, ResultContentType
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.star_handler import EventType
from astrbot.core.utils.path_util import path_Mapping
from astrbot.core.utils.segmented_reply import (
    calculate_segment_delay,
    get_segmented_reply_session_tracker,
    resolve_segmented_reply_config,
)

from ..context import PipelineContext, call_event_hook
from ..stage import Stage, register_stage


@register_stage
class RespondStage(Stage):
    # 组件类型到其非空判断函数的映射
    _component_validators = {
        Comp.Plain: lambda comp: bool(
            comp.text and comp.text.strip(),
        ),  # 纯文本消息需要strip
        Comp.Face: lambda comp: comp.id is not None,  # QQ表情
        Comp.Record: lambda comp: bool(comp.file),  # 语音
        Comp.Video: lambda comp: bool(comp.file),  # 视频
        Comp.At: lambda comp: bool(comp.qq) or bool(comp.name),  # @
        Comp.Image: lambda comp: bool(comp.file),  # 图片
        Comp.Reply: lambda comp: bool(comp.id) and comp.sender_id is not None,  # 回复
        Comp.Poke: lambda comp: comp.target_id() is not None,  # 戳一戳
        Comp.Node: lambda comp: bool(comp.content),  # 转发节点
        Comp.Nodes: lambda comp: bool(comp.nodes),  # 多个转发节点
        Comp.File: lambda comp: bool(comp.file_ or comp.url),
        Comp.Json: lambda comp: bool(comp.data),  # Json 卡片
        Comp.Share: lambda comp: bool(comp.url) or bool(comp.title),
        Comp.Music: lambda comp: (
            (comp.id and comp._type and comp._type != "custom")
            or (comp._type == "custom" and comp.url and comp.audio and comp.title)
        ),  # 音乐分享
        Comp.Forward: lambda comp: bool(comp.id),  # 合并转发
        Comp.Location: lambda comp: bool(
            comp.lat is not None and comp.lon is not None
        ),  # 位置
        Comp.Contact: lambda comp: bool(comp._type and comp.id),  # 推荐好友 or 群
        Comp.Shake: lambda _: True,  # 窗口抖动（戳一戳）
        Comp.Dice: lambda _: True,  # 掷骰子魔法表情
        Comp.RPS: lambda _: True,  # 猜拳魔法表情
        Comp.Unknown: lambda comp: bool(comp.text and comp.text.strip()),
    }

    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.config = ctx.astrbot_config
        self.platform_settings: dict = self.config.get("platform_settings", {})

        self.reply_with_mention = ctx.astrbot_config["platform_settings"][
            "reply_with_mention"
        ]
        self.reply_with_quote = ctx.astrbot_config["platform_settings"][
            "reply_with_quote"
        ]

        # 分段回复发送节奏（按配置模式解析；不可见项已在 resolve 中回落模板）
        seg_cfg = resolve_segmented_reply_config(
            ctx.astrbot_config["platform_settings"].get("segmented_reply", {}),
        )
        self.enable_seg: bool = bool(seg_cfg.get("enable", False))
        self.seg_config_mode = str(seg_cfg.get("config_mode", "simple") or "simple")
        self.seg_send_speed = str(seg_cfg.get("send_speed", "natural") or "natural")
        self.only_llm_result = bool(seg_cfg.get("only_llm_result", True))
        self.interval_method = str(seg_cfg.get("interval_method", "linear") or "linear")
        self.log_base = float(seg_cfg.get("log_base", 2.6) or 2.6)
        self.linear_base = float(seg_cfg.get("linear_base", 0.5) or 0.5)
        self.linear_factor = float(seg_cfg.get("linear_factor", 0.08) or 0.08)
        self.fixed_delay = float(seg_cfg.get("fixed_delay", 1.5) or 1.5)
        self.enable_smart_reply = bool(seg_cfg.get("enable_smart_reply", True))
        self.enable_keep_reply = bool(seg_cfg.get("enable_keep_reply", True))
        self.disable_quote_in_private = bool(
            seg_cfg.get("disable_quote_in_private", True)
        )
        self.interval = [1.5, 3.5]
        if self.enable_seg:
            interval_str = str(seg_cfg.get("interval", "1.5,3.5") or "1.5,3.5")
            interval_str_ls = interval_str.replace(" ", "").split(",")
            try:
                self.interval = [float(t) for t in interval_str_ls]
            except BaseException as e:
                logger.error(f"解析分段回复的间隔时间失败。{e}")
            logger.info(self._format_segmented_reply_rhythm_log())


    def _format_segmented_reply_rhythm_log(self) -> str:
        """按当前配置模式只打印实际生效的节奏字段（避免简易/进阶混入专业残留项）。"""
        mode = str(getattr(self, "seg_config_mode", "simple") or "simple").lower()
        mode_label = {
            "simple": "简易",
            "advanced": "进阶",
            "pro": "专业",
        }.get(mode, mode)
        speed = str(getattr(self, "seg_send_speed", "natural") or "natural").lower()
        speed_label = {
            "natural": "自然",
            "fast": "快速",
            "slow": "慢速",
            "自然": "自然",
            "快速": "快速",
            "慢速": "慢速",
        }.get(speed, speed)
        method = str(self.interval_method or "linear").lower()
        method_label = {
            "linear": "线性",
            "log": "对数",
            "random": "随机",
            "fixed": "固定",
        }.get(method, method)
        on_off = lambda v: "开" if v else "关"

        # 简易/进阶：用户侧只有「发送节奏」；内部映射后的实际延迟只打印生效那一项
        if mode in ("simple", "advanced"):
            if method == "fixed":
                delay_desc = f"固定延迟: {self.fixed_delay}s"
            elif method == "linear":
                delay_desc = f"线性: {self.linear_base}+{self.linear_factor}*字数"
            elif method == "log":
                delay_desc = f"对数底: {self.log_base}"
            elif method == "random":
                delay_desc = f"随机间隔: {self.interval}"
            else:
                delay_desc = f"间隔策略: {method_label}"
            parts = [
                f"模式: {mode_label}",
                f"发送节奏: {speed_label}",
                delay_desc,
                f"智能回复: {on_off(self.enable_smart_reply)}",
                f"保留回复: {on_off(self.enable_keep_reply)}",
                f"私聊不引用: {on_off(self.disable_quote_in_private)}",
            ]
            if mode == "advanced":
                parts.insert(3, f"仅LLM分段: {on_off(self.only_llm_result)}")
            return "分段回复节奏: " + " ".join(parts)

        # 专业：打印专业模式实际使用的间隔字段
        if method == "linear":
            delay_desc = f"线性: {self.linear_base}+{self.linear_factor}*字数"
        elif method == "log":
            delay_desc = f"对数底: {self.log_base}"
        elif method == "random":
            delay_desc = f"随机间隔: {self.interval}"
        elif method == "fixed":
            delay_desc = f"固定延迟: {self.fixed_delay}s"
        else:
            delay_desc = f"间隔: {method_label}"
        return (
            "分段回复节奏: "
            f"模式: {mode_label} "
            f"间隔策略: {method_label} "
            f"{delay_desc} "
            f"智能回复: {on_off(self.enable_smart_reply)} "
            f"保留回复: {on_off(self.enable_keep_reply)} "
            f"私聊不引用: {on_off(self.disable_quote_in_private)}"
        )

    async def _word_cnt(self, text: str) -> int:
        """分段回复 统计字数"""
        if all(ord(c) < 128 for c in text):
            word_count = len(text.split())
        else:
            word_count = len([c for c in text if c.isalnum()])
        return word_count

    async def _calc_comp_interval(self, comp: BaseMessageComponent) -> float:
        """分段回复：基于「即将发送」内容计算等待时间。"""
        text = ""
        if isinstance(comp, Comp.Plain):
            text = comp.text or ""
        return calculate_segment_delay(
            text,
            method=self.interval_method,
            interval=(self.interval[0], self.interval[-1]),
            log_base=self.log_base,
            linear_base=self.linear_base,
            linear_factor=self.linear_factor,
            fixed_delay=self.fixed_delay,
        )

    async def _is_empty_message_chain(self, chain: list[BaseMessageComponent]) -> bool:
        """检查消息链是否为空

        Args:
            chain (list[BaseMessageComponent]): 包含消息对象的列表

        """
        if not chain:
            return True

        for comp in chain:
            comp_type = type(comp)

            # 检查组件类型是否在字典中
            if comp_type in self._component_validators:
                if self._component_validators[comp_type](comp):
                    return False

        # 如果所有组件都为空
        return True

    def is_seg_reply_required(self, event: AstrMessageEvent) -> bool:
        """检查是否需要分段回复"""
        if not self.enable_seg:
            return False

        if (result := event.get_result()) is None:
            return False
        if self.only_llm_result and not result.is_model_result():
            return False

        if event.get_platform_name() in [
            "qq_official_webhook",
            "weixin_official_account",
            "dingtalk",
        ]:
            return False

        return True

    def _extract_comp(
        self,
        raw_chain: list[BaseMessageComponent],
        extract_types: set[ComponentType],
        modify_raw_chain: bool = True,
    ):
        extracted = []
        if modify_raw_chain:
            remaining = []
            for comp in raw_chain:
                if comp.type in extract_types:
                    extracted.append(comp)
                else:
                    remaining.append(comp)
            raw_chain[:] = remaining
        else:
            extracted = [comp for comp in raw_chain if comp.type in extract_types]

        return extracted


    def _record_delivered_llm_plain(self, event: AstrMessageEvent, chain_or_comp) -> None:
        """累计 LLM 回复实际已发送的纯文本，供打断后裁剪历史。"""
        try:
            plain = ""
            if hasattr(chain_or_comp, "get_plain_text"):
                plain = (chain_or_comp.get_plain_text() or "").strip()
            elif isinstance(chain_or_comp, Comp.Plain):
                plain = (chain_or_comp.text or "").strip()
            elif isinstance(chain_or_comp, list):
                plain = "".join(
                    (c.text or "") for c in chain_or_comp if isinstance(c, Comp.Plain)
                ).strip()
            if not plain:
                return
            prev = event.get_extra("_delivered_llm_plain_text", "") or ""
            event.set_extra(
                "_delivered_llm_plain_text",
                f"{prev}\n{plain}" if prev else plain,
            )
        except Exception:
            logger.debug("记录已发送 LLM 文本失败", exc_info=True)

    def _should_stop_sending(self, event: AstrMessageEvent) -> bool:
        """打断回复：发送阶段应立即停止后续分段/流式输出。

        注意：不要用 event.is_stopped()。
        插件里常见写法是先 stop_event() 再 yield result——stop 只表示终止事件传播
        （后续插件/默认 LLM 不再跑），当前这次 yield 仍应由 RespondStage 正常发出。
        真正的打断信号：
        - agent_stop_requested / agent_user_aborted：软打断（新消息）
        - agent_force_stop：/stop 或 Dashboard 强制停止
        """
        if event.get_extra("agent_force_stop"):
            return True
        if event.get_extra("agent_stop_requested"):
            return True
        if event.get_extra("agent_user_aborted"):
            return True
        return False

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        result = event.get_result()
        if result is None:
            return
        if self._should_stop_sending(event):
            logger.info("检测到打断信号，跳过发送阶段。")
            event.clear_result()
            return
        if event.get_extra("_streaming_finished", False):
            # prevent some plugin make result content type to LLM_RESULT after streaming finished, lead to send again
            return
        if result.result_content_type == ResultContentType.STREAMING_FINISH:
            event.set_extra("_streaming_finished", True)
            return
        sent_plain_texts = event.get_extra(
            "_send_message_to_user_current_session_plain_texts",
            [],
        )
        result_plain_text = result.get_plain_text().strip()
        if (
            result_plain_text
            and isinstance(sent_plain_texts, list)
            and result_plain_text in sent_plain_texts
            and all(
                comp.type
                in {
                    ComponentType.Plain,
                    ComponentType.Reply,
                    ComponentType.At,
                }
                for comp in result.chain
            )
        ):
            logger.info(
                "send_message_to_user already delivered the same text in this session, skip respond stage to avoid duplicate reply.",
            )
            return

        logger.info(
            f"Prepare to send - {event.get_sender_name()}/{event.get_sender_id()}: {event._outline_chain(result.chain)}",
        )

        if result.result_content_type == ResultContentType.STREAMING_RESULT:
            if result.async_stream is None:
                logger.warning("async_stream 为空，跳过发送。")
                return
            # 流式结果直接交付平台适配器处理
            realtime_segmenting = (
                self.config.get("provider_settings", {}).get(
                    "unsupported_streaming_strategy",
                    "realtime_segmenting",
                )
                == "realtime_segmenting"
            )
            logger.info(f"应用流式输出({event.get_platform_id()})")
            if self._should_stop_sending(event):
                logger.info("检测到打断信号，跳过流式发送。")
                event.clear_result()
                return
            await event.send_streaming(result.async_stream, realtime_segmenting)
            return
        if len(result.chain) > 0:
            # 检查路径映射
            if mappings := self.platform_settings.get("path_mapping", []):
                for idx, component in enumerate(result.chain):
                    if isinstance(component, Comp.File) and component.file:
                        # 支持 File 消息段的路径映射。
                        component.file = path_Mapping(mappings, component.file)
                        result.chain[idx] = component

            # 检查消息链是否为空
            try:
                if await self._is_empty_message_chain(result.chain):
                    logger.info("消息为空，跳过发送阶段")
                    return
            except Exception as e:
                logger.warning(f"空内容检查异常: {e}")

            # 将 Plain 为空的消息段移除
            result.chain = [
                comp
                for comp in result.chain
                if not (
                    isinstance(comp, Comp.Plain)
                    and (not comp.text or not comp.text.strip())
                )
            ]

            # 发送消息链
            # Record 需要强制单独发送
            need_separately = {ComponentType.Record}
            if self.is_seg_reply_required(event):
                # Preserve At; Reply handling follows enable_keep_reply / enable_smart_reply.
                header_comps = self._extract_comp(
                    result.chain,
                    {ComponentType.Reply, ComponentType.At},
                    modify_raw_chain=True,
                )
                at_headers = [c for c in header_comps if c.type == ComponentType.At]
                reply_headers = [c for c in header_comps if c.type == ComponentType.Reply]
                if not self.enable_keep_reply:
                    reply_headers = []
                if not result.chain or len(result.chain) == 0:
                    # may fix #2670
                    logger.warning(
                        f"实际消息链为空, 跳过发送阶段。header_chain: {header_comps}, actual_chain: {result.chain}",
                    )
                    return

                source_id = str(
                    getattr(getattr(event, "message_obj", None), "message_id", "") or ""
                )
                tracker = get_segmented_reply_session_tracker()
                # 私聊始终不引用：关闭智能回复/保留回复带来的 Reply 注入
                is_private = (
                    event.get_message_type() == MessageType.FRIEND_MESSAGE
                )
                allow_quote = not (self.disable_quote_in_private and is_private)
                use_smart_reply = self.enable_smart_reply and allow_quote
                use_keep_reply = self.enable_keep_reply and allow_quote
                if not allow_quote:
                    reply_headers = []
                first_reply_chain: list[BaseMessageComponent] = list(reply_headers)
                if use_smart_reply and source_id:
                    if tracker.should_add_smart_reply(
                        str(getattr(event, "unified_msg_origin", "") or ""),
                        source_id,
                        platform_name=str(event.get_platform_name() or ""),
                    ):
                        if not any(isinstance(c, Comp.Reply) for c in first_reply_chain):
                            first_reply_chain.insert(0, Comp.Reply(id=source_id))
                            logger.info("智能回复：检测到插话，第一段附加 Reply")
                if use_keep_reply and source_id and not first_reply_chain:
                    # keep-reply: ensure there is a Reply even if platform quote is off
                    first_reply_chain = [Comp.Reply(id=source_id)]
                # last segment Reply set (keep-reply only)
                last_reply_chain: list[BaseMessageComponent] = (
                    list(reply_headers)
                    if reply_headers
                    else (list(first_reply_chain) if use_keep_reply else [])
                )

                comps = list(result.chain)
                for idx, comp in enumerate(comps):
                    if self._should_stop_sending(event):
                        logger.info("分段发送过程中检测到打断信号，停止后续分段。")
                        break
                    # 第一段立即发；后续段按「本段」字数延迟，模拟打字
                    if idx > 0:
                        delay = await self._calc_comp_interval(comp)
                        slept = 0.0
                        while slept < delay:
                            if self._should_stop_sending(event):
                                logger.info("分段等待期间检测到打断信号，停止后续分段。")
                                break
                            step = min(0.1, delay - slept)
                            await asyncio.sleep(step)
                            slept += step
                        else:
                            pass
                        if self._should_stop_sending(event):
                            break
                    try:
                        if comp.type in need_separately:
                            await event.send(result.derive([comp]))
                        else:
                            # Reply 策略（对齐分段插件）：
                            # - 第一段：智能回复命中 / 保留回复时带 Reply + At
                            # - 中间段：不带 Reply
                            # - 最后一段：仅「保留回复」开启时继续带原 Reply
                            if idx == 0:
                                prefix = [*first_reply_chain, *at_headers]
                            elif use_keep_reply and idx == len(comps) - 1:
                                prefix = list(last_reply_chain)
                            else:
                                prefix = []
                            await event.send(result.derive([*prefix, comp]))
                        if result.is_model_result():
                            self._record_delivered_llm_plain(event, comp)
                        logger.info(
                            "分段发送 %s/%s: %s",
                            idx + 1,
                            len(comps),
                            event._outline_chain([comp]),
                        )
                    except Exception as e:
                        logger.error(
                            f"发送消息链失败: chain = {MessageChain([comp])}, error = {e}",
                            exc_info=True,
                        )
                if use_smart_reply and source_id:
                    tracker.mark_bot_reply(
                        str(getattr(event, "unified_msg_origin", "") or ""),
                        source_id,
                    )
            else:
                if all(
                    comp.type in {ComponentType.Reply, ComponentType.At}
                    for comp in result.chain
                ):
                    # may fix #2670
                    logger.warning(
                        f"消息链全为 Reply 和 At 消息段, 跳过发送阶段。chain: {result.chain}",
                    )
                    return
                sep_comps = self._extract_comp(
                    result.chain,
                    need_separately,
                    modify_raw_chain=True,
                )
                for comp in sep_comps:
                    chain = result.derive([comp])
                    try:
                        await event.send(chain)
                    except Exception as e:
                        logger.error(
                            f"发送消息链失败: chain = {chain}, error = {e}",
                            exc_info=True,
                        )
                chain = result.derive(result.chain)
                if result.chain and len(result.chain) > 0:
                    try:
                        await event.send(chain)
                        if result.is_model_result():
                            self._record_delivered_llm_plain(event, chain)
                    except Exception as e:
                        logger.error(
                            f"发送消息链失败: chain = {chain}, error = {e}",
                            exc_info=True,
                        )

        if await call_event_hook(event, EventType.OnAfterMessageSentEvent):
            return

        event.clear_result()
