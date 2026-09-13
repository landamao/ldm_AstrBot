"""/image 指令：任务ID、权限、单任务限制、冷却计时点。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.builtin_stars.builtin_commands.commands.image import (  # noqa: E402
    ImageCommands,
    _截断提示词,
)


class FakeEvent:
    def __init__(self, sender_id: str, *, admin: bool = False) -> None:
        self._sender_id = sender_id
        self.unified_msg_origin = f"test:FriendMessage:{sender_id}"
        self.role = "admin" if admin else "member"
        self.result = None
        # 图生图参考图提取（_load_reference_images）会读消息段；默认空消息
        self.message_obj = SimpleNamespace(message=[])

    def get_sender_id(self) -> str:
        return self._sender_id

    def is_admin(self) -> bool:
        return self.role == "admin"

    def set_result(self, result) -> None:
        self.result = result


def _result_text(event: FakeEvent) -> str:
    return event.result.get_plain_text()


def _cmds(
    single_task: bool = True,
    cooldown: float = 0,
    admin_only: bool = False,
) -> ImageCommands:
    context = MagicMock()
    context.send_message = AsyncMock(return_value=True)
    config = {
        "生图设置": {
            "仅允许管理员使用": admin_only,
            "生图单任务限制": single_task,
            "生图冷却时间": cooldown,
        }
    }
    return ImageCommands(context, config)


def _provider(provider_id: str = "seedream", model: str = "seedream-4") -> MagicMock:
    prov = MagicMock()
    prov.provider_config = {"id": provider_id, "model": model}
    return prov


def test_截断提示词超过20字用省略号():
    assert _截断提示词("短词") == "短词"
    assert _截断提示词("一二三四五六七八九十一二三四五六七八九十") == (
        "一二三四五六七八九十一二三四五六七八九十"
    )
    assert _截断提示词("一二三四五六七八九十一二三四五六七八九十一") == (
        "一二三四五六七八九十一二三四五六七八九十..."
    )


def test_help文案不以斜杠开头():
    text = ImageCommands.build_group_help_message()
    assert text.startswith("生图  /image")
    assert "用法：" in text
    assert text.count("用法：") == 1
    assert "使用方法：" not in text
    assert "/image star <提示词>" in text
    assert "也可写成 /image start <提示词>" in text


def test_mlist字段分行并标模型ID():
    async def _run():
        cmds = _cmds()
        p1 = _provider("seedream", "doubao-seedream-4-0")
        p2 = _provider("gpt-image", "gpt-image-1")
        event = FakeEvent("u1")
        with (
            patch(
                "astrbot.builtin_stars.builtin_commands.commands.image._iter_image_generation_providers",
                return_value=[p1, p2],
            ),
            patch.object(
                cmds.context,
                "get_using_image_generation_provider",
                return_value=p1,
            ),
        ):
            await cmds.mlist(event)
        text = _result_text(event)
        assert "模型ID: seedream（默认）" in text
        assert "模型: doubao-seedream-4-0" in text
        assert "模型ID: gpt-image" in text

    asyncio.run(_run())


def test_普通用户不能停别人的任务管理员可以():
    async def _run():
        cmds = _cmds()
        started = asyncio.Event()

        async def slow(_prompt, n=1, image=None):
            started.set()
            await asyncio.sleep(30)
            return []

        provider = _provider()
        provider.generate_image = slow
        user = FakeEvent("100")
        other = FakeEvent("200")
        admin = FakeEvent("999", admin=True)
        with patch(
            "astrbot.builtin_stars.builtin_commands.commands.image._select_image_generation_provider",
            return_value=(provider, ""),
        ):
            await cmds.star(user, "一只猫")
            task_id = list(cmds._tasks)[0]
            await started.wait()
            await cmds.stop(other, task_id)
            assert "只能停止自己的生图任务" in _result_text(other)
            assert task_id in cmds._tasks
            await cmds.stop(admin, task_id)
            assert "已请求停止" in _result_text(admin)
            await asyncio.sleep(0.05)
            assert task_id not in cmds._tasks

    asyncio.run(_run())


def test_单任务开启时普通用户必须等任务结束():
    async def _run():
        cmds = _cmds(single_task=True, cooldown=0)
        started = asyncio.Event()

        async def slow(_prompt, n=1, image=None):
            started.set()
            await asyncio.sleep(30)
            return []

        provider = _provider()
        provider.generate_image = slow
        user = FakeEvent("100")
        with patch(
            "astrbot.builtin_stars.builtin_commands.commands.image._select_image_generation_provider",
            return_value=(provider, ""),
        ):
            await cmds.star(user, "第一张")
            await started.wait()
            await cmds.star(user, "第二张")
            assert "当前已有进行中的生图任务" in _result_text(user)
            assert len(cmds._tasks) == 1
            task_id = list(cmds._tasks)[0]
            await cmds.stop(user, task_id)
            await asyncio.sleep(0.05)
            assert not cmds._tasks
            await cmds.star(user, "第三张")
            assert "已开始生图" in _result_text(user)
            assert len(cmds._tasks) == 1
            leftover = list(cmds._tasks.values())[0]
            leftover.asyncio_task.cancel()
            await asyncio.sleep(0.05)

    asyncio.run(_run())


def test_单任务开启时冷却在任务结束后计时():
    async def _run():
        cmds = _cmds(single_task=True, cooldown=10)
        started = asyncio.Event()

        async def slow(_prompt, n=1, image=None):
            started.set()
            await asyncio.sleep(30)
            return []

        provider = _provider()
        provider.generate_image = slow
        user = FakeEvent("100")
        with patch(
            "astrbot.builtin_stars.builtin_commands.commands.image._select_image_generation_provider",
            return_value=(provider, ""),
        ):
            await cmds.star(user, "第一张")
            await started.wait()
            assert user.get_sender_id() not in cmds._cooldown_at
            task_id = list(cmds._tasks)[0]
            await cmds.stop(user, task_id)
            await asyncio.sleep(0.05)
            assert user.get_sender_id() in cmds._cooldown_at
            await cmds.star(user, "第二张")
            assert "生图冷却中" in _result_text(user)

    asyncio.run(_run())


def test_单任务关闭时冷却在任务开始时计时():
    async def _run():
        cmds = _cmds(single_task=False, cooldown=10)
        started = asyncio.Event()

        async def slow(_prompt, n=1, image=None):
            started.set()
            await asyncio.sleep(30)
            return []

        provider = _provider()
        provider.generate_image = slow
        user = FakeEvent("100")
        with patch(
            "astrbot.builtin_stars.builtin_commands.commands.image._select_image_generation_provider",
            return_value=(provider, ""),
        ):
            await cmds.star(user, "第一张")
            await started.wait()
            assert user.get_sender_id() in cmds._cooldown_at
            await cmds.star(user, "第二张")
            assert "生图冷却中" in _result_text(user)
            leftover = list(cmds._tasks.values())[0]
            leftover.asyncio_task.cancel()
            await asyncio.sleep(0.05)

    asyncio.run(_run())


def test_管理员不受单任务和冷却限制():
    async def _run():
        cmds = _cmds(single_task=True, cooldown=10)
        started = asyncio.Event()
        count = 0

        async def slow(_prompt, n=1, image=None):
            nonlocal count
            count += 1
            if count == 1:
                started.set()
            await asyncio.sleep(30)
            return []

        provider = _provider()
        provider.generate_image = slow
        admin = FakeEvent("999", admin=True)
        with patch(
            "astrbot.builtin_stars.builtin_commands.commands.image._select_image_generation_provider",
            return_value=(provider, ""),
        ):
            await cmds.star(admin, "第一张")
            await started.wait()
            await cmds.star(admin, "第二张")
            assert "已开始生图" in _result_text(admin)
            assert len(cmds._tasks) == 2
            for task in list(cmds._tasks.values()):
                task.asyncio_task.cancel()
            await asyncio.sleep(0.05)

    asyncio.run(_run())


def test_tlist权限与提示词截断():
    async def _run():
        cmds = _cmds()
        long_prompt = "一二三四五六七八九十一二三四五六七八九十一"
        task = SimpleNamespace(
            task_id="abcd1234",
            user_id="100",
            umo="x",
            prompt=long_prompt,
            model_id="seedream",
            model_name="seedream-4",
            created_at=1.0,
            asyncio_task=None,
        )
        cmds._tasks["abcd1234"] = task
        cmds._user_tasks["100"] = {"abcd1234"}
        own = FakeEvent("100")
        other = FakeEvent("200")
        admin = FakeEvent("999", admin=True)
        await cmds.tlist(own, "")
        text = _result_text(own)
        assert "abcd1234" in text
        assert "一二三四五六七八九十一二三四五六七八九十..." in text
        assert "用户ID:" not in text
        await cmds.tlist(other, "")
        assert "当前没有进行中的生图任务" in _result_text(other)
        await cmds.tlist(other, "100")
        assert "权限不足以查看" in _result_text(other)
        await cmds.tlist(admin, "100")
        admin_text = _result_text(admin)
        assert "用户ID: 100" in admin_text
        await cmds.tlist(admin, "all")
        assert "abcd1234" in _result_text(admin)

    asyncio.run(_run())


def test_缺参帮助不以斜杠开头():
    async def _run():
        cmds = _cmds()
        event = FakeEvent("100")
        await cmds.star(event, "")
        assert _result_text(event).startswith("使用方法：")
        await cmds.model(event, "", "")
        assert _result_text(event).startswith("使用方法：")
        await cmds.stop(event, "")
        assert _result_text(event).startswith("使用方法：")

    asyncio.run(_run())


def test_配置缺失按限制处理():
    cmds = ImageCommands(MagicMock(), None)
    单任务, 冷却 = cmds._image_cfg()
    assert 单任务 is True
    assert 冷却 == 0.0
    cmds = ImageCommands(MagicMock(), {"生图设置": {}})
    单任务, 冷却 = cmds._image_cfg()
    assert 单任务 is True
    assert 冷却 == 0.0


def test_默认生图模型读当前配置不读启动缓存():
    from astrbot.core.provider.provider import Provider
    from astrbot.core.star.context import Context

    p1 = MagicMock(spec=Provider)
    p1.provider_config = {"id": "old-model"}
    p2 = MagicMock(spec=Provider)
    p2.provider_config = {"id": "new-model"}
    ctx = Context.__new__(Context)
    ctx.provider_manager = SimpleNamespace(
        provider_settings={"default_image_generation_provider_id": "old-model"},
        inst_map={"old-model": p1, "new-model": p2},
        image_generation_provider_insts=[p1, p2],
    )
    ctx.get_config = MagicMock(
        return_value={
            "provider_settings": {
                "default_image_generation_provider_id": "new-model",
            }
        }
    )
    assert ctx.get_using_image_generation_provider() is p2


def test_仅管理员使用拦截普通用户():
    async def _run():
        cmds = _cmds(admin_only=True)
        user = FakeEvent("100")
        admin = FakeEvent("999", admin=True)
        await cmds.mlist(user)
        text = _result_text(user)
        assert "权限不足以使用此指令" in text
        assert "ID: 100" in text
        assert "通过 /sid 获取 ID" in text
        with patch(
            "astrbot.builtin_stars.builtin_commands.commands.image._iter_image_generation_providers",
            return_value=[],
        ):
            await cmds.mlist(admin)
        assert "没有可用的生图模型" in _result_text(admin)

    asyncio.run(_run())


def test_关闭仅管理员后普通用户可用():
    async def _run():
        cmds = _cmds(admin_only=False)
        user = FakeEvent("100")
        with patch(
            "astrbot.builtin_stars.builtin_commands.commands.image._iter_image_generation_providers",
            return_value=[],
        ):
            await cmds.mlist(user)
        assert "没有可用的生图模型" in _result_text(user)

    asyncio.run(_run())
