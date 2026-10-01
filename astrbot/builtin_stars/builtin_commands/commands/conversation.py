import datetime
import json

from sqlalchemy import case, func, select
from sqlmodel import col, desc

from astrbot import logger
from astrbot.api import sp, star
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult
from astrbot.core.agent.context.compressor import (
    LLMSummaryCompressor,
    TruncateByTurnsCompressor,
)
from astrbot.core.agent.context.round_utils import split_into_rounds
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import (
    bind_checkpoint_messages,
    dump_messages_with_checkpoints,
)
from astrbot.core.agent.runners.deerflow.constants import (
    DEERFLOW_PROVIDER_TYPE,
    DEERFLOW_THREAD_ID_KEY,
)
from astrbot.core.db.po import ProviderStat
from astrbot.core.platform.astr_message_event import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.provider.entities import TokenUsage
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词

from .utils.param_utils import 转整数, 转整数或None
from .utils.rst_scene import RstScene
from .utils.session_target import SessionTargetResolver

THIRD_PARTY_AGENT_RUNNER_KEY = {
    "dify": "dify_conversation_id",
    "coze": "coze_conversation_id",
    "dashscope": "dashscope_conversation_id",
    DEERFLOW_PROVIDER_TYPE: DEERFLOW_THREAD_ID_KEY,
}
THIRD_PARTY_AGENT_RUNNER_STR = ", ".join(THIRD_PARTY_AGENT_RUNNER_KEY.keys())


def _format_tokens(n: int | float | None) -> str:
    """token 数量格式化：≥1e6 用 M，≥1e3 用 k，其余原样。"""
    try:
        value = int(n or 0)
    except (TypeError, ValueError):
        value = 0
    abs_value = abs(value)
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        # 避免 999999 显示成 1000.00k
        if abs_value >= 999_995:
            return f"{value / 1_000_000:.2f}M"
        return f"{value / 1_000:.2f}k"
    return str(value)


