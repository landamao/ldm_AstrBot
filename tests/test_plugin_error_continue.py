"""验证「插件报错不中断事件」配置的两种行为。

运行：~/ldmbot/.venv/Scripts/python tests/test_plugin_error_continue.py
也可用 pytest 收集（async 用例已带 pytest.mark.asyncio 标记）。
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.pipeline.bootstrap import (  # noqa: E402
    ensure_builtin_stages_registered,
)

# star_request 与 process_stage.stage 互相引用，须按应用真实入口先注册内置 stage
ensure_builtin_stages_registered()  # noqa: E402

from astrbot.core.pipeline.process_stage.method.star_request import (  # noqa: E402
    StarRequestSubStage,
)
from astrbot.core.star.star import star_map  # noqa: E402


class FakeEvent:
    def __init__(self, handlers, is_at_or_wake=False):
        self._extras = {
            "activated_handlers": handlers,
            "handlers_parsed_params": {},
        }
        self.is_at_or_wake_command = is_at_or_wake
        self._stopped = False
        self._result = None
        self.unified_msg_origin = "test:session"
        self.plugins_name = None  # None 等价于 "*"，全部插件可用
        self.set_result_calls = []
        self.stop_called = False

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def is_stopped(self):
        return self._stopped

    def stop_event(self):
        self._stopped = True
        self.stop_called = True

    def set_result(self, result):
        self.set_result_calls.append(result)
        self._result = result

    def get_result(self):
        return self._result

    def clear_result(self):
        self._result = None


def make_stage(plugin_error_continue):
    ctx = SimpleNamespace(
        astrbot_config={
            "provider_settings": {"prompt_prefix": "", "identifier": ""},
            "plugin_error_continue": plugin_error_continue,
        },
    )
    stage = StarRequestSubStage()
    stage.ctx = ctx
    return stage


def register_plugin(monkey_run):
    """往全局 star_map 注册测试插件元数据，返回 (bad, good) 两个 handler 元数据。"""

    async def bad_handler(event):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    async def good_handler(event):
        monkey_run.append("good")
        yield

    star_map["test_plugin_error_continue_mod"] = SimpleNamespace(name="测试插件")
    bad = SimpleNamespace(
        handler_module_path="test_plugin_error_continue_mod",
        handler_full_name="test_plugin_error_continue_mod.bad_handler",
        handler_name="bad_handler",
        handler=bad_handler,
    )
    good = SimpleNamespace(
        handler_module_path="test_plugin_error_continue_mod",
        handler_full_name="test_plugin_error_continue_mod.good_handler",
        handler_name="good_handler",
        handler=good_handler,
    )
    return bad, good


def cleanup_plugin():
    star_map.pop("test_plugin_error_continue_mod", None)


@pytest.mark.asyncio
async def test_error_stops_event_by_default():
    """默认关闭：报错后 stop_event，后续 handler 不执行。"""
    run = []
    bad, good = register_plugin(run)
    stage = make_stage(False)
    event = FakeEvent([bad, good], is_at_or_wake=True)
    replies = [r async for r in stage.process(event)]

    assert event.stop_called is True
    assert run == []  # good handler 被 break 跳过
    # 唤醒消息仍回复错误文案
    assert len(event.set_result_calls) == 1
    assert "boom" in event.set_result_calls[0].chain[0].text
    assert "测试插件" in event.set_result_calls[0].chain[0].text
    assert len(replies) == 1  # 错误回复的 yield
    cleanup_plugin()
    print("test_error_stops_event_by_default: PASS")


@pytest.mark.asyncio
async def test_error_continues_when_enabled():
    """开启配置：报错不中断，后续 handler 继续执行，事件未停止。"""
    run = []
    bad, good = register_plugin(run)
    stage = make_stage(True)
    event = FakeEvent([bad, good], is_at_or_wake=True)
    replies = [r async for r in stage.process(event)]

    assert event.stop_called is False
    assert event.is_stopped() is False
    assert run == ["good"]
    # 继续模式不发错误文案：文案发送会置位 _has_send_oper，导致唤醒消息的
    # LLM 兜底请求被 ProcessStage 跳过；事件结果保持为空。
    assert event.set_result_calls == []
    assert len(replies) == 1  # 仅 good handler 的 yield
    cleanup_plugin()
    print("test_error_continues_when_enabled: PASS")


@pytest.mark.asyncio
async def test_error_continues_non_wake_no_reply():
    """非唤醒消息：开启配置报错继续，且不回复错误文案。"""
    run = []
    bad, good = register_plugin(run)
    stage = make_stage(True)
    event = FakeEvent([bad, good], is_at_or_wake=False)
    replies = [r async for r in stage.process(event)]

    assert event.stop_called is False
    assert run == ["good"]
    assert event.set_result_calls == []
    assert len(replies) == 1  # 仅 good handler 的 yield
    cleanup_plugin()
    print("test_error_continues_non_wake_no_reply: PASS")


async def main():
    await test_error_stops_event_by_default()
    await test_error_continues_when_enabled()
    await test_error_continues_non_wake_no_reply()
    print("\n全部通过 ✓")


if __name__ == "__main__":
    asyncio.run(main())
