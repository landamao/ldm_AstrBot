"""生图指令 /image"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult
from astrbot.api.message_components import Image as ImageComponent
from astrbot.api.message_components import Reply
from astrbot.core.star.filter.command import GreedyStr
from astrbot.core.tools.image_generation_tools import (
    _iter_image_generation_providers,
    _select_image_generation_provider,
)
from astrbot.core.utils.wake_prefix import 获取第一个唤醒词

NATIVE_PERMISSION_MESSAGE = (
    "你(ID: {sender_id})的权限不足以使用此指令。通过 {wake_prefix}sid 获取 ID 并请管理员添加。"
)


def _截断提示词(text: str, limit: int = 20) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


@dataclass
class ImageTask:
    task_id: str
    user_id: str
    umo: str
    prompt: str
    model_id: str
    model_name: str
    created_at: float
    ref_count: int = 0
    asyncio_task: asyncio.Task | None = None


class ImageCommands:
    """聊天侧生图指令。"""

    def __init__(self, context: star.Context, config=None) -> None:
        self.context = context
        self.config = config
        self._lock = asyncio.Lock()
        self._tasks: dict[str, ImageTask] = {}
        self._user_tasks: dict[str, set[str]] = {}
        self._cooldown_at: dict[str, float] = {}


    @staticmethod
    def build_group_help_message() -> str:
        """仅输入 /image 或未知子指令时展示的帮助。"""
        前缀 = 获取第一个唤醒词()
        return "\n".join(
            [
                f"生图  {前缀}image",
                "",
                "用法：",
                f"{前缀}image star <提示词>",
                "  使用默认生图模型生成图片，返回任务ID",
                f"  也可写成 {前缀}image start <提示词>",
                "  消息带图或引用图片消息时，图片作为参考图进行图生图",
                "",
                f"{前缀}image model <模型ID> <提示词>",
                "  指定生图模型生成图片，返回任务ID",
                "",
                f"{前缀}image stop <任务ID>",
                "  停止生图任务。普通用户只能停止自己的任务，管理员可停止所有人的任务",
                "",
                f"{前缀}image mlist",
                "  列出可用生图模型",
                "",
                f"{前缀}image tlist",
                "  列出自己正在进行的生图任务",
                "",
                f"{前缀}image tlist <用户ID>",
                "  管理员查看指定用户正在进行的生图任务",
                "",
                f"{前缀}image tlist all",
                "  管理员查看所有用户正在进行的生图任务",
                "",
                f"{前缀}image help",
                "  查看本帮助",
            ]
        )

    def _生图设置(self) -> dict:
        cfg = self.config or {}
        settings = cfg.get("生图设置")
        if not isinstance(settings, dict):
            return {}
        return settings

    def admin_only(self) -> bool:
        """仅允许管理员使用。配置缺失按开启处理。"""
        settings = self._生图设置()
        if "仅允许管理员使用" not in settings:
            return True
        return bool(settings.get("仅允许管理员使用"))

    def _image_cfg(self) -> tuple[bool, float]:
        """读取生图指令限制。缺失按限制处理：单任务默认开启，冷却缺失按 0。"""
        settings = self._生图设置()
        if "生图单任务限制" not in settings:
            单任务 = True
        else:
            单任务 = bool(settings.get("生图单任务限制"))
        raw = settings.get("生图冷却时间", 0)
        try:
            冷却 = float(raw)
        except (TypeError, ValueError):
            冷却 = 0.0
        if 冷却 < 0:
            冷却 = 0.0
        return 单任务, 冷却

    def _reply(self, event: AstrMessageEvent, text: str) -> None:
        event.set_result(MessageEventResult().message(text).use_t2i(False))

    async def _load_reference_images(
        self,
        event: AstrMessageEvent,
    ) -> list[tuple[bytes, str]]:
        """提取消息里的参考图并读成 (bytes, mime) 列表。

        顺序：直接上传的图在前，引用消息里的图在后。每张失败跳过并记日志，
        最多取 10 张。
        """
        comps = list(event.message_obj.message or [])
        images: list[ImageComponent] = []
        for comp in comps:
            if isinstance(comp, ImageComponent):
                images.append(comp)
            elif isinstance(comp, Reply) and comp.chain:
                for reply_comp in comp.chain:
                    if isinstance(reply_comp, ImageComponent):
                        images.append(reply_comp)
        refs: list[tuple[bytes, str]] = []
        for index, image_comp in enumerate(images[:10]):
            try:
                path = await image_comp.convert_to_file_path()
                with open(path, "rb") as f:
                    data = f.read()
            except Exception as exc:  # noqa: BLE001
                logger.warning("生图参考图读取失败（第 %s 张）: %s", index + 1, exc)
                continue
            mime = "image/png" if ".png" in str(path).lower() else "image/jpeg"
            refs.append((data, mime))
        return refs

    def _deny_if_admin_only(self, event: AstrMessageEvent) -> bool:
        """开启仅管理员时拦截普通用户，提示与框架原生一致。返回 True 表示已拦截。"""
        if not self.admin_only() or event.is_admin():
            return False
        sender_id = event.get_sender_id()
        self._reply(
            event,
            NATIVE_PERMISSION_MESSAGE.format(
                sender_id=sender_id,
                wake_prefix=获取第一个唤醒词(),
            ),
        )
        logger.info(
            "触发 /image 时, 用户(ID=%s) 权限不足。",
            sender_id,
        )
        return True

    def _format_models(self) -> str:
        providers = _iter_image_generation_providers(self.context)
        if not providers:
            return (
                "没有可用的生图模型。"
                "请先在 WebUI「模型提供商-生图」中配置并启用生图模型。"
            )
        default = self.context.get_using_image_generation_provider()
        default_id = ""
        if default is not None:
            default_id = str(default.provider_config.get("id", "") or "")
        lines = [f"可用生图模型  共 {len(providers)} 个", ""]
        for prov in providers:
            provider_id = str(prov.provider_config.get("id", "") or "")
            model = str(prov.provider_config.get("model", "") or "")
            marker = "（默认）" if provider_id and provider_id == default_id else ""
            lines.append(f"模型ID: {provider_id}{marker}")
            if model:
                lines.append(f"模型: {model}")
            lines.append("")
        lines.append(
            f"生成时把上面的「模型ID」作为 {获取第一个唤醒词()}image model 的模型ID。"
        )
        return "\n".join(lines).rstrip()

    def _format_task(self, task: ImageTask, *, show_user: bool) -> str:
        lines = [f"任务ID: {task.task_id}"]
        if show_user:
            lines.append(f"用户ID: {task.user_id}")
        if task.model_id:
            lines.append(f"模型ID: {task.model_id}")
        if task.model_name:
            lines.append(f"模型: {task.model_name}")
        prompt = _截断提示词(task.prompt)
        if prompt:
            lines.append(f"提示词: {prompt}")
        return "\n".join(lines)

    def _list_tasks(self, tasks: list[ImageTask], *, show_user: bool) -> str:
        if not tasks:
            return "当前没有进行中的生图任务。"
        blocks = [f"进行中的生图任务  共 {len(tasks)} 个", ""]
        for task in tasks:
            blocks.append(self._format_task(task, show_user=show_user))
            blocks.append("")
        return "\n".join(blocks).rstrip()

    def _user_running_ids(self, user_id: str) -> set[str]:
        return set(self._user_tasks.get(user_id) or ())

    async def _check_limits(
        self,
        event: AstrMessageEvent,
        user_id: str,
    ) -> str:
        """普通用户的单任务 / 冷却检查。空字符串表示通过。"""
        if event.is_admin():
            return ""
        单任务, 冷却 = self._image_cfg()
        now = time.time()
        if 单任务 and self._user_running_ids(user_id):
            running = sorted(self._user_running_ids(user_id))
            ids = "、".join(f"「{tid}」" for tid in running)
            return (
                "当前已有进行中的生图任务，请等待完成后再发起。\n"
                f"任务ID: {ids}"
            )
        last = self._cooldown_at.get(user_id)
        if 冷却 > 0 and last is not None:
            remain = last + 冷却 - now
            if remain > 0:
                sec = int(remain) if remain >= 1 else 1
                return f"生图冷却中，请 {sec} 秒后再试。"
        return ""

    async def _register_task(
        self,
        event: AstrMessageEvent,
        prompt: str,
        model_id: str,
        model_name: str,
        ref_count: int = 0,
    ) -> tuple[ImageTask | None, str]:
        user_id = str(event.get_sender_id() or "")
        async with self._lock:
            err = await self._check_limits(event, user_id)
            if err:
                return None, err
            task_id = uuid.uuid4().hex[:8]
            while task_id in self._tasks:
                task_id = uuid.uuid4().hex[:8]
            task = ImageTask(
                task_id=task_id,
                user_id=user_id,
                umo=event.unified_msg_origin,
                prompt=prompt,
                model_id=model_id,
                model_name=model_name,
                created_at=time.time(),
                ref_count=ref_count,
            )
            self._tasks[task_id] = task
            self._user_tasks.setdefault(user_id, set()).add(task_id)
            单任务, 冷却 = self._image_cfg()
            if 冷却 > 0 and not 单任务 and not event.is_admin():
                self._cooldown_at[user_id] = time.time()
        return task, ""

    async def _finish_task(
        self,
        task: ImageTask,
        *,
        start_cooldown: bool,
        cooldown: float,
        is_admin: bool,
    ) -> None:
        async with self._lock:
            self._tasks.pop(task.task_id, None)
            ids = self._user_tasks.get(task.user_id)
            if ids:
                ids.discard(task.task_id)
                if not ids:
                    self._user_tasks.pop(task.user_id, None)
            if start_cooldown and cooldown > 0 and not is_admin:
                self._cooldown_at[task.user_id] = time.time()

    async def _run_generate(
        self,
        task: ImageTask,
        provider,
        reference_images: list[tuple[bytes, str]],
        *,
        start_cooldown_on_done: bool,
        cooldown: float,
        is_admin: bool,
    ) -> None:
        开始时间 = time.time()
        try:
            images = await provider.generate_image(
                task.prompt,
                n=1,
                image=reference_images or None,
            )
        except asyncio.CancelledError:
            logger.info("生图任务已停止：任务ID=%s 用户ID=%s", task.task_id, task.user_id)
            await self._finish_task(
                task,
                start_cooldown=start_cooldown_on_done,
                cooldown=cooldown,
                is_admin=is_admin,
            )
            try:
                await self.context.send_message(
                    task.umo,
                    MessageChain()
                    .message(f"已停止生图任务「{task.task_id}」。")
                    .use_t2i(False),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("发送生图停止提示失败: %s", exc)
            return
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "生图任务失败：任务ID=%s 耗时=%.1f秒 %s",
                task.task_id,
                time.time() - 开始时间,
                exc,
            )
            await self._finish_task(
                task,
                start_cooldown=start_cooldown_on_done,
                cooldown=cooldown,
                is_admin=is_admin,
            )
            try:
                await self.context.send_message(
                    task.umo,
                    MessageChain()
                    .message(f"生图失败\n任务ID: {task.task_id}\n原因: {exc}")
                    .use_t2i(False),
                )
            except Exception as send_exc:  # noqa: BLE001
                logger.warning("发送生图失败提示失败: %s", send_exc)
            return

        await self._finish_task(
            task,
            start_cooldown=start_cooldown_on_done,
            cooldown=cooldown,
            is_admin=is_admin,
        )
        lines = [
            "生图完成",
            f"任务ID: {task.task_id}",
            f"耗时: {time.time() - 开始时间:.1f} 秒",
        ]
        if task.ref_count:
            lines.append(f"参考图: {task.ref_count} 张")
        if task.model_id:
            lines.append(f"模型ID: {task.model_id}")
        if task.model_name:
            lines.append(f"模型: {task.model_name}")
        chain = MessageChain().message("\n".join(lines)).use_t2i(False)
        for image in images:
            chain.base64_image(image.base64_data)
        try:
            await self.context.send_message(task.umo, chain)
        except Exception as exc:  # noqa: BLE001
            logger.error("发送生图结果失败：任务ID=%s %s", task.task_id, exc)
        else:
            logger.info(
                "生图任务完成：任务ID=%s 用户ID=%s 模型ID=%s 图片=%s 耗时=%.1f秒",
                task.task_id,
                task.user_id,
                task.model_id,
                len(images),
                time.time() - 开始时间,
            )

    async def _start(
        self,
        event: AstrMessageEvent,
        prompt: str,
        model: str,
    ) -> None:
        if self._deny_if_admin_only(event):
            return
        prompt = (prompt or "").strip()
        if not prompt:
            self._reply(
                event,
                f"使用方法：{获取第一个唤醒词()}image star <提示词> 或 {获取第一个唤醒词()}image model <模型ID> <提示词>",
            )
            return
        provider, error = _select_image_generation_provider(self.context, model)
        if provider is None:
            self._reply(event, error)
            return
        model_id = str(provider.provider_config.get("id", "") or "")
        model_name = str(provider.provider_config.get("model", "") or "")
        reference_images = await self._load_reference_images(event)
        task, err = await self._register_task(
            event, prompt, model_id, model_name, ref_count=len(reference_images)
        )
        if task is None:
            self._reply(event, err)
            return
        单任务, 冷却 = self._image_cfg()
        is_admin = event.is_admin()
        asyncio_task = asyncio.create_task(
            self._run_generate(
                task,
                provider,
                reference_images,
                start_cooldown_on_done=单任务,
                cooldown=冷却,
                is_admin=is_admin,
            )
        )
        task.asyncio_task = asyncio_task
        logger.info(
            "生图任务已创建：任务ID=%s 用户ID=%s 模型ID=%s 参考图=%s 提示词=%s",
            task.task_id,
            task.user_id,
            model_id,
            len(reference_images),
            _截断提示词(prompt, 80),
        )
        lines = ["已开始生图", f"任务ID: {task.task_id}"]
        if reference_images:
            lines.append(f"参考图: {len(reference_images)} 张")
        if model_id:
            lines.append(f"模型ID: {model_id}")
        if model_name:
            lines.append(f"模型: {model_name}")
        wake_prefix = 获取第一个唤醒词()
        lines.append(f"如需停止请发送\n{wake_prefix}image stop {task.task_id}")
        self._reply(event, "\n".join(lines))

    async def star(self, event: AstrMessageEvent, prompt: GreedyStr | str = "") -> None:
        await self._start(event, str(prompt or ""), "")

    async def model(
        self,
        event: AstrMessageEvent,
        model_id: str = "",
        prompt: GreedyStr | str = "",
    ) -> None:
        if self._deny_if_admin_only(event):
            return
        model_id = (model_id or "").strip()
        prompt = str(prompt or "").strip()
        if not model_id and not prompt:
            self._reply(event, f"使用方法：{获取第一个唤醒词()}image model <模型ID> <提示词>")
            return
        if not model_id:
            self._reply(event, f"请输入模型ID。使用方法：{获取第一个唤醒词()}image model <模型ID> <提示词>")
            return
        if not prompt:
            self._reply(event, f"请输入提示词。使用方法：{获取第一个唤醒词()}image model <模型ID> <提示词>")
            return
        await self._start(event, prompt, model_id)

    async def stop(self, event: AstrMessageEvent, task_id: str = "") -> None:
        if self._deny_if_admin_only(event):
            return
        task_id = (task_id or "").strip()
        if not task_id:
            self._reply(event, f"使用方法：{获取第一个唤醒词()}image stop <任务ID>")
            return
        user_id = str(event.get_sender_id() or "")
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                self._reply(event, f"生图任务「{task_id}」不存在或已结束。")
                return
            if task.user_id != user_id and not event.is_admin():
                self._reply(event, "只能停止自己的生图任务。")
                return
            asyncio_task = task.asyncio_task
        if asyncio_task is None or asyncio_task.done():
            self._reply(event, f"生图任务「{task_id}」不存在或已结束。")
            return
        asyncio_task.cancel()
        logger.info(
            "停止生图任务：任务ID=%s 操作者=%s 任务所属=%s",
            task_id,
            user_id,
            task.user_id,
        )
        self._reply(event, f"已请求停止生图任务「{task_id}」。")

    async def mlist(self, event: AstrMessageEvent) -> None:
        if self._deny_if_admin_only(event):
            return
        self._reply(event, self._format_models())

    async def tlist(self, event: AstrMessageEvent, user_id: str = "") -> None:
        if self._deny_if_admin_only(event):
            return
        arg = (user_id or "").strip()
        sender = str(event.get_sender_id() or "")
        is_admin = event.is_admin()
        async with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda item: item.created_at)
        if not arg:
            own = [item for item in tasks if item.user_id == sender]
            self._reply(event, self._list_tasks(own, show_user=False))
            return
        if not is_admin:
            self._reply(event, "你的权限不足以查看其他用户的生图任务。")
            return
        if arg.lower() == "all":
            self._reply(event, self._list_tasks(tasks, show_user=True))
            return
        filtered = [item for item in tasks if item.user_id == arg]
        if not filtered:
            self._reply(event, f"用户「{arg}」没有进行中的生图任务。")
            return
        self._reply(event, self._list_tasks(filtered, show_user=True))

    async def help(self, event: AstrMessageEvent) -> None:
        if self._deny_if_admin_only(event):
            return
        self._reply(event, self.build_group_help_message())