def _estimate_history_context_tokens(history_json: str) -> int:
    """按本地估算统计对话历史占用（不含 system prompt / 工具 schema）。"""
    try:
        raw = json.loads(history_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0
    if not isinstance(raw, list) or not raw:
        return 0
    try:
        messages = bind_checkpoint_messages(raw)
        return EstimateTokenCounter().count_tokens(messages)
    except Exception:
        logger.debug("估算对话历史 token 失败", exc_info=True)
        return 0


class ConversationCommands(SessionTargetResolver):
    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def _resolve_target_session(
        self, message: AstrMessageEvent, session_id: str | None
    ) -> str | None:
        """完整 ID 或 persona 风格简写定位；跨会话保留管理员校验。

        顺序：先解析（未命中=未找到会话），命中且目标≠当前会话时才校验管理员。
        """
        target = (session_id or "").strip()
        if not target or target == message.unified_msg_origin:
            return message.unified_msg_origin
        resolved: str
        if ":" in target:
            try:
                session = MessageSession.from_str(target)
                if not session.platform_id or not session.session_id:
                    raise ValueError("会话 ID 不能为空")
            except ValueError:
                message.set_result(MessageEventResult().message(
                    "会话 ID 格式错误，可使用群号、QQ号、昵称、群名或完整会话ID。"
                ))
                return None
            resolved = target
        else:
            candidates, aliases = await self._resolve_targets(target)
            if not candidates:
                logger.warning(f"会话定位: 「{target}」未匹配到任何会话")
                message.set_result(MessageEventResult().message(
                    f"未找到会话「{target}」，请输入对方的群号/QQ号/昵称。"
                ))
                return None
            if len(candidates) > 1:
                lines = [f"会话ID「{target}」匹配到多个会话，请输入更精确的ID："]
                lines += [f"- {self._display(umo, aliases)}" for umo in candidates]
                message.set_result(MessageEventResult().message("\n".join(lines)).use_t2i(False))
                return None
            resolved = candidates[0]
        if resolved != message.unified_msg_origin and getattr(message, "role", None) != "admin":
            logger.warning(f"会话定位: 非管理员尝试跨会话操作「{resolved}」")
            message.set_result(
                MessageEventResult().message("跨会话操作需要机器人管理员权限。")
            )
            return None
        return resolved

    async def _get_current_persona_id(self, session_id):
        curr = await self.context.conversation_manager.get_curr_conversation_id(
            session_id,
        )
        if not curr:
            return None
        conv = await self.context.conversation_manager.get_conversation(
            session_id,
            curr,
        )
        if not conv:
            return None
        return conv.persona_id

    async def reset(self, message: AstrMessageEvent) -> None:
        """重置 LLM 会话"""
        umo = message.unified_msg_origin
        cfg = self.context.get_config(umo=message.unified_msg_origin)
        is_unique_session = cfg["platform_settings"]["unique_session"]
        is_group = bool(message.get_group_id())

        scene = RstScene.get_scene(is_group, is_unique_session)

        alter_cmd_cfg = await sp.get_async("global", "global", "alter_cmd", {})
        plugin_config = alter_cmd_cfg.get("astrbot", {})
        reset_cfg = plugin_config.get("reset", {})

        required_perm = reset_cfg.get(
            scene.key,
            "admin" if is_group and not is_unique_session else "member",
        )

        if required_perm == "admin" and message.role != "admin":
            message.set_result(
                MessageEventResult().message(
                    f"在{scene.name}场景下，reset命令需要管理员权限，"
                    f"你 (ID {message.get_sender_id()}) 不是管理员，无法执行此操作。",
                ),
            )
            return

        agent_runner_type = cfg["provider_settings"]["agent_runner_type"]
        if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
            active_event_registry.stop_all(umo, exclude=message)
            await sp.remove_async(
                scope="umo",
                scope_id=umo,
                key=THIRD_PARTY_AGENT_RUNNER_KEY[agent_runner_type],
            )
            message.set_result(MessageEventResult().message("重置对话成功。"))
            return

        if not self.context.get_using_provider(umo):
            message.set_result(
                MessageEventResult().message("未找到任何 LLM 提供商。请先配置。"),
            )
            return

        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)

        if not cid:
            message.set_result(
                MessageEventResult().message(
                    f"当前未处于对话状态，请 {获取第一个唤醒词()}switch 切换或者 {获取第一个唤醒词()}new 创建。",
                ),
            )
            return

        active_event_registry.stop_all(umo, exclude=message)

        await self.context.conversation_manager.update_conversation(
            umo,
            cid,
            [],
        )

        # 清除群聊上下文增强的历史消息
        # 1. 设置标记，after_message_sent 钩子会清内存中的 raw_records
        message.set_extra("_clean_group_context_session", True)
        # 2. 清除数据库中的 platform_message_history
        try:
            await self.context.message_history_manager.delete_all(
                platform_id=message.get_platform_id(),
                user_id=umo,
            )
        except Exception:
            logger.exception("清除群聊消息历史失败。")

        ret = "清除聊天历史成功！"

        message.set_extra("_clean_ltm_session", True)

        message.set_result(MessageEventResult().message(ret))

    async def compact(
        self,
        message: AstrMessageEvent,
        arg1: str | int | None = None,
        arg2: str | int | None = None,
        arg3: str | int | None = None,
    ) -> None:
        """手动压缩上下文：/compact [yes] [保留最近N轮] [会话ID]。

        会话 ID 使用完整的「平台ID:消息类型:会话号」，参数顺序不限。
        不传会话 ID 时压缩当前对话，跨会话仅管理员可用。
        """
        allow_truncate = False
        keep_recent_rounds: int | None = None
        session_id: str | None = None
        tokens = [str(raw).strip() for raw in (arg1, arg2, arg3) if raw is not None]
        # 显式名称或完整 ID 存在时，其余数字只作轮数。
        has_named_target = any(
            token.lower() not in {"yes", "y", "是"}
            and 转整数或None(token)[1]
            for token in tokens
        )
        for token in tokens:
            invalid = False
            if token.lower() in {"yes", "y", "是"}:
                invalid = allow_truncate
                allow_truncate = True
            else:
                parsed, err = 转整数或None(token)
                is_target = bool(err)
                # 数字精确命中已知会话才当目标；否则保持原有保留轮数用法。
                if parsed is not None and parsed > 0 and not has_named_target:
                    candidates, _ = await self._resolve_targets(token)
                    is_target = bool(candidates)
                if is_target:
                    invalid = session_id is not None
                    session_id = token
                elif parsed is None or parsed <= 0 or keep_recent_rounds is not None:
                    invalid = True
                else:
                    keep_recent_rounds = parsed
            if invalid:
                message.set_result(MessageEventResult().message(
                    "使用方法：/compact [yes] [保留最近N轮] [会话]\n"
                    "参数无法识别或重复。会话支持群号、QQ号、昵称、群名、线程ID或完整会话ID。"
                ))
                return

        umo = await self._resolve_target_session(message, session_id)
        if umo is None:
            return
        cfg = self.context.get_config(umo=umo)
        agent_runner_type = cfg["provider_settings"]["agent_runner_type"]
        if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
            message.set_result(
                MessageEventResult().message(
                    f"当前 Agent 类型为 {agent_runner_type}，上下文由第三方托管，不支持手动压缩。"
                ),
            )
            return

        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        if not cid:
            message.set_result(
                MessageEventResult().message(
                    f"会话「{umo}」没有当前对话，请先在目标会话创建或切换对话。"
                    if session_id
                    else f"当前未处于对话状态，请 {获取第一个唤醒词()}switch 切换或者 {获取第一个唤醒词()}new 创建。"
                ),
            )
            return

        conv = await self.context.conversation_manager.get_conversation(umo, cid)
        if not conv or not conv.history:
            message.set_result(
                MessageEventResult().message("当前对话没有可压缩的历史消息。"),
            )
            return

        try:
            raw_history = json.loads(conv.history or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            message.set_result(
                MessageEventResult().message("对话历史解析失败，无法压缩。"),
            )
            return
        if not isinstance(raw_history, list) or not raw_history:
            message.set_result(
                MessageEventResult().message("当前对话没有可压缩的历史消息。"),
            )
            return

        messages = bind_checkpoint_messages(raw_history)
        if not any(msg.role != "system" for msg in messages):
            message.set_result(
                MessageEventResult().message("当前对话没有可压缩的历史消息。"),
            )
            return

        active_event_registry.stop_all(umo, exclude=message)

        tokens_before = EstimateTokenCounter().count_tokens(messages)
        rounds_before = len(split_into_rounds(messages))
        before_source = "估算"

        # 模型返回的真实占用优先（provider_stats 最近一次请求），缺失时才本地估算
        try:
            db = self.context.get_db()
            async with db.get_db() as db_session:
                last_stat = (
                    await db_session.execute(
                        select(ProviderStat)
                        .where(
                            col(ProviderStat.agent_type) == "internal",
                            col(ProviderStat.conversation_id) == cid,
                            col(ProviderStat.current_context_tokens) > 0,
                        )
                        .order_by(desc(ProviderStat.id))
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if last_stat is not None and last_stat.current_context_tokens > 0:
                tokens_before = int(last_stat.current_context_tokens)
                before_source = "模型返回"
        except Exception:
            logger.debug("读取压缩前上下文占用失败，改用本地估算", exc_info=True)

        settings = cfg.get("provider_settings") or {}
        if keep_recent_rounds is None:
            # 未显式指定保留轮数时，沿用自动压缩的「保留最近对话轮数」配置
            configured_rounds = int(settings.get("llm_compress_keep_recent_rounds", 5) or 0)
            if configured_rounds > 0:
                keep_recent_rounds = configured_rounds
        provider = None
        compress_provider_id = settings.get("llm_compress_provider_id") or ""
        if compress_provider_id:
            try:
                provider = self.context.get_provider_by_id(compress_provider_id)
            except Exception:
                provider = None
        if provider is None:
            try:
                provider = self.context.get_using_provider(umo=umo)
            except ValueError:
                provider = None

        # 手动 /compact 优先走 LLM 摘要；无模型时不自动回退截断
        summary_usage: TokenUsage | None = None
        if provider is None:
            hint = (
                "未找到可用的 LLM 压缩模型，无法执行摘要压缩。\n"
                "如仍要按轮次截断，请发送：/compact yes"
                + (f" {keep_recent_rounds}" if keep_recent_rounds else "")
                + (f" {umo}" if session_id else "")
            )
            if not allow_truncate:
                message.set_result(MessageEventResult().message(hint))
                return
            truncate_turns = int(settings.get("dequeue_context_length", 1) or 1)
            if keep_recent_rounds:
                # 保留最近 N 轮：等价于只丢更早的轮次
                from astrbot.core.agent.context.truncator import ContextTruncator

                truncator = ContextTruncator()
                compressed = truncator.truncate_by_turns(
                    messages,
                    keep_most_recent_turns=keep_recent_rounds,
                    drop_turns=max(1, truncate_turns),
                )
                method_label = f"按轮次截断（保留最近 {keep_recent_rounds} 轮）"
            else:
                compressor = TruncateByTurnsCompressor(
                    truncate_turns=max(1, truncate_turns)
                )
                compressed = await compressor(messages)
                method_label = "按轮次截断"
        else:
            keep_ratio = settings.get("llm_compress_keep_recent_ratio", 0.15)
            instruction = settings.get("llm_compress_instruction") or None
            compressor = LLMSummaryCompressor(
                provider=provider,
                keep_recent_ratio=float(keep_ratio),
                instruction_text=instruction,
                keep_recent_rounds=keep_recent_rounds,
            )
            method_label = "LLM 摘要"
            if keep_recent_rounds:
                method_label = f"LLM 摘要（保留最近 {keep_recent_rounds} 轮）"
            # LLM 摘要耗时较长，先发出提示，完成后再发结果
            await message.send(
                MessageChain().message(f"正在压缩上下文（{method_label}），请稍候…")
            )
            try:
                compressed = await compressor(messages)
            except Exception as e:
                logger.error("手动上下文压缩失败: %s", e, exc_info=True)
                message.set_result(
                    MessageEventResult().message(f"上下文压缩失败：{e}"),
                )
                return
            summary_usage = getattr(compressor, "last_usage", None)

        if not compressed:
            message.set_result(
                MessageEventResult().message("上下文压缩失败：结果为空，历史保持不变。"),
            )
            return

        if provider is not None and len(compressed) >= len(messages):
            # LLM 失败时会原样返回消息列表
            message.set_result(
                MessageEventResult().message(
                    "上下文压缩未生效（模型未返回有效摘要），历史保持不变。\n"
                    "如仍要按轮次截断，请发送：/compact yes"
                    + (f" {keep_recent_rounds}" if keep_recent_rounds else "")
                    + (f" {umo}" if session_id else "")
                ),
            )
            return

        try:
            dumped = dump_messages_with_checkpoints(compressed)
            await self.context.conversation_manager.update_conversation(
                umo,
                cid,
                dumped,
            )
        except Exception as e:
            logger.error("保存压缩后的历史失败: %s", e, exc_info=True)
            message.set_result(
                MessageEventResult().message(f"压缩完成但保存失败：{e}"),
            )
            return

        tokens_after = EstimateTokenCounter().count_tokens(compressed)
        rounds_after = len(split_into_rounds(compressed))
        lines = [
            f"上下文压缩完成（{method_label}）",
            f"会话 ID: {umo}",
            f"轮数: {rounds_before} → {rounds_after}",
        ]
        if provider is not None:
            lines.append(f"压缩前占用: {_format_tokens(tokens_before)}（{before_source}）")
        if provider is not None and summary_usage and summary_usage.output > 0:
            lines.append(f"摘要输出: {_format_tokens(summary_usage.output)}（模型返回）")
        else:
            lines.append(f"压缩后占用: {_format_tokens(tokens_after)}（估算）")
        message.set_result(MessageEventResult().message("\n".join(lines)))
    async def stop(self, message: AstrMessageEvent) -> None:
        """强制停止当前会话正在运行的 Agent（/stop）。

        与「新消息软打断」不同：
        - 立即 request_stop 活跃 runner，尽快中断生成
        - 标记 agent_force_stop，按已发送内容落库并追加英文停止标记
        """
        cfg = self.context.get_config(umo=message.unified_msg_origin)
        agent_runner_type = cfg["provider_settings"]["agent_runner_type"]
        umo = message.unified_msg_origin

        if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
            stopped_count = active_event_registry.stop_all(umo, exclude=message)
        else:
            # 强制停止：停发送；历史按已发送内容落库并追加英文停止标记
            stopped_count = active_event_registry.request_agent_stop_all(
                umo,
                exclude=message,
                extra_updates={"agent_force_stop": True},
            )
            # 立即打断活跃 runner，避免仅靠 0.5s 轮询 watcher 才停
            try:
                from astrbot.core.pipeline.process_stage.follow_up import (
                    get_active_runner,
                )

                active_runner = get_active_runner(umo)
                if active_runner is not None:
                    active_runner.request_stop()
            except Exception:
                logger.warning("获取或停止活跃 runner 失败", exc_info=True)

        if stopped_count > 0:
            message.set_result(
                MessageEventResult().message(
                    f"已强制停止 {stopped_count} 个运行中的任务。"
                )
            )
            return

        message.set_result(MessageEventResult().message("当前会话没有运行中的任务。"))

    async def his(self, message: AstrMessageEvent, page: int = 1) -> None:
        """查看对话记录"""
        if not self.context.get_using_provider(message.unified_msg_origin):
            message.set_result(
                MessageEventResult().message("未找到任何 LLM 提供商。请先配置。"),
            )
            return

        page, err = 转整数(page, "页码", 最小值=1)
        if err:
            message.set_result(MessageEventResult().message(err).use_t2i(False))
            return

        size_per_page = 6

        conv_mgr = self.context.conversation_manager
        umo = message.unified_msg_origin
        session_curr_cid = await conv_mgr.get_curr_conversation_id(umo)

        if not session_curr_cid:
            session_curr_cid = await conv_mgr.new_conversation(
                umo,
                message.get_platform_id(),
            )

        contexts, total_pages = await conv_mgr.get_human_readable_context(
            umo,
            session_curr_cid,
            page,
            size_per_page,
        )

        parts = []
        for context in contexts:
            if len(context) > 150:
                context = context[:150] + "..."
            parts.append(f"{context}\n")

        history = "".join(parts)
        ret = (
            f"当前对话历史记录："
            f"{history or '无历史记录'}\n\n"
            f"第 {page} 页 | 共 {total_pages} 页\n"
            f"*输入 {获取第一个唤醒词()}history 2 跳转到第 2 页"
        )

        message.set_result(MessageEventResult().message(ret).use_t2i(False))

    async def convs(self, message: AstrMessageEvent, page: int = 1) -> None:
        """查看对话列表"""
        cfg = self.context.get_config(umo=message.unified_msg_origin)
        agent_runner_type = cfg["provider_settings"]["agent_runner_type"]
        if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
            message.set_result(
                MessageEventResult().message(
                    f"{THIRD_PARTY_AGENT_RUNNER_STR} 对话列表功能暂不支持。",
                ),
            )
            return

        page, err = 转整数(page, "页码", 最小值=1)
        if err:
            message.set_result(MessageEventResult().message(err).use_t2i(False))
            return

        size_per_page = 6
        """获取所有对话列表"""
        conversations_all = await self.context.conversation_manager.get_conversations(
            message.unified_msg_origin,
        )
        """计算总页数"""
        total_pages = (len(conversations_all) + size_per_page - 1) // size_per_page
        """确保页码有效"""
        page = max(1, min(page, total_pages))
        """分页处理"""
        start_idx = (page - 1) * size_per_page
        end_idx = start_idx + size_per_page
        conversations_paged = conversations_all[start_idx:end_idx]

        parts = ["对话列表：\n---\n"]
        """全局序号从当前页的第一个开始"""
        global_index = start_idx + 1

        """生成所有对话的标题字典"""
        _titles = {}
        for conv in conversations_all:
            title = conv.title if conv.title else "新对话"
            _titles[conv.cid] = title

        """遍历分页后的对话生成列表显示"""
        provider_settings = cfg.get("provider_settings", {})
        platform_name = message.get_platform_name()
        for conv in conversations_paged:
            (
                persona_id,
                _,
                force_applied_persona_id,
                _,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=message.unified_msg_origin,
                conversation_persona_id=conv.persona_id,
                platform_name=platform_name,
                provider_settings=provider_settings,
            )
            if persona_id == "[%None]":
                persona_name = "无"
            elif persona_id:
                persona_name = persona_id
            else:
                persona_name = "无"

            if force_applied_persona_id:
                persona_name = f"{persona_name} (自定义规则)"

            title = _titles.get(conv.cid, "新对话")
            parts.append(
                f"{global_index}. {title}({conv.cid[:4]})\n  人格情景: {persona_name}\n  上次更新: {datetime.datetime.fromtimestamp(conv.updated_at).strftime('%m-%d %H:%M')}\n"
            )
            global_index += 1

        parts.append("---\n")
        ret = "".join(parts)
        curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
            message.unified_msg_origin,
        )
        if curr_cid:
            """从所有对话的标题字典中获取标题"""
            title = _titles.get(curr_cid, "新对话")
            ret += f"\n当前对话: {title}({curr_cid[:4]})"
        else:
            ret += "\n当前对话: 无"

        cfg = self.context.get_config(umo=message.unified_msg_origin)
        unique_session = cfg["platform_settings"]["unique_session"]
        if unique_session:
            ret += "\n会话隔离粒度: 个人"
        else:
            ret += "\n会话隔离粒度: 群聊"

        ret += f"\n第 {page} 页 | 共 {total_pages} 页"
        ret += f"\n*输入 {获取第一个唤醒词()}ls 2 跳转到第 2 页"
        ret += f"\n*输入 {获取第一个唤醒词()}switch <序号> 切换对话"

        message.set_result(MessageEventResult().message(ret).use_t2i(False))
        return

    async def new_conv(self, message: AstrMessageEvent) -> None:
        """创建新对话"""
        cfg = self.context.get_config(umo=message.unified_msg_origin)
        agent_runner_type = cfg["provider_settings"]["agent_runner_type"]
        if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
            active_event_registry.stop_all(message.unified_msg_origin, exclude=message)
            await sp.remove_async(
                scope="umo",
                scope_id=message.unified_msg_origin,
                key=THIRD_PARTY_AGENT_RUNNER_KEY[agent_runner_type],
            )
            message.set_result(MessageEventResult().message("已创建新对话。"))
            return

        active_event_registry.stop_all(message.unified_msg_origin, exclude=message)
        cpersona = await self._get_current_persona_id(message.unified_msg_origin)
        cid = await self.context.conversation_manager.new_conversation(
            message.unified_msg_origin,
            message.get_platform_id(),
            persona_id=cpersona,
        )

        message.set_extra("_clean_ltm_session", True)

        message.set_result(
            MessageEventResult().message(f"切换到新对话: 新对话({cid[:4]})。"),
        )

    async def groupnew_conv(self, message: AstrMessageEvent, sid: str = "") -> None:
        """创建新群聊对话"""
        sid = (sid or "").strip()
        if not sid:
            message.set_result(
                MessageEventResult().message(
                    f"请输入群聊 ID。使用方法：{获取第一个唤醒词()}groupnew <群聊ID>"
                ),
            )
            return
        session = str(
            MessageSession(
                platform_name=message.platform_meta.id,
                message_type=MessageType("GroupMessage"),
                session_id=sid,
            ),
        )

        cpersona = await self._get_current_persona_id(session)
        cid = await self.context.conversation_manager.new_conversation(
            session,
            message.get_platform_id(),
            persona_id=cpersona,
        )
        message.set_result(
            MessageEventResult().message(
                f"群聊 {session} 已切换到新对话: 新对话({cid[:4]})。",
            ),
        )

    async def switch_conv(
        self,
        message: AstrMessageEvent,
        index: int | None = None,
    ) -> None:
        """通过 /ls 前面的序号切换对话"""
        # index 可能是 None（省略）、int、或 str（框架传入的数字串）
        num, err = 转整数或None(index, "对话序号", 最小值=1)
        if err:
            message.set_result(
                MessageEventResult().message(
                    f"{err}\n使用 {获取第一个唤醒词()}ls 查看对话列表。"
                ),
            )
            return
        if num is None:
            message.set_result(
                MessageEventResult().message(
                    f"请输入对话序号。{获取第一个唤醒词()}switch 对话序号。{获取第一个唤醒词()}ls 查看对话 {获取第一个唤醒词()}new 新建对话"
                ),
            )
            return
        conversations = await self.context.conversation_manager.get_conversations(
            message.unified_msg_origin,
        )
        if num > len(conversations):
            message.set_result(
                MessageEventResult().message(f"对话序号超出范围，共 {len(conversations)} 个对话。使用 {获取第一个唤醒词()}ls 查看对话列表。"),
            )
        else:
            conversation = conversations[num - 1]
            title = conversation.title if conversation.title else "新对话"
            await self.context.conversation_manager.switch_conversation(
                message.unified_msg_origin,
                conversation.cid,
            )
            message.set_result(
                MessageEventResult().message(
                    f"切换到对话: {title}({conversation.cid[:4]})。",
                ),
            )

    async def rename_conv(self, message: AstrMessageEvent, new_name: str = "") -> None:
        """重命名对话"""
        new_name = (new_name or "").strip()
        if not new_name:
            message.set_result(
                MessageEventResult().message(
                    f"请输入新的对话名称。使用方法：{获取第一个唤醒词()}rename <新名称>"
                )
            )
            return
        await self.context.conversation_manager.update_conversation_title(
            message.unified_msg_origin,
            new_name,
        )
        message.set_result(MessageEventResult().message("重命名对话成功。"))

    async def del_conv(self, message: AstrMessageEvent) -> None:
        """删除当前对话"""
        umo = message.unified_msg_origin
        cfg = self.context.get_config(umo=umo)
        is_unique_session = cfg["platform_settings"]["unique_session"]
        if message.get_group_id() and not is_unique_session and message.role != "admin":
            # 群聊，没开独立会话，发送人不是管理员
            message.set_result(
                MessageEventResult().message(
                    f"会话处于群聊，并且未开启独立会话，并且你 (ID {message.get_sender_id()}) 不是管理员，因此没有权限删除当前对话。",
                ),
            )
            return

        agent_runner_type = cfg["provider_settings"]["agent_runner_type"]
        if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
            active_event_registry.stop_all(umo, exclude=message)
            await sp.remove_async(
                scope="umo",
                scope_id=umo,
                key=THIRD_PARTY_AGENT_RUNNER_KEY[agent_runner_type],
            )
            message.set_result(MessageEventResult().message("重置对话成功。"))
            return

        session_curr_cid = (
            await self.context.conversation_manager.get_curr_conversation_id(umo)
        )

        if not session_curr_cid:
            message.set_result(
                MessageEventResult().message(
                    f"当前未处于对话状态，请 {获取第一个唤醒词()}switch 序号 切换或 {获取第一个唤醒词()}new 创建。",
                ),
            )
            return

        active_event_registry.stop_all(umo, exclude=message)

        await self.context.conversation_manager.delete_conversation(
            umo,
            session_curr_cid,
        )

        ret = f"删除当前对话成功。不再处于对话状态，使用 {获取第一个唤醒词()}switch 序号 切换到其他对话或 {获取第一个唤醒词()}new 创建。"
        message.set_extra("_clean_ltm_session", True)
        message.set_result(MessageEventResult().message(ret))

    async def status(
        self,
        message: AstrMessageEvent,
        session_id: str | None = None,
    ) -> None:
        """查看当前对话状态及 Token 用量统计。

        会话 ID 使用完整的「平台ID:消息类型:会话号」，不传查看当前会话；
        跨会话仅机器人管理员可用。
        """
        umo = await self._resolve_target_session(message, session_id)
        if umo is None:
            return

        # 运行状态
        active_count = active_event_registry.count(umo, exclude=message)
        runner_active = False
        context_tokens = 0
        context_source = ""
        try:
            from astrbot.core.pipeline.process_stage.follow_up import (
                get_active_runner,
                has_active_runner,
            )

            runner_active = has_active_runner(umo)
            if runner_active:
                active_runner = get_active_runner(umo)
                runner_stats = getattr(active_runner, "stats", None)
                context_tokens = int(
                    getattr(runner_stats, "current_context_tokens", 0) or 0
                )
                if context_tokens > 0:
                    context_source = "模型返回"
        except Exception:
            runner_active = False

        if active_count > 0 or runner_active:
            run_line = f"Agent 状态: 是（{active_count} 个活跃任务）"
        else:
            run_line = "Agent 状态: 否"

        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)

        if not cid:
            message.set_result(
                MessageEventResult().message(
                    f"会话 ID: {umo}\n{run_line}\n目标会话没有当前对话。"
                    if session_id
                    else f"{run_line}\n当前没有进行中的对话，使用 {获取第一个唤醒词()}new 创建。"
                ),
            )
            return

        db = self.context.get_db()
        async with db.get_db() as session:
            result = await session.execute(
                select(
                    func.count(case((col(ProviderStat.id).is_not(None), 1))).label(
                        "record_count",
                    ),
                    func.coalesce(func.sum(ProviderStat.token_input_other), 0).label(
                        "total_input_other",
                    ),
                    func.coalesce(func.sum(ProviderStat.token_input_cached), 0).label(
                        "total_input_cached",
                    ),
                    func.coalesce(func.sum(ProviderStat.token_output), 0).label(
                        "total_output",
                    ),
                ).where(
                    col(ProviderStat.agent_type) == "internal",
                    col(ProviderStat.conversation_id) == cid,
                )
            )
            stats = result.one()

            if context_tokens <= 0:
                last_stat = (
                    await session.execute(
                        select(ProviderStat)
                        .where(
                            col(ProviderStat.agent_type) == "internal",
                            col(ProviderStat.conversation_id) == cid,
                            col(ProviderStat.current_context_tokens) > 0,
                        )
                        .order_by(desc(ProviderStat.id))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if last_stat is not None and last_stat.current_context_tokens > 0:
                    context_tokens = int(last_stat.current_context_tokens)
                    context_source = "模型返回"

        conv = await self.context.conversation_manager.get_conversation(umo, cid)
        if context_tokens <= 0 and conv is not None:
            context_tokens = _estimate_history_context_tokens(conv.history)
            if context_tokens > 0:
                context_source = "历史消息估算"

        if conv is not None and conv.updated_at > 0:
            updated_str = datetime.datetime.fromtimestamp(conv.updated_at).strftime(
                "%m-%d %H:%M"
            )
            active_line = f"最后活跃时间: {updated_str}"
        else:
            active_line = "最后活跃时间: 未知"

        total_input_other = stats.total_input_other
        total_input_cached = stats.total_input_cached
        total_output = stats.total_output
        total_input = total_input_other + total_input_cached
        total_tokens = total_input + total_output

        history_rounds = 0
        if conv is not None and conv.history:
            try:
                raw_history = json.loads(conv.history or "[]")
                if isinstance(raw_history, list) and raw_history:
                    # 一轮 = 从某条 user 起，到下一条 user 前的 assistant/tool
                    history_rounds = len(split_into_rounds(raw_history))
            except (TypeError, ValueError, json.JSONDecodeError):
                history_rounds = 0

        if context_tokens > 0:
            ctx_line = f"当前上下文: {_format_tokens(context_tokens)}"
            if context_source:
                ctx_line += f"（{context_source}）"
        else:
            ctx_line = "当前上下文: 未知"

        history_line = f"历史消息轮数: {history_rounds}"

        if stats.record_count == 0:
            ret = (
                f"会话 ID: {umo}\n"
                f"对话 ID: {cid[:8]}...\n"
                f"{active_line}\n"
                f"{run_line}\n"
                f"{ctx_line}\n"
                f"{history_line}\n"
                f"累计消耗: 暂无统计"
            )
        else:
            ret = (
                f"会话 ID: {umo}\n"
                f"对话 ID: {cid[:8]}...\n"
                f"{active_line}\n"
                f"{run_line}\n"
                f"{ctx_line}\n"
                f"{history_line}\n"
                f"累计消耗:\n"
                f"  总计:     {_format_tokens(total_tokens)}\n"
                f"  输入:     {_format_tokens(total_input)}\n"
                f"  输入缓存: {_format_tokens(total_input_cached)}\n"
                f"  输出:     {_format_tokens(total_output)}"
            )

        message.set_result(MessageEventResult().message(ret))
