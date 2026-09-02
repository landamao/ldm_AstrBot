import builtins
from types import SimpleNamespace
from typing import TYPE_CHECKING

from sqlalchemy import select

from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core import logger, sp
from astrbot.core.db.po import ConversationV2
from astrbot.core.utils.active_event_registry import active_event_registry
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词

from .conversation import THIRD_PARTY_AGENT_RUNNER_KEY

if TYPE_CHECKING:
    from astrbot.core.db.po import Persona


class PersonaCommands:
    def __init__(self, context: star.Context) -> None:
        self.context = context

    def _build_tree_output(
        self,
        folder_tree: list[dict],
        all_personas: list["Persona"],
        depth: int = 0,
    ) -> list[str]:
        """递归构建树状输出，使用短线条表示层级"""
        lines: list[str] = []
        # 使用短线条作为缩进前缀，每层只用 "│" 加一个空格
        prefix = "│ " * depth

        for folder in folder_tree:
            # 输出文件夹
            lines.append(f"{prefix}├ 📁 {folder['name']}/")

            # 获取该文件夹下的人格
            folder_personas = [
                p for p in all_personas if p.folder_id == folder["folder_id"]
            ]
            child_prefix = "│ " * (depth + 1)

            # 输出该文件夹下的人格
            for persona in folder_personas:
                lines.append(f"{child_prefix}├ 👤 {persona.persona_id}")

            # 递归处理子文件夹
            children = folder.get("children", [])
            if children:
                lines.extend(
                    self._build_tree_output(
                        children,
                        all_personas,
                        depth + 1,
                    )
                )

        return lines

    # ==== 会话ID解析（远程换人格用） ====

    async def _known_umos(self) -> list[str]:
        """已知 UMO 列表，与 WebUI 会话管理同源（conversations 表 distinct user_id）"""
        db = self.context.get_db()
        async with db.get_db() as session:
            result = await session.execute(select(ConversationV2.user_id).distinct())
            return sorted({str(row[0]) for row in result.fetchall() if row[0]})

    @staticmethod
    def _umo_matches(umo: str, target: str) -> bool:
        """会话ID匹配：完整 UMO / 会话ID段 / WebChat 线程的 ! 分段（不含部分数字误匹配）"""
        parts = umo.split(":", 2)
        sid = parts[2] if len(parts) >= 3 else umo
        return (
            umo == target
            or sid == target
            or target in sid.split("!")
            or sid.startswith(f"{target}!")
        )

    async def _resolve_targets(self, raw: str) -> tuple[list[str], dict]:
        """把输入解析为候选 UMO 列表与别称映射。

        支持群号/QQ号/WebChat 线程分段/完整 UMO/昵称/群名（别称仅精确相等）。
        """
        raw = (raw or "").strip()
        candidates: set[str] = set()
        aliases: list = []
        if raw:
            try:
                aliases = await self.context.get_db().get_umo_aliases()
            except Exception:
                logger.warning("人格指令: 读取会话别称失败", exc_info=True)
                aliases = []
            try:
                umos = await self._known_umos()
            except Exception:
                logger.warning("人格指令: 读取已知会话失败", exc_info=True)
                umos = []
            candidates = {umo for umo in umos if self._umo_matches(umo, raw)}
            umo_set = set(umos)
            for alias in aliases:
                # 昵称/群名命中也必须在 conversations 表里有对话，
                # 防止只剩昵称残留的死会话被远程换人格建活
                if (
                    raw in (alias.user_alias, alias.auto_name)
                    and alias.umo
                    and alias.umo in umo_set
                ):
                    candidates.add(alias.umo)
        alias_map = {a.umo: a for a in aliases}
        return sorted(candidates), alias_map

    @staticmethod
    def _display(umo: str, alias_map: dict) -> str:
        alias = alias_map.get(umo)
        name = ""
        if alias is not None:
            name = (alias.user_alias or alias.auto_name or "").strip()
        return f"{umo}（{name}）" if name else umo

    async def _pick_target(
        self,
        message: AstrMessageEvent,
        raw: str,
    ) -> tuple[str | None, str | None]:
        """解析会话ID，唯一命中返回 (umo, 显示名)；失败直接设置回执并返回 (None, None)"""
        candidates, alias_map = await self._resolve_targets(raw)
        if not candidates:
            logger.warning(f"人格指令: 会话ID「{raw}」未匹配到任何会话")
            message.set_result(
                MessageEventResult().message(
                    f"未找到会话「{raw}」，请输入对方的群号/QQ号/昵称。",
                ),
            )
            return None, None
        if len(candidates) > 1:
            lines = [f"会话ID「{raw}」匹配到多个会话，请输入更精确的ID："]
            lines += [f"- {self._display(u, alias_map)}" for u in candidates]
            message.set_result(
                MessageEventResult().message("\n".join(lines)).use_t2i(False),
            )
            return None, None
        umo = candidates[0]
        return umo, self._display(umo, alias_map)

    # ==== 远程/本会话通用的 set / unset / reset ====

    async def _set_persona_on(
        self,
        message: AstrMessageEvent,
        target_umo: str,
        display: str,
        ps: str,
        explicit: bool,
        hint: str | None,
    ) -> None:
        前缀 = 获取第一个唤醒词()
        cfg = (
            await sp.get_async("umo", target_umo, "session_service_config", {}) or {}
        )
        if cfg.get("persona_id"):
            # 自定义规则强制了人格 → 直接改规则值，实时生效
            cfg["persona_id"] = ps
            await sp.put_async("umo", target_umo, "session_service_config", cfg)
            logger.info(
                f"人格指令: 会话 {target_umo} 的自定义规则人格已设为「{ps}」",
            )
            if explicit:
                reset_hint = (
                    f"{前缀}persona reset {hint}"
                    if hint
                    else f"{前缀}persona reset 会话ID"
                )
                msg = (
                    f"已将 {display} 的自定义规则人格设为「{ps}」，立即生效。\n"
                    f"如需清空上下文请使用指令\n{reset_hint}。"
                )
            else:
                msg = (
                    f"已将自定义规则的人格设为「{ps}」，立即生效。"
                    f"如需清空上下文请注意使用 {前缀}reset。"
                )
            message.set_result(MessageEventResult().message(msg).use_t2i(False))
            return

        cm = self.context.conversation_manager
        cid = await cm.get_curr_conversation_id(target_umo)
        if cid:
            await cm.update_conversation_persona_id(target_umo, ps, cid)
            logger.info(f"人格指令: 会话 {target_umo} 当前对话人格已设为「{ps}」")
            if explicit:
                reset_hint = (
                    f"{前缀}persona reset {hint}"
                    if hint
                    else f"{前缀}persona reset 会话ID"
                )
                msg = (
                    f"已将 {display} 当前对话的人格设为「{ps}」。\n"
                    f"如需清空上下文请使用指令\n{reset_hint}。"
                )
            else:
                msg = (
                    f"设置成功。如果你正在切换到不同的人格，请注意使用 {前缀}reset "
                    f"来清空上下文，防止原人格对话影响现人格。"
                )
            message.set_result(MessageEventResult().message(msg))
            return

        # 没有对话 → 自动创建并带上人格，对方下一条消息即用上新人格
        await cm.new_conversation(target_umo, persona_id=ps)
        logger.info(f"人格指令: 会话 {target_umo} 没有对话，已自动创建并设人格「{ps}」")
        if explicit:
            msg = f"{display} 当前没有对话，已自动创建并设为人格「{ps}」，对方下一条消息即生效。"
        else:
            msg = f"当前没有对话，已自动创建并设为人格「{ps}」，下一条消息即生效。"
        message.set_result(MessageEventResult().message(msg))

    async def _unset_persona_on(
        self,
        message: AstrMessageEvent,
        target_umo: str,
        display: str,
        explicit: bool,
    ) -> None:
        cfg = (
            await sp.get_async("umo", target_umo, "session_service_config", {}) or {}
        )
        if cfg.get("persona_id"):
            # 自定义规则强制了人格 → 从规则里移除（其余字段保留）
            cfg.pop("persona_id", None)
            await sp.put_async("umo", target_umo, "session_service_config", cfg)
            logger.info(f"人格指令: 已移除会话 {target_umo} 的自定义规则人格")
            if explicit:
                msg = f"已从 {display} 的自定义规则移除人格强制，恢复按对话/默认人格生效。"
            else:
                msg = "已从自定义规则移除人格强制，恢复按对话/默认人格生效。"
            message.set_result(MessageEventResult().message(msg).use_t2i(False))
            return

        cid = await self.context.conversation_manager.get_curr_conversation_id(
            target_umo,
        )
        if not cid:
            msg = (
                f"{display} 没有自定义规则人格，也没有进行中的对话。"
                if explicit
                else "当前没有对话，无法取消人格。"
            )
            message.set_result(MessageEventResult().message(msg))
            return
        await self.context.conversation_manager.update_conversation_persona_id(
            target_umo,
            "[%None]",
            cid,
        )
        logger.info(f"人格指令: 已取消会话 {target_umo} 当前对话的人格")
        msg = (
            f"已取消 {display} 当前对话的人格。" if explicit else "取消人格成功。"
        )
        message.set_result(MessageEventResult().message(msg))

    async def _reset_on(
        self,
        message: AstrMessageEvent,
        target_umo: str,
        display: str,
        explicit: bool,
    ) -> None:
        前缀 = 获取第一个唤醒词()
        cfg = self.context.get_config(umo=target_umo)
        agent_runner_type = cfg["provider_settings"]["agent_runner_type"]
        if agent_runner_type in THIRD_PARTY_AGENT_RUNNER_KEY:
            active_event_registry.stop_all(target_umo, exclude=message)
            await sp.remove_async(
                "umo",
                target_umo,
                THIRD_PARTY_AGENT_RUNNER_KEY[agent_runner_type],
            )
            logger.info(
                f"人格指令: 已重置会话 {target_umo} 的第三方 Agent 会话（{agent_runner_type}）",
            )
            msg = (
                f"已重置 {display} 的第三方 Agent 会话。"
                if explicit
                else "重置对话成功。"
            )
            message.set_result(MessageEventResult().message(msg))
            return

        if not self.context.get_using_provider(target_umo):
            message.set_result(
                MessageEventResult().message("未找到任何 LLM 提供商。请先配置。"),
            )
            return

        cid = await self.context.conversation_manager.get_curr_conversation_id(
            target_umo,
        )
        if not cid:
            msg = (
                f"{display} 当前没有对话，无需重置。"
                if explicit
                else f"当前未处于对话状态，请 {前缀}switch 切换或者 {前缀}new 创建。"
            )
            message.set_result(MessageEventResult().message(msg))
            return

        active_event_registry.stop_all(target_umo, exclude=message)
        await self.context.conversation_manager.update_conversation(
            target_umo,
            cid,
            [],
        )
        # 清理群聊上下文增强：内存 raw_records（仅群聊会话）
        if ":GroupMessage:" in target_umo:
            try:
                metadata = self.context.get_registered_star("ldm")
                gcc = getattr(
                    getattr(metadata, "star_cls", None),
                    "group_chat_context",
                    None,
                )
                if gcc is not None:
                    await gcc.remove_session(
                        SimpleNamespace(unified_msg_origin=target_umo),
                    )
            except Exception:
                logger.warning("人格指令: 清理群聊上下文失败", exc_info=True)
        # 清理数据库中的群聊消息历史（与 /reset 保持一致）
        try:
            platform_id = target_umo.split(":", 1)[0]
            await self.context.message_history_manager.delete_all(
                platform_id,
                target_umo,
            )
        except Exception:
            logger.warning("人格指令: 清理群聊消息历史失败", exc_info=True)
        logger.info(f"人格指令: 已重置会话 {target_umo} 的对话上下文")
        msg = (
            f"已重置 {display} 的对话上下文。" if explicit else "清除聊天历史成功！"
        )
        message.set_result(MessageEventResult().message(msg))

    # ==== 指令入口 ====

    async def _show_info(self, message: AstrMessageEvent, umo: str) -> None:
        curr_persona_name = "无"
        cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
        default_persona = await self.context.persona_manager.get_default_persona_v3(
            umo=umo,
        )
        session_service_config = (
            await sp.get_async("umo", umo, "session_service_config", {}) or {}
        )
        force_applied_persona_id = session_service_config.get("persona_id") or None

        curr_cid_title = "无"
        if cid:
            conv = await self.context.conversation_manager.get_conversation(
                unified_msg_origin=umo,
                conversation_id=cid,
                create_if_not_exists=True,
            )
            if conv is None:
                message.set_result(
                    MessageEventResult().message(
                        f"当前对话不存在，请先使用 {获取第一个唤醒词()}new 新建一个对话。",
                    ),
                )
                return

            provider_settings = self.context.get_config(umo=umo).get(
                "provider_settings",
                {},
            )
            (
                persona_id,
                _,
                _,
                _,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=conv.persona_id,
                platform_name=message.get_platform_name(),
                provider_settings=provider_settings,
            )

            if persona_id == "[%None]":
                curr_persona_name = "无"
            elif persona_id:
                curr_persona_name = persona_id

            if force_applied_persona_id:
                curr_persona_name = f"{curr_persona_name} (自定义规则)"

            curr_cid_title = conv.title if conv.title else "新对话"
            curr_cid_title += f"({cid[:4]})"

        前缀 = 获取第一个唤醒词()
        message.set_result(
            MessageEventResult()
            .message(
                f"""[Persona]

- 设置人格情景: `{前缀}persona 人格 [会话ID]`
- 人格情景列表: `{前缀}persona list`
- 人格情景详细信息: `{前缀}persona view 人格`
- 取消人格: `{前缀}persona unset [会话ID]`
- 重置对话上下文: `{前缀}persona reset [会话ID]`

会话ID支持对方的群号/QQ号/昵称，省略时作用于当前会话。
远程设置人格时若对方没有对话会自动创建。

默认人格情景: {default_persona["name"]}
当前对话 {curr_cid_title} 的人格情景: {curr_persona_name}

配置人格情景请前往管理面板-配置页
""",
            )
            .use_t2i(False),
        )

    async def persona(self, message: AstrMessageEvent) -> None:
        l = message.message_str.split(" ")  # noqa: E741
        umo = message.unified_msg_origin

        if len(l) == 1:
            await self._show_info(message, umo)
            return

        if l[1] == "list":
            # 获取文件夹树和所有人格
            folder_tree = await self.context.persona_manager.get_folder_tree()
            all_personas = self.context.persona_manager.personas

            lines = ["📂 人格列表：\n"]

            # 构建树状输出
            tree_lines = self._build_tree_output(folder_tree, all_personas)
            lines.extend(tree_lines)

            # 输出根目录下的人格（没有文件夹的）
            root_personas = [p for p in all_personas if p.folder_id is None]
            if root_personas:
                if tree_lines:  # 如果有文件夹内容，加个空行
                    lines.append("")
                for persona in root_personas:
                    lines.append(f"👤 {persona.persona_id}")

            # 统计信息
            total_count = len(all_personas)
            lines.append(f"\n共 {total_count} 个人格")
            lines.append(f"\n*使用 `{获取第一个唤醒词()}persona <人格名>` 设置人格")
            lines.append(f"*使用 `{获取第一个唤醒词()}persona view <人格名>` 查看详细信息")

            msg = "\n".join(lines)
            message.set_result(MessageEventResult().message(msg).use_t2i(False))
        elif l[1] == "view":
            if len(l) == 2:
                message.set_result(MessageEventResult().message("请输入人格情景名"))
                return
            ps = l[2].strip()
            if persona := next(
                builtins.filter(
                    lambda persona: persona["name"] == ps,
                    self.context.provider_manager.personas,
                ),
                None,
            ):
                msg = f"人格{ps}的详细信息：\n"
                msg += f"{persona['prompt']}\n"
            else:
                msg = f"人格{ps}不存在"
            message.set_result(MessageEventResult().message(msg))
        elif l[1] == "unset":
            raw = "".join(l[2:]).strip()
            if not raw:
                await self._unset_persona_on(message, umo, "当前会话", False)
                return
            target, display = await self._pick_target(message, raw)
            if target:
                await self._unset_persona_on(message, target, display, True)
        elif l[1] == "reset":
            raw = "".join(l[2:]).strip()
            if not raw:
                await self._reset_on(message, umo, "当前会话", False)
                return
            target, display = await self._pick_target(message, raw)
            if target:
                await self._reset_on(message, target, display, True)
        else:
            tokens = [t for t in l[1:] if t]  # 连续空格时丢弃空 token
            ps = "".join(tokens).strip()
            explicit = False
            target_umo = umo
            display = "当前会话"
            hint = None
            if len(tokens) >= 2:
                # 末位 token 能命中已知会话时视作会话ID，其余部分才是人格名
                candidates, alias_map = await self._resolve_targets(tokens[-1])
                if len(candidates) > 1:
                    lines = [f"会话ID「{tokens[-1]}」匹配到多个会话，请输入更精确的ID："]
                    lines += [f"- {self._display(u, alias_map)}" for u in candidates]
                    message.set_result(
                        MessageEventResult().message("\n".join(lines)).use_t2i(False),
                    )
                    return
                if len(candidates) == 1:
                    explicit = True
                    target_umo = candidates[0]
                    display = self._display(target_umo, alias_map)
                    hint = tokens[-1]
                    ps = "".join(tokens[:-1]).strip()
                else:
                    # 末位不是有效会话ID：若整体拼接是有效人格名则按旧习惯
                    # 作用当前会话（兼容人格名带空格），否则按误用拒绝执行
                    joined = "".join(tokens).strip()
                    if any(
                        p["name"] == joined
                        for p in self.context.provider_manager.personas
                    ):
                        ps = joined
                        logger.info(
                            f"人格指令: 末位非会话ID，按当前会话人格名处理「{ps}」",
                        )
                    else:
                        logger.warning(
                            f"人格指令: 会话ID「{tokens[-1]}」未匹配到任何会话",
                        )
                        message.set_result(
                            MessageEventResult().message(
                                f"未找到会话「{tokens[-1]}」，请输入对方的群号/QQ号/昵称。",
                            ),
                        )
                        return
            if persona := next(
                builtins.filter(
                    lambda persona: persona["name"] == ps,
                    self.context.provider_manager.personas,
                ),
                None,
            ):
                logger.info(
                    f"人格指令: 收到设置请求 目标={target_umo} 人格=「{ps}」",
                )
                await self._set_persona_on(
                    message,
                    target_umo,
                    display,
                    ps,
                    explicit,
                    hint,
                )
            else:
                logger.warning(
                    f"人格指令: 人格「{ps}」不存在（目标: {target_umo}）",
                )
                message.set_result(
                    MessageEventResult().message(
                        f"不存在该人格情景。使用 {获取第一个唤醒词()}persona list 查看所有。",
                    ),
                )
