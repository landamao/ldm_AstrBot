import inspect
import traceback
import typing as T

from astrbot import logger
from astrbot.core.message.components import Json
from astrbot.core.message.message_event_result import (
    CommandResult,
    MessageChain,
    MessageEventResult,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.star import StarMetadata, star_map
from astrbot.core.star.star_handler import EventType, star_handlers_registry


def plugin_display_name(md: StarMetadata | None) -> str:
    """插件展示名：优先 display_name，否则 name。"""
    if md is None:
        return "未知"
    name = (md.display_name or md.name or "").strip()
    return name or "未知"


def format_event_stopped_message(
    md: StarMetadata | None,
    handler_name: str | None,
) -> str:
    """插件「xxx」（方法）终止了事件传播"""
    method = (handler_name or "").strip() or "未知"
    return f"插件「{plugin_display_name(md)}」（{method}）终止了事件传播"


def log_event_stopped(md: StarMetadata | None, handler_name: str | None) -> None:
    logger.info(f"{format_event_stopped_message(md, handler_name)}。")


async def notify_event_stopped(
    event: AstrMessageEvent,
    md: StarMetadata | None,
    handler_name: str | None,
) -> None:
    """打日志；WebChat 再发一条结构化提示，ChatUI 原样显示。同一事件只通知一次。"""
    if event.get_extra("_event_stopped_notified"):
        return
    event.set_extra("_event_stopped_notified", True)
    log_event_stopped(md, handler_name)
    if event.get_platform_name() != "webchat":
        return
    text = format_event_stopped_message(md, handler_name)
    await event.send(
        MessageChain(
            type="event_stopped",
            chain=[
                Json(
                    {
                        "text": text,
                        "plugin": plugin_display_name(md),
                        "method": (handler_name or "").strip(),
                    }
                )
            ],
        )
    )


async def call_handler(
    event: AstrMessageEvent,
    handler: T.Callable[..., T.Awaitable[T.Any] | T.AsyncGenerator[T.Any, None]],
    *args,
    **kwargs,
) -> T.AsyncGenerator[T.Any, None]:
    """执行事件处理函数并处理其返回结果

    该方法负责调用处理函数并处理不同类型的返回值。它支持两种类型的处理函数:
    1. 异步生成器: 实现洋葱模型，每次 yield 都会将控制权交回上层
    2. 协程: 执行一次并处理返回值

    Args:
        event (AstrMessageEvent): 事件对象
        handler (Awaitable): 事件处理函数

    Returns:
        AsyncGenerator[None, None]: 异步生成器，用于在管道中传递控制流

    """
    ready_to_call = None  # 一个协程或者异步生成器

    trace_ = None

    try:
        ready_to_call = handler(event, *args, **kwargs)
    except TypeError:
        logger.error("处理函数参数不匹配，请检查 handler 的定义。", exc_info=True)

    if not ready_to_call:
        return

    if inspect.isasyncgen(ready_to_call):
        _has_yielded = False
        try:
            async for ret in ready_to_call:
                # 这里逐步执行异步生成器, 对于每个 yield 返回的 ret, 执行下面的代码
                # 返回值只能是 MessageEventResult 或者 None（无返回值）
                _has_yielded = True
                if isinstance(ret, MessageEventResult | CommandResult):
                    # 如果返回值是 MessageEventResult, 设置结果并继续
                    event.set_result(ret)
                    yield
                else:
                    # 如果返回值是 None, 则不设置结果并继续
                    # 继续执行后续阶段
                    yield ret
            if not _has_yielded:
                # 如果这个异步生成器没有执行到 yield 分支
                yield
        except Exception as e:
            logger.error(f"Previous Error: {trace_}")
            raise e
    elif inspect.iscoroutine(ready_to_call):
        # 如果只是一个协程, 直接执行
        ret = await ready_to_call
        if isinstance(ret, MessageEventResult | CommandResult):
            event.set_result(ret)
            yield
        else:
            yield ret


async def call_event_hook(
    event: AstrMessageEvent,
    hook_type: EventType,
    *args,
    **kwargs,
) -> bool:
    """调用事件钩子函数

    Returns:
        bool: 如果事件被终止，返回 True
    #

    """
    handlers = star_handlers_registry.get_handlers_by_event_type(
        hook_type,
        plugins_name=event.plugins_name,
    )
    # 会话级禁用插件过滤：与指令链路保持一致，被会话规则禁用的插件其钩子不再触发
    from astrbot.core.star.session_plugin_manager import SessionPluginManager

    handlers = await SessionPluginManager.filter_handlers_by_session(event, handlers)
    for handler in handlers:
        try:
            assert inspect.iscoroutinefunction(handler.handler)
            logger.debug(
                f"hook({hook_type.name}) -> {star_map[handler.handler_module_path].name} - {handler.handler_name}",
            )
            await handler.handler(event, *args, **kwargs)
        except BaseException:
            logger.error(traceback.format_exc())

        if event.is_stopped():
            await notify_event_stopped(
                event,
                star_map.get(handler.handler_module_path),
                handler.handler_name,
            )
            return True

    return event.is_stopped()
