"""限流忽略空消息验证（platform_settings.rate_limit.ignore_empty_message）。

验证行为：
- 消息链为空或仅含空字符串文本组件的消息（如 NapCat 的"正在输入"状态事件）
  不计入限流、不触发 stall
- 用户发送的空格等有字符的文本是真实消息，仍正常计数
- @ 等非文本组件存在时不算空消息
- 配置关闭后恢复旧行为：空消息也计数、也会触发 stall

运行：python tests/test_rate_limit_empty_message.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.message.components import At, Plain  # noqa: E402
from astrbot.core.pipeline.rate_limit_check.stage import RateLimitStage  # noqa: E402

SESSION = "session-1"


def make_event(chain):
    return SimpleNamespace(
        session_id=SESSION,
        message_obj=SimpleNamespace(message=chain),
    )


def make_stage(count=30, time=60, ignore_empty_message=True):
    stage = RateLimitStage()
    ctx = SimpleNamespace(
        astrbot_config={
            "platform_settings": {
                "rate_limit": {
                    "count": count,
                    "time": time,
                    "strategy": "stall",
                    "ignore_empty_message": ignore_empty_message,
                }
            }
        }
    )
    return stage, ctx


def test_empty_chain_not_counted():
    async def scenario():
        stage, ctx = make_stage()
        await stage.initialize(ctx)
        await stage.process(make_event([]))
        assert not stage.event_timestamps.get(SESSION), "空消息链不应计入限流"
        await stage.process(make_event([Plain(text="hello")]))
        assert len(stage.event_timestamps[SESSION]) == 1, "正常消息应计数"

    asyncio.run(scenario())
    print("test_empty_chain_not_counted: PASS")


def test_empty_string_text_not_counted_but_space_is_real_content():
    async def scenario():
        stage, ctx = make_stage()
        await stage.initialize(ctx)
        await stage.process(make_event([Plain(text="")]))
        assert not stage.event_timestamps.get(SESSION), "空字符串文本不应计入限流"
        await stage.process(make_event([Plain(text=" ")]))
        assert len(stage.event_timestamps[SESSION]) == 1, (
            "空格是用户真实发送的内容，应计入限流"
        )

    asyncio.run(scenario())
    print("test_empty_string_text_not_counted_but_space_is_real_content: PASS")


def test_non_text_components_counted():
    async def scenario():
        stage, ctx = make_stage()
        await stage.initialize(ctx)
        await stage.process(make_event([At(qq="123")]))
        assert len(stage.event_timestamps[SESSION]) == 1, (
            "@ 等非文本组件存在时不算空消息"
        )

    asyncio.run(scenario())
    print("test_non_text_components_counted: PASS")


def test_empty_message_does_not_stall():
    """计数已满时空消息到达：不应 stall 也不应计数（本次 bug 场景）。"""

    async def scenario():
        stage, ctx = make_stage(count=1)
        await stage.initialize(ctx)
        await stage.process(make_event([Plain(text="hi")]))
        assert len(stage.event_timestamps[SESSION]) == 1
        await asyncio.wait_for(
            stage.process(make_event([Plain(text="")])),
            timeout=1,
        )
        assert len(stage.event_timestamps[SESSION]) == 1, "空消息不应被计数"

    asyncio.run(scenario())
    print("test_empty_message_does_not_stall: PASS")


def test_disabled_config_keeps_old_behavior():
    """关闭配置后恢复旧行为：空消息计数，且队列满时会触发 stall。"""

    async def scenario():
        stage, ctx = make_stage(count=2, ignore_empty_message=False)
        await stage.initialize(ctx)
        await stage.process(make_event([Plain(text="hi")]))
        await stage.process(make_event([Plain(text="")]))
        assert len(stage.event_timestamps[SESSION]) == 2, (
            "关闭配置后空消息应计数（旧行为）"
        )
        try:
            await asyncio.wait_for(
                stage.process(make_event([Plain(text="")])),
                timeout=1,
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("关闭配置后空消息应触发 stall（旧行为）")

    asyncio.run(scenario())
    print("test_disabled_config_keeps_old_behavior: PASS")


if __name__ == "__main__":
    test_empty_chain_not_counted()
    test_empty_string_text_not_counted_but_space_is_real_content()
    test_non_text_components_counted()
    test_empty_message_does_not_stall()
    test_disabled_config_keeps_old_behavior()
    print("\n全部通过 ✓")
