"""非纯文本回复的历史落库回归测试（TTS 插件替换文本后上下文丢失）。

背景：LLMResponse.completion_text 是派生属性——只要 result_chain 里有内容，
它就等于 result_chain.get_plain_text()。TTS 插件在 on_llm_response 里把
result_chain 换成「只有 Record 的链」后，completion_text 读成空字符串，
_save_to_history 据此判定「LLM 响应为空」直接 return，导致该轮的用户消息和
回复都没有落库，下一轮 LLM 失忆（表现为「转语音之后上下文丢失」）。

验证行为：
- result_chain 只有 Record（无 Plain）时，本轮仍要落库，且 assistant 正文为原文
- result_chain 为空 / 无响应时，仍然不落库（不制造空轮次）
- 正常纯文本回复照常落库

运行：python tests/test_history_save_non_text_reply.py
"""

import asyncio
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.agent.message import Message, TextPart  # noqa: E402
from astrbot.core.db.po import Conversation  # noqa: E402
from astrbot.core.message.components import Plain, Record  # noqa: E402
from astrbot.core.message.message_event_result import MessageChain  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    InternalAgentSubStage,
)
from astrbot.core.provider.entities import LLMResponse, ProviderRequest  # noqa: E402

UMO = "NapCat:GroupMessage:123456"


class FakeEvent:
    def __init__(self):
        self.unified_msg_origin = UMO
        self._extras = {}

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)


class FakeConvManager:
    def __init__(self):
        self.calls = []

    async def update_conversation(self, umo, cid, **kwargs):
        self.calls.append({"umo": umo, "cid": cid, **kwargs})


def make_stage():
    stage = object.__new__(InternalAgentSubStage)
    stage.conv_manager = FakeConvManager()
    return stage


def make_req():
    return ProviderRequest(
        conversation=Conversation(platform_id="aiocqhttp", user_id="1", cid="cid-1")
    )


def make_messages():
    return [
        Message(role="system", content="sys"),
        Message(
            role="user",
            content=[TextPart(text="你好5")],
        ),
        Message(role="assistant", content=[TextPart(text="测试成功")]),
    ]


def run_case(name, llm_response):
    stage = make_stage()
    event = FakeEvent()
    asyncio.run(
        stage._save_to_history(
            event,
            make_req(),
            llm_response,
            make_messages(),
            runner_stats=None,
        )
    )
    return name, stage.conv_manager.calls


def test_voice_only_reply_is_saved():
    """替换成语音（只有 Record）时仍要落库，assistant 正文保留原文。"""
    resp = LLMResponse(role="assistant", completion_text="测试成功")
    resp.completion_text = "测试成功"
    resp.result_chain = MessageChain(
        chain=[Record(file="x.mp3", url="")]
    )
    # 前提确认：这正是插件造成的状态——completion_text 被派生为空
    assert resp.completion_text == ""

    name, calls = run_case("voice_only", resp)
    assert len(calls) == 1, f"{name}: 语音回复那一轮被丢弃了，仍会导致上下文丢失"

    history = calls[0]["history"]
    roles = [item["role"] for item in history]
    assert roles == ["user", "assistant"], f"{name}: 落库角色异常 {roles}"
    saved_text = str(history[1]["content"])
    assert "测试成功" in saved_text, f"{name}: assistant 正文丢失 {saved_text}"


def test_image_only_reply_is_saved():
    """同类问题：插件把回复换成图片（无 Plain）也不应丢轮次。"""
    from astrbot.core.message.components import Image

    resp = LLMResponse(role="assistant", completion_text="这是图")
    resp.result_chain = MessageChain(chain=[Image(file="x.png")])
    assert resp.completion_text == ""

    _, calls = run_case("image_only", resp)
    assert len(calls) == 1, "纯图片回复那一轮被丢弃"


def test_empty_response_still_skipped():
    """真·空响应：不落库，避免制造空轮次把历史写坏。"""
    resp = LLMResponse(role="assistant", completion_text="")
    resp.result_chain = MessageChain(chain=[])

    _, calls = run_case("empty", resp)
    assert calls == [], "空响应不应落库"


def test_plain_text_reply_still_saved():
    """正常纯文本回复不受影响。"""
    resp = LLMResponse(role="assistant", completion_text="测试成功")
    resp.result_chain = MessageChain(chain=[Plain("测试成功")])
    assert resp.completion_text == "测试成功"

    _, calls = run_case("plain", resp)
    assert len(calls) == 1, "正常文本回复应落库"


if __name__ == "__main__":
    test_voice_only_reply_is_saved()
    test_image_only_reply_is_saved()
    test_empty_response_still_skipped()
    test_plain_text_reply_still_saved()
    print("all passed: 非纯文本回复的历史落库回归测试")
