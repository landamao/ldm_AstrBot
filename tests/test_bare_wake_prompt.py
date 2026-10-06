"""空唤醒（仅 @ 机器人/全体成员或仅唤醒前缀）正常走 LLM 管线验证。

背景：原先空艾特由 builtin_stars 的 handle_empty_mention 特殊处理——
60 秒等待窗口 + 绕开正常管线直接 request_llm，群聊上下文注入不生效；
关闭 empty_mention_waiting 后空艾特更是被 internal 阶段当空消息直接跳过。

改造后：
- handle_empty_mention 已删除，空艾特与普通消息一样走唤醒逻辑
- internal 阶段对空唤醒补一条 system_reminder 提示词后继续 LLM 请求，
  群聊上下文等 on_llm_request 注入机制照常生效
- 私聊纯空白消息、@ 别人等场景维持跳过，不被误判

运行：python tests/test_bare_wake_prompt.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.message.components import At, AtAll, Image, Plain  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    _BARE_WAKE_PROMPT,
    InternalAgentSubStage,
)

WAKE_PREFIX = "小助手"


def make_stage() -> InternalAgentSubStage:
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    stage.ctx = SimpleNamespace(astrbot_config={"wake_prefix": [WAKE_PREFIX]})
    return stage


class FakeEvent:
    def __init__(self, comps, is_wake=True, self_id="bot123", message_str=""):
        self.message_obj = SimpleNamespace(message=list(comps))
        self.is_wake = is_wake
        self.unified_msg_origin = "aiocqhttp:GroupMessage:456"
        self._self_id = self_id
        self.message_str = message_str

    def get_self_id(self) -> str:
        return self._self_id


def test_empty_mention_fills_prompt():
    """空艾特：补提示词，正常走 LLM。"""
    stage = make_stage()
    event = FakeEvent([At(qq="bot123")])
    assert stage._is_bare_wake(event) is True
    assert stage._fill_bare_wake_prompt(event) is True
    assert event.message_str == _BARE_WAKE_PROMPT


def test_at_all_fills_prompt():
    """空 @ 全体成员同样视为空唤醒。"""
    stage = make_stage()
    event = FakeEvent([AtAll()])
    assert stage._fill_bare_wake_prompt(event) is True


def test_wake_prefix_only_fills_prompt():
    """仅发唤醒前缀（前缀已被唤醒阶段从 message_str 移除）同样补提示词。"""
    stage = make_stage()
    event = FakeEvent([Plain(WAKE_PREFIX)], message_str="")
    assert stage._is_bare_wake(event) is True
    assert stage._fill_bare_wake_prompt(event) is True


def test_at_with_text_is_not_bare():
    """普通艾特（@ + 文本）不进空唤醒分支，走原有逻辑。"""
    stage = make_stage()
    event = FakeEvent([At(qq="bot123"), Plain("你好")], message_str="你好")
    assert stage._is_bare_wake(event) is False
    # message_str 非空时 fill 直接返回 False，message_str 不被改写
    assert stage._fill_bare_wake_prompt(event) is False
    assert event.message_str == "你好"


def test_at_other_person_is_not_bare():
    """@ 了别人不算空唤醒。"""
    stage = make_stage()
    event = FakeEvent([At(qq="bot123"), At(qq="other999")])
    assert stage._is_bare_wake(event) is False


def test_media_component_is_not_bare():
    """空艾特带图片属于有内容消息，不补提示词（走媒体处理）。"""
    stage = make_stage()
    event = FakeEvent([At(qq="bot123"), Image(file="x.png")])
    assert stage._is_bare_wake(event) is False


def test_private_blank_message_stays_skipped():
    """私聊纯空白消息维持跳过，不因空唤醒逻辑误触发 LLM。"""
    stage = make_stage()
    event = FakeEvent([Plain("   ")], message_str="")
    assert stage._is_bare_wake(event) is False
    assert stage._fill_bare_wake_prompt(event) is False


def test_not_wake_event_stays_skipped():
    """未唤醒的事件不补提示词。"""
    stage = make_stage()
    event = FakeEvent([At(qq="bot123")], is_wake=False)
    assert stage._fill_bare_wake_prompt(event) is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")
