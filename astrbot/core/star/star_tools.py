from __future__ import annotations

import inspect
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.core.message.components import BaseMessageComponent
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import MessageSesion
from astrbot.core.star.context import Context
from astrbot.core.star.star import star_map
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.io import ensure_dir

if TYPE_CHECKING:
    from PIL import ImageFont


class StarTools:
    """提供给插件的便捷工具方法。"""

    _context: ClassVar[Context | None] = None

    @classmethod
    def initialize(cls, context: Context) -> None:
        """用上下文引用初始化 StarTools。

        Args:
            context: 暴露给插件的 Context 上下文对象。
        """
        cls._context = context

    @classmethod
    async def send_message(
        cls,
        session: str | MessageSesion,
        message_chain: MessageChain,
    ) -> bool:
        """通过统一消息源头（unified_msg_origin）向会话发送消息。

        Args:
            session: 消息会话，取自 event.session 或 event.unified_msg_origin。
            message_chain: 要发送的消息链。

        Returns:
            是否找到了匹配的平台。

        Raises:
            ValueError: StarTools 未初始化或会话解析失败时抛出。
        """
        if cls._context is None:
            raise ValueError("StarTools 未初始化")
        return await cls._context.send_message(session, message_chain)

    @classmethod
    async def send_message_by_id(
        cls,
        type: str,
        id: str,
        message_chain: MessageChain,
        platform: str = "aiocqhttp",
    ) -> bool:
        """按类型和 ID 向群聊或私聊发送消息。

        Args:
            type: 群聊填 "GroupMessage"，私聊填 "PrivateMessage"。
            id: 群 ID 或用户 ID。
            message_chain: 要发送的消息链。
            platform: 平台适配器名称或实例 ID。

        Returns:
            是否找到了匹配的平台。
        """
        if cls._context is None:
            raise ValueError("StarTools 未初始化")
        # 按平台实例 ID 匹配；找不到再按适配器名称匹配
        platforms = cls._context.platform_manager.get_insts()
        platform_id = None
        for p in platforms:
            if p.meta().id == platform:
                platform_id = p.meta().id
                break
        if platform_id is None:
            for p in platforms:
                if p.meta().name == platform:
                    platform_id = p.meta().id
                    break
        if platform_id is None:
            raise ValueError(f"找不到平台: {platform}")
        session_str = f"{platform_id}:{type}:{id}"
        return await cls._context.send_message(session_str, message_chain)

    @classmethod
    async def create_message(
        cls,
        type: str,
        self_id: str,
        session_id: str,
        sender: MessageMember,
        message: list[BaseMessageComponent],
        message_str: str,
        message_id: str = "",
        raw_message: object = None,
        group_id: str = "",
    ) -> AstrBotMessage:
        """构造一个 AstrBot 消息对象。

        Args:
            type: 消息类型，如 "GroupMessage"、"FriendMessage" 或
                "OtherMessage"。
            self_id: 机器人自身 ID。
            session_id: 会话 ID，通常是用户 ID 或群 ID。
            sender: 发送者信息，如
                MessageMember(user_id="123456", nickname="昵称")。
            message: 消息组件列表。该消息链不会直接发给 LLM，
                但可能被其他处理器处理。
            message_str: 发给 LLM 的纯文本消息，应与消息链内容对应。
            message_id: 消息 ID。留空则自动生成。
            raw_message: 原始消息对象。
            group_id: 群 ID。私聊时留空。

        Returns:
            构造好的 AstrBot 消息对象。
        """
        abm = AstrBotMessage()
        abm.type = MessageType(type)
        abm.self_id = self_id
        abm.session_id = session_id
        if message_id == "":
            message_id = uuid.uuid4().hex
        abm.message_id = message_id
        abm.sender = sender
        abm.message = message
        abm.message_str = message_str
        abm.raw_message = raw_message
        abm.group_id = group_id
        return abm

    @classmethod
    async def create_event(
        cls,
        abm: AstrBotMessage,
        platform: str = "aiocqhttp",
        is_wake: bool = True,
    ) -> None:
        """构造事件并投递到目标平台。

        Args:
            abm: 要提交的消息对象，需先用 create_message 创建。
            platform: 平台 ID 或适配器名称。为兼容旧版，默认 aiocqhttp。
            is_wake: 是否将事件标记为唤醒事件。只有唤醒事件才会
                获得 LLM 回复。

        Raises:
            ValueError: StarTools 未初始化或找不到平台时抛出。
        """
        if cls._context is None:
            raise ValueError("StarTools 未初始化")
        platforms = cls._context.platform_manager.get_insts()
        adapter = next((p for p in platforms if p.meta().id == platform), None)
        if adapter is None:
            adapter = next((p for p in platforms if p.meta().name == platform), None)
        if adapter is None:
            raise ValueError(f"找不到平台: {platform}")

        event = adapter.create_event(abm)
        event.is_wake = is_wake
        adapter.commit_event(event)

    @classmethod
    def activate_llm_tool(cls, name: str) -> bool:
        """激活一个已注册的函数调用工具。

        Args:
            name: 工具名称。

        Returns:
            是否激活成功。

        Raises:
            ValueError: StarTools 未初始化时抛出。
        """
        if cls._context is None:
            raise ValueError("StarTools 未初始化")
        return cls._context.activate_llm_tool(name)

    @classmethod
    def deactivate_llm_tool(cls, name: str) -> bool:
        """停用一个已注册的函数调用工具。

        Args:
            name: 工具名称。

        Returns:
            是否停用成功。

        Raises:
            ValueError: StarTools 未初始化时抛出。
        """
        if cls._context is None:
            raise ValueError("StarTools 未初始化")
        return cls._context.deactivate_llm_tool(name)

    @classmethod
    def register_llm_tool(
        cls,
        name: str,
        func_args: list,
        desc: str,
        func_obj: Callable[..., Awaitable[Any]],
    ) -> None:
        """注册一个函数调用工具。

        Args:
            name: 工具名称。
            func_args: 函数参数定义。
            desc: 工具描述。
            func_obj: 函数对象，必须是异步函数。

        Raises:
            ValueError: StarTools 未初始化时抛出。
        """
        if cls._context is None:
            raise ValueError("StarTools 未初始化")
        cls._context.register_llm_tool(name, func_args, desc, func_obj)

    @classmethod
    def unregister_llm_tool(cls, name: str) -> None:
        """注销一个函数调用工具。

        Args:
            name: 工具名称。

        Raises:
            ValueError: StarTools 未初始化时抛出。
        """
        if cls._context is None:
            raise ValueError("StarTools 未初始化")
        cls._context.unregister_llm_tool(name)


    @classmethod
    def get_data_dir(cls, plugin_name: str | None = None) -> Path:
        """获取插件数据目录的绝对路径。

        该方法会在 data/plugin_data 下创建插件专属目录。若不传
        plugin_name，则自动从调用栈中识别调用者所属插件。

        Args:
            plugin_name: 可选的插件名称。为 None 时自动识别调用者
                插件名。

        Returns:
            插件数据目录的绝对路径，即 data/plugin_data/{plugin_name}。

        Raises:
            RuntimeError: 无法解析调用者模块信息或模块元数据，或
                目录创建失败时抛出。
            ValueError: 无法解析插件名称时抛出。
        """
        if not plugin_name:
            frame = inspect.currentframe()
            module = None
            if frame:
                frame = frame.f_back
                module = inspect.getmodule(frame)

            if not module:
                raise RuntimeError("无法解析调用者模块信息")

            metadata = star_map.get(module.__name__, None)

            if not metadata:
                raise RuntimeError(
                    f"无法解析模块 {module.__name__} 的插件元数据",
                )

            plugin_name = metadata.name

        if not plugin_name:
            raise ValueError("无法解析插件名称")

        data_dir = Path(
            os.path.join(get_astrbot_data_path(), "plugin_data", plugin_name),
        )

        try:
            ensure_dir(data_dir)
        except OSError as e:
            if isinstance(e, PermissionError):
                raise RuntimeError(
                    f"无法创建目录 {data_dir}: 权限不足",
                ) from e
            raise RuntimeError(f"无法创建目录 {data_dir}: {e!s}") from e

        return data_dir.resolve()

    @classmethod
    def get_font_path(cls) -> Path:
        return Path(get_astrbot_data_path()) / "font.ttf"

    @classmethod
    def get_font(
        cls, size: int = 14
    ) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        """获取共享字体实例。

        按 size 缓存，跨插件复用同一个字体对象，避免每个插件各自
        加载字体实例导致内存浪费。

        字体查找顺序：data/font.ttf（自定义）→ 系统字体（跨平台
        CJK 回退链）→ PIL 默认字体。

        Args:
            size: 字体大小（像素），默认 14。

        Returns:
            PIL 字体实例（FreeTypeFont 或 ImageFont）。
        """
        from astrbot.core.utils.t2i.local_strategy import FontManager

        return FontManager.get_font(size)
