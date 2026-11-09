"""重要通知服务。

用于持久化需要管理员知晓或处理的系统事件（例如上下文压缩失败、插件报错、
聊天流程里未捕获的异常）。
与日志的区别：日志会被滚动刷掉、没人主动看就毫无察觉；重要通知会一直
残留，直到管理员在 WebUI 通知中心或通过 help 指令看到并手动标记已读。

行为约定：
- 同一 key 的未读通知聚合计数（count + 1），不重复堆积
- 聚合时标题与详情更新为「最近一次」的内容（同一插件换个群又报错时，
  详情里看到的是最新那次的群号/时间/调用栈）
- 已读必须显式调用 mark_read（打开面板不算已读）
- 已读后同一 key 再次发生会生成一条新通知（旧问题已确认，新发生是新问题）
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlmodel import select

from astrbot.core import db_helper, logger
from astrbot.core.db.po import ImportantNotification

# 详情长度上限：调用栈可能很长，超出部分截断，避免把数据库和通知面板撑爆
DETAIL_MAX_CHARS = 4000
# 调用栈长度上限（详情里单独限制，保证前面的会话信息不被截掉）
TRACEBACK_MAX_CHARS = 2800


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationService:
    """重要通知的写入与查询。"""

    async def notify(self, key: str, title: str, detail: str = "") -> None:
        """记录一条重要通知。

        同 key 存在未读通知时聚合（count+1、刷新详情与时间），否则新建。
        写入失败只记录日志，不影响调用方主流程。
        """
        try:
            async with db_helper.get_db() as session:
                existing = (
                    await session.execute(
                        select(ImportantNotification)
                        .where(ImportantNotification.key == key)
                        .where(ImportantNotification.read == False)  # noqa: E712
                        .order_by(ImportantNotification.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    existing.count += 1
                    existing.title = title
                    existing.detail = detail
                    existing.updated_at = _now()
                    session.add(existing)
                else:
                    session.add(
                        ImportantNotification(
                            key=key,
                            title=title,
                            detail=detail,
                        )
                    )
                await session.commit()
        except Exception:
            logger.error("写入重要通知失败 (key=%s)", key, exc_info=True)

    async def list_notifications(
        self, include_read: bool = True, limit: int = 100
    ) -> list[ImportantNotification]:
        """按时间倒序列出通知，未读在前。"""
        try:
            async with db_helper.get_db() as session:
                stmt = select(ImportantNotification).order_by(
                    ImportantNotification.read.asc(),
                    ImportantNotification.updated_at.desc(),
                )
                if not include_read:
                    stmt = stmt.where(ImportantNotification.read == False)  # noqa: E712
                stmt = stmt.limit(max(1, min(int(limit), 500)))
                return list((await session.execute(stmt)).scalars().all())
        except Exception:
            logger.error("读取重要通知失败", exc_info=True)
            return []

    async def unread_count(self) -> int:
        try:
            async with db_helper.get_db() as session:
                total = (
                    await session.execute(
                        select(func.count())
                        .select_from(ImportantNotification)
                        .where(ImportantNotification.read == False)  # noqa: E712
                    )
                ).scalar()
                return int(total or 0)
        except Exception:
            logger.error("统计未读重要通知失败", exc_info=True)
            return 0

    async def mark_read(self, ids: list[int]) -> int:
        """将指定 id 的通知标记为已读，返回实际标记数量。"""
        if not ids:
            return 0
        try:
            async with db_helper.get_db() as session:
                stmt = select(ImportantNotification).where(
                    ImportantNotification.id.in_([int(i) for i in ids]),
                    ImportantNotification.read == False,  # noqa: E712
                )
                items = list((await session.execute(stmt)).scalars().all())
                for item in items:
                    item.read = True
                    item.read_at = _now()
                    session.add(item)
                await session.commit()
                return len(items)
        except Exception:
            logger.error("标记重要通知已读失败", exc_info=True)
            return 0

    async def delete_notifications(
        self,
        ids: list[int] | None = None,
        clear_read: bool = False,
    ) -> int:
        """删除通知，返回删除条数。

        - 传 ids：删这些通知（不管已读与否）
        - clear_read=True：删掉所有已读通知（保留未读）
        """
        id_list = [int(i) for i in ids] if ids else []
        if not id_list and not clear_read:
            return 0
        try:
            async with db_helper.get_db() as session:
                stmt = select(ImportantNotification)
                if id_list:
                    stmt = stmt.where(ImportantNotification.id.in_(id_list))
                else:
                    stmt = stmt.where(ImportantNotification.read == True)  # noqa: E712
                items = list((await session.execute(stmt)).scalars().all())
                for item in items:
                    await session.delete(item)
                await session.commit()
                return len(items)
        except Exception:
            logger.error("删除重要通知失败", exc_info=True)
            return 0


notification_service = NotificationService()


# ---------------------------------------------------------------------------
# 异常通知：把聊天流程里的报错沉淀成重要通知
# ---------------------------------------------------------------------------


def _safe_text(source: Any) -> str:
    """尽量从 event 的属性/方法里取值，取不到或无值返回空串。"""
    try:
        value = source() if callable(source) else source
    except Exception:
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _truncate(text: str, limit: int) -> str:
    """超长文本中间截断（保留头尾，头尾都是调用栈里最有信息量的部分）。"""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    head_len = max(0, limit // 3)
    tail_len = max(0, limit - head_len)
    return f"{text[:head_len]}\n...（已省略 {omitted} 个字符）...\n{text[-tail_len:]}"


def _event_context_lines(event: Any) -> list[str]:
    """把消息事件里的会话信息摊平成几行：平台 / 群号 / 发送者 / 会话标识。"""
    if event is None:
        return []
    lines: list[str] = []
    platform_name = _safe_text(getattr(event, "get_platform_name", None))
    platform_id = _safe_text(getattr(event, "get_platform_id", None))
    if platform_name and platform_id and platform_id != platform_name:
        lines.append(f"平台：{platform_name}（{platform_id}）")
    elif platform_name or platform_id:
        lines.append(f"平台：{platform_name or platform_id}")
    group_id = _safe_text(getattr(event, "get_group_id", None))
    if group_id:
        lines.append(f"群号：{group_id}")
    sender_name = _safe_text(getattr(event, "get_sender_name", None))
    sender_id = _safe_text(getattr(event, "get_sender_id", None))
    if sender_name and sender_id:
        lines.append(f"发送者：{sender_name}（{sender_id}）")
    elif sender_id:
        lines.append(f"发送者：{sender_id}")
    # 会话标识（platform:消息类型:会话号）：排查时可直接拿它在 WebUI 定位会话
    umo = _safe_text(getattr(event, "unified_msg_origin", ""))
    if umo:
        lines.append(f"会话标识：{umo}")
    return lines


def build_error_detail(
    event: Any,
    fields: list[tuple[str, str]] | None = None,
    error: BaseException | None = None,
    traceback_text: str = "",
) -> str:
    """拼装一条异常通知的详情：时间 + 会话 + 自定义字段 + 异常 + 调用栈。"""
    lines: list[str] = [
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        *_event_context_lines(event),
    ]
    for label, value in fields or []:
        text = (value or "").strip()
        if text:
            lines.append(f"{label}：{text}")
    if error is not None:
        lines.append(f"异常类型：{type(error).__name__}")
        message = str(error).strip()
        if message:
            lines.append(f"异常信息：{_truncate(message, 600)}")
    trace = (traceback_text or "").strip()
    if trace:
        lines.extend(["", "--- 调用栈 ---", _truncate(trace, TRACEBACK_MAX_CHARS)])
    return _truncate("\n".join(lines), DETAIL_MAX_CHARS)


async def notify_plugin_error(
    event: Any,
    plugin_name: str,
    handler_name: str,
    error: BaseException,
    traceback_text: str = "",
) -> None:
    """插件处理消息时抛异常 → 重要通知（同一插件聚合计数）。"""
    try:
        name = (plugin_name or "").strip() or "未知插件"
        await notification_service.notify(
            key=f"plugin_error:{name}",
            title=f"插件异常：{name}",
            detail=build_error_detail(
                event,
                [("插件", name), ("处理函数", handler_name or "")],
                error,
                traceback_text,
            ),
        )
    except Exception:
        logger.error("写入插件异常通知失败 (plugin=%s)", plugin_name, exc_info=True)


async def notify_agent_error(
    event: Any,
    error: BaseException,
    traceback_text: str = "",
    model: str = "",
    provider: str = "",
    runner_type: str = "",
) -> None:
    """LLM/Agent 请求失败 → 重要通知（按提供方聚合计数）。"""
    try:
        provider = (provider or "").strip()
        model = (model or "").strip()
        key_suffix = provider or runner_type or "internal"
        label = model or provider or "内部 Agent"
        await notification_service.notify(
            key=f"agent_error:{key_suffix}",
            title=f"AI 请求异常：{label}",
            detail=build_error_detail(
                event,
                [
                    ("请求类型", runner_type or "内部 Agent"),
                    ("提供方", provider),
                    ("模型", model),
                ],
                error,
                traceback_text,
            ),
        )
    except Exception:
        logger.error("写入 AI 请求异常通知失败", exc_info=True)


async def notify_plugin_hook_error(
    event: Any,
    plugin_name: str,
    hook_name: str,
    error: BaseException,
    traceback_text: str = "",
) -> None:
    """插件事件钩子（on_llm_request / on_decorating_result 等）抛异常 → 重要通知。

    钩子异常在 pipeline 里是被 catch 掉只打日志的，不检查通知就会完全静默，
    所以单独走一个 key（同一插件聚合）。
    """
    try:
        name = (plugin_name or "").strip() or "未知插件"
        await notification_service.notify(
            key=f"plugin_hook_error:{name}",
            title=f"插件钩子异常：{name}",
            detail=build_error_detail(
                event,
                [("插件", name), ("钩子", hook_name or "")],
                error,
                traceback_text,
            ),
        )
    except Exception:
        logger.error("写入插件钩子异常通知失败 (plugin=%s)", plugin_name, exc_info=True)


async def notify_pipeline_error(
    event: Any,
    error: BaseException,
    traceback_text: str = "",
) -> None:
    """聊天流程未捕获的异常 → 重要通知（按异常类型聚合计数）。"""
    try:
        type_name = type(error).__name__
        await notification_service.notify(
            key=f"pipeline_uncaught:{type_name}",
            title=f"聊天流程异常：{type_name}",
            detail=build_error_detail(event, [], error, traceback_text),
        )
    except Exception:
        logger.error("写入聊天流程异常通知失败", exc_info=True)


async def notify_plugin_load_error(
    plugin_name: str,
    error: BaseException | None,
    traceback_text: str = "",
) -> None:
    """插件载入失败 → 重要通知（同一插件聚合计数）。"""
    try:
        name = (plugin_name or "").strip() or "未知插件"
        await notification_service.notify(
            key=f"plugin_load_error:{name}",
            title=f"插件加载失败：{name}",
            detail=build_error_detail(
                None, [("插件目录", name)], error, traceback_text
            ),
        )
    except Exception:
        logger.error("写入插件加载失败通知失败 (plugin=%s)", plugin_name, exc_info=True)
