import random
import re
import time
import traceback
from collections.abc import AsyncGenerator

from astrbot.core import file_token_service, html_renderer, logger
from astrbot.core.message.components import At, Image, Json, Node, Plain, Record, Reply
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.pipeline.content_safety_check.stage import ContentSafetyCheckStage
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.message_type import MessageType
from astrbot.core.star.session_llm_manager import SessionServiceManager
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from astrbot.core.utils.segmented_reply import (
    SegmentedReplySplitter,
    resolve_segmented_reply_config,
)

from ..context import PipelineContext
from ..stage import Stage, register_stage, registered_stages


@register_stage
class ResultDecorateStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        self.ctx = ctx
        self.reply_prefix = ctx.astrbot_config["platform_settings"]["reply_prefix"]
        self.reply_with_mention = ctx.astrbot_config["platform_settings"][
            "reply_with_mention"
        ]
        self.reply_with_quote = ctx.astrbot_config["platform_settings"][
            "reply_with_quote"
        ]
        # 走 resolve，保证简易/进阶/专业模式可见项与残留隔离一致
        seg_cfg = resolve_segmented_reply_config(
            ctx.astrbot_config["platform_settings"].get("segmented_reply", {}),
        )
        self.disable_quote_in_private = bool(
            seg_cfg.get("disable_quote_in_private", True)
        )
        self.t2i_word_threshold = ctx.astrbot_config["t2i_word_threshold"]
        try:
            self.t2i_word_threshold = int(self.t2i_word_threshold)
            self.t2i_word_threshold = max(self.t2i_word_threshold, 50)
        except BaseException:
            self.t2i_word_threshold = 150
        self.t2i_strategy = ctx.astrbot_config["t2i_strategy"]
        self.t2i_use_network = self.t2i_strategy == "remote"
        self.t2i_active_template = ctx.astrbot_config["t2i_active_template"]

        self.forward_threshold = ctx.astrbot_config["platform_settings"][
            "forward_threshold"
        ]

        trigger_probability = ctx.astrbot_config["provider_tts_settings"].get(
            "trigger_probability",
            1,
        )
        try:
            self.tts_trigger_probability = max(
                0.0,
                min(float(trigger_probability), 1.0),
            )
        except (TypeError, ValueError):
            self.tts_trigger_probability = 1.0

        # 分段回复（按简易/进阶/专业模式解析）
        seg_cfg = resolve_segmented_reply_config(
            ctx.astrbot_config["platform_settings"].get("segmented_reply", {}),
        )
        self.enable_segmented_reply = bool(seg_cfg.get("enable", False))
        self.only_llm_result = bool(seg_cfg.get("only_llm_result", True))
        self.segment_splitter = SegmentedReplySplitter(
            split_mode=str(seg_cfg.get("split_mode", "chars") or "chars"),
            split_chars=list(seg_cfg.get("split_words") or []),
            regex=str(seg_cfg.get("regex") or r"[。？！?!…\n]+"),
            enable_smart_split=bool(seg_cfg.get("enable_smart_split", True)),
            balanced_split=bool(seg_cfg.get("balanced_split", True)),
            max_segments=int(seg_cfg.get("max_segments", 7) or 0),
            min_segment_length=int(seg_cfg.get("min_segment_length", 10) or 0),
            balanced_ratio_min=float(seg_cfg.get("balanced_ratio_min", 0.4) or 0.4),
            balanced_ratio_max=float(seg_cfg.get("balanced_ratio_max", 0.9) or 0.9),
            no_split_around=list(seg_cfg.get("no_split_around") or []),
            content_cleanup_rule=str(seg_cfg.get("content_cleanup_rule") or ""),
            clean_before_items=list(seg_cfg.get("clean_before_items") or []),
            clean_after_items=list(seg_cfg.get("clean_after_items") or []),
            trim_edge_blank_lines=bool(seg_cfg.get("trim_edge_blank_lines", True)),
            max_length_to_disable=int(seg_cfg.get("max_length_to_disable", 0) or 0),
            min_length_to_split=int(seg_cfg.get("min_length_to_split", 0) or 0),
        )

        # exception
        self.content_safe_check_reply = ctx.astrbot_config["content_safety"][
            "also_use_in_response"
        ]
        self.content_safe_check_stage = None
        if self.content_safe_check_reply:
            for stage_cls in registered_stages:
                if stage_cls.__name__ == "ContentSafetyCheckStage":
                    self.content_safe_check_stage = stage_cls()
                    await self.content_safe_check_stage.initialize(ctx)

        provider_cfg = ctx.astrbot_config.get("provider_settings", {})
        self.show_reasoning = provider_cfg.get("display_reasoning_text", False)

    async def process(
        self,
        event: AstrMessageEvent,
    ) -> None | AsyncGenerator[None, None]:
        result = event.get_result()
        if result is None or not result.chain:
            return

        if result.result_content_type == ResultContentType.STREAMING_RESULT:
            return

        is_stream = result.result_content_type == ResultContentType.STREAMING_FINISH

        # 回复时检查内容安全
        if (
            self.content_safe_check_reply
            and self.content_safe_check_stage
            and result.is_llm_result()
            and not is_stream  # 流式输出不检查内容安全
        ):
            text = ""
            for comp in result.chain:
                if isinstance(comp, Plain):
                    text += comp.text

            if isinstance(self.content_safe_check_stage, ContentSafetyCheckStage):
                async for _ in self.content_safe_check_stage.process(
                    event,
                    check_text=text,
                ):
                    yield

        # 发送消息前事件钩子
        handlers = star_handlers_registry.get_handlers_by_event_type(
            EventType.OnDecoratingResultEvent,
            plugins_name=event.plugins_name,
        )
        for handler in handlers:
            try:
                logger.debug(
                    f"hook(on_decorating_result) -> {star_map[handler.handler_module_path].name} - {handler.handler_name}",
                )
                if is_stream:
                    logger.warning(
                        "启用流式输出时，依赖发送消息前事件钩子的插件可能无法正常工作",
                    )
                await handler.handler(event)

                if (result := event.get_result()) is None or not result.chain:
                    logger.debug(
                        f"hook(on_decorating_result) -> {star_map[handler.handler_module_path].name} - {handler.handler_name} 将消息结果清空。",
                    )
            except BaseException:
                logger.error(traceback.format_exc())

            if event.is_stopped():
                logger.info(
                    f"{star_map[handler.handler_module_path].name} - {handler.handler_name} 终止了事件传播。",
                )
                return

        # 流式输出不执行下面的逻辑
        if is_stream:
            return

        # 需要再获取一次。插件可能直接对 chain 进行了替换。
        result = event.get_result()
        if result is None:
            return

        if len(result.chain) > 0:
            # 回复前缀
            if self.reply_prefix:
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        comp.text = self.reply_prefix + comp.text
                        break

            # 分段回复（智能断句 / 均分 / 最大段数）
            if self.enable_segmented_reply and event.get_platform_name() not in [
                "qq_official_webhook",
                "weixin_official_account",
                "dingtalk",
            ]:
                if (
                    self.only_llm_result and result.is_model_result()
                ) or not self.only_llm_result:
                    try:
                        result.chain = self.segment_splitter.split_chain(result.chain)
                    except Exception:
                        logger.error(
                            f"智能分段失败，保留原文: {traceback.format_exc()}",
                        )

            # TTS
            tts_provider = self.ctx.plugin_manager.context.get_using_tts_provider(
                event.unified_msg_origin,
            )

            should_tts = (
                bool(self.ctx.astrbot_config["provider_tts_settings"]["enable"])
                and result.is_llm_result()
                and await SessionServiceManager.should_process_tts_request(event)
                and random.random() <= self.tts_trigger_probability
                and tts_provider
            )
            if should_tts and not tts_provider:
                logger.warning(
                    f"会话 {event.unified_msg_origin} 未配置文本转语音模型。",
                )

            if (
                not should_tts
                and self.show_reasoning
                and event.get_extra("_llm_reasoning_content")
            ):
                # inject reasoning content to chain
                reasoning_content = str(event.get_extra("_llm_reasoning_content"))
                if event.get_platform_name() == "lark":
                    result.chain.insert(
                        0,
                        Json(
                            data={
                                "type": "lark_collapsible_panel_reasoning",
                                "title": "💭 Thinking",
                                "expanded": False,
                                "content": reasoning_content,
                            },
                        ),
                    )
                else:
                    result.chain.insert(
                        0, Plain(f"🤔 思考: {reasoning_content}\n\n────\n")
                    )

            if should_tts and tts_provider:
                new_chain = []
                for comp in result.chain:
                    if isinstance(comp, Plain) and len(comp.text) > 1:
                        try:
                            logger.info(f"TTS 请求: {comp.text}")
                            audio_path = await tts_provider.get_audio(comp.text)
                            logger.info(f"TTS 结果: {audio_path}")
                            if not audio_path:
                                logger.error(
                                    f"由于 TTS 音频文件未找到，消息段转语音失败: {comp.text}",
                                )
                                new_chain.append(comp)
                                continue

                            event.track_temporary_local_file(audio_path)

                            use_file_service = self.ctx.astrbot_config[
                                "provider_tts_settings"
                            ]["use_file_service"]
                            callback_api_base = self.ctx.astrbot_config[
                                "callback_api_base"
                            ]
                            dual_output = self.ctx.astrbot_config[
                                "provider_tts_settings"
                            ]["dual_output"]

                            url = None
                            if use_file_service and callback_api_base:
                                token = await file_token_service.register_file(
                                    audio_path,
                                )
                                url = f"{callback_api_base}/api/file/{token}"
                                logger.debug(f"已注册：{url}")

                            new_chain.append(
                                Record(
                                    file=url or audio_path,
                                    url=url or audio_path,
                                    text=comp.text,
                                ),
                            )
                            if dual_output:
                                new_chain.append(comp)
                        except Exception:
                            logger.error(traceback.format_exc())
                            logger.error("TTS 失败，使用文本发送。")
                            new_chain.append(comp)
                    else:
                        new_chain.append(comp)
                result.chain = new_chain

            # 文本转图片
            elif (
                result.use_t2i_ is None and self.ctx.astrbot_config["t2i"]
            ) or result.use_t2i_:
                parts = []
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        parts.append("\n\n" + comp.text)
                    else:
                        break
                plain_str = "".join(parts)
                if plain_str and len(plain_str) > self.t2i_word_threshold:
                    render_start = time.time()
                    try:
                        url = await html_renderer.render_t2i(
                            plain_str,
                            return_url=True,
                            use_network=self.t2i_use_network,
                            template_name=self.t2i_active_template,
                        )
                    except BaseException:
                        logger.error("文本转图片失败，使用文本发送。")
                        return
                    if time.time() - render_start > 3:
                        logger.warning(
                            "文本转图片耗时超过了 3 秒，如果觉得很慢可以在 WebUI 中关闭文本转图片模式。",
                        )
                    if url:
                        if url.startswith("http"):
                            result.chain = [Image.fromURL(url)]
                        elif (
                            self.ctx.astrbot_config["t2i_use_file_service"]
                            and self.ctx.astrbot_config["callback_api_base"]
                        ):
                            token = await file_token_service.register_file(url)
                            url = f"{self.ctx.astrbot_config['callback_api_base']}/api/file/{token}"
                            logger.debug(f"已注册：{url}")
                            result.chain = [Image.fromURL(url)]
                        else:
                            result.chain = [Image.fromFileSystem(url)]

            # 触发转发消息
            if event.get_platform_name() == "aiocqhttp":
                word_cnt = 0
                for comp in result.chain:
                    if isinstance(comp, Plain):
                        word_cnt += len(comp.text)
                if word_cnt > self.forward_threshold:
                    node = Node(
                        uin=event.get_self_id(),
                        name="ldm",
                        content=[*result.chain],
                    )
                    result.chain = [node]

            # at 回复 / 引用回复仅适用于纯文本或图文消息
            can_decorate = all(
                isinstance(item, (Plain, Image)) for item in result.chain
            )
            if can_decorate:
                # at 回复
                if (
                    self.reply_with_mention
                    and event.get_message_type() != MessageType.FRIEND_MESSAGE
                ):
                    result.chain.insert(
                        0,
                        At(qq=event.get_sender_id(), name=event.get_sender_name()),
                    )
                    if len(result.chain) > 1 and isinstance(result.chain[1], Plain):
                        result.chain[1].text = "\n" + result.chain[1].text

                # 引用回复
                if self.reply_with_quote:
                    # 分段回复：私聊始终不引用
                    if not (
                        self.disable_quote_in_private
                        and event.get_message_type() == MessageType.FRIEND_MESSAGE
                    ):
                        result.chain.insert(0, Reply(id=event.message_obj.message_id))
