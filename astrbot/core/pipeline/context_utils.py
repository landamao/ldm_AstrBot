import functools
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
from astrbot.core.star.star_handler import (
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)


def plugin_display_name(md: StarMetadata | None) -> str:
    """插件展示名：优先 display_name，否则 name。"""
    if md is None:
        return "未知"
    name = (md.display_name or md.name or "").strip()
    return name or "未知"


def _handler_code(handler: StarHandlerMetadata) -> T.Any | None:
    """取 handler 可调对象底层的 code 对象，无法解析返回 None。

    插件实例化后 handler 会被绑成 functools.partial(裸函数, 实例)（LLM 工具同样），
    partial 没有 __code__，必须拆到最底层函数；bound method 则取 __func__。
    """
    func = handler.handler
    while isinstance(func, functools.partial):
        func = func.func
    func = getattr(func, "__func__", func)
    return getattr(func, "__code__", None)


def resolve_stop_source(
    event: AstrMessageEvent,
) -> tuple[StarMetadata | None, str | None]:
    """从 stop_event() 留下的调用栈解析真实停止来源。

    由内向外找第一个注册插件处理器帧；找不到返回 (None, None)，
    由调用方输出「未找到调用点」，不做时序猜测。
    """
    frames = getattr(event, "_stopped_by_frames", None) or []
    if not frames:
        return None, None
    code_map: dict[T.Any, tuple[str, str]] = {}
    for handler in star_handlers_registry.star_handlers_map.values():
        code = _handler_code(handler)
        if code is not None:
            code_map[code] = (handler.handler_module_path, handler.handler_name)
    for code, _lineno in frames:
        hit = code_map.get(code)
        if hit is None:
            continue
        return star_map.get(hit[0]), hit[1]
    return None, None


def _stop_outline(event: AstrMessageEvent) -> str:
    """停止日志附带的消息概要，空消息占位。"""
    outline = (event.get_message_outline() or "").strip()
    return outline or "（空）"


def format_event_stopped_message(plugin_name: str, method: str, outline: str) -> str:
    """插件「xxx」（方法）终止了事件传播。消息概要「xxx」"""
    return f"插件「{plugin_name}」（{method}）终止了事件传播。消息概要「{outline}」"


def format_unknown_stopped_message(outline: str) -> str:
    """无法归因时的固定文案，不做时序猜测。"""
    return f"事件被终止传播，未找到调用点。消息概要「{outline}」"


async def notify_event_stopped(event: AstrMessageEvent) -> None:
    """按 stop_event() 留痕归因真实来源后打日志；WebChat 再发一条结构化提示，ChatUI 原样显示。同一事件只通知一次。"""
    if event.get_extra("_event_stopped_notified"):
        return
    event.set_extra("_event_stopped_notified", True)
    md, handler_name = resolve_stop_source(event)
    outline = _stop_outline(event)
    if md is not None or handler_name:
        plugin = plugin_display_name(md)
        method = (handler_name or "").strip() or "未知"
        text = format_event_stopped_message(plugin, method, outline)
    else:
        plugin = ""
        method = ""
        text = format_unknown_stopped_message(outline)
    logger.info(f"{text}。")
    if event.get_platform_name() != "webchat":
        return
    await event.send(
        MessageChain(
            type="event_stopped",
            chain=[
                Json(
                    {
                        "text": text,
                        "plugin": plugin,
                        "method": method,
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
    # 事件已死不再执行钩子（与指令链路 star_request 的预检一致），
    # 只补一条真实来源的终止日志
    if event.is_stopped():
        await notify_event_stopped(event)
        return True
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
            await notify_event_stopped(event)
            return True

    return event.is_stopped()
