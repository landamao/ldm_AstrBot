"""群聊上下文前置注入（leading_user_content_parts）验证。

验证行为：
- ProviderRequest.assemble_context 拼装顺序：
  leading（参考资料，如群聊上下文）→ 用户 prompt → extra（系统提醒/指令）→ 图片
- 群聊上下文块挂载到 leading_user_content_parts，不再追加到用户消息之后
- 简单格式降级：仅 prompt 时仍降级为纯字符串；有 leading 块时不降级
"""

import base64
import tempfile
from pathlib import Path

import pytest

from astrbot.builtin_stars.astrbot.group_chat_context import GroupChatContext
from astrbot.core.agent.message import TextPart
from astrbot.core.provider.entities import ProviderRequest

# 1x1 透明 PNG
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.mark.asyncio
async def test_assemble_order():
    """拼装顺序：leading → 用户发言 → extra → 图片。"""
    png_path = Path(tempfile.mkdtemp()) / "a.png"
    png_path.write_bytes(_PNG_1PX)
    req = ProviderRequest(
        prompt="帮我看看他们在聊什么",
        leading_user_content_parts=[
            TextPart(text="<system_reminder>GROUP HISTORY</system_reminder>")
        ],
        extra_user_content_parts=[
            TextPart(text="<system_reminder>PLUGIN HINT</system_reminder>")
        ],
        image_urls=[str(png_path)],
    )
    msg = await req.assemble_context()
    assert isinstance(msg["content"], list), "多内容块时不应降级为纯字符串"
    texts = [b.get("text", "") for b in msg["content"] if b["type"] == "text"]
    assert "GROUP HISTORY" in texts[0], "群聊块应在最前"
    assert texts[1] == "帮我看看他们在聊什么", "用户发言应紧随群聊块"
    assert "PLUGIN HINT" in texts[2], "插件提醒应在用户发言之后"
    assert msg["content"][-1]["type"] == "image_url", "图片应在最后"


@pytest.mark.asyncio
async def test_simple_format_degrade():
    """仅 prompt 时降级为字符串；有 leading 块时保持多模态格式。"""
    req = ProviderRequest(prompt="hi")
    msg = await req.assemble_context()
    assert msg == {"role": "user", "content": "hi"}, "纯文本应保持向后兼容的简单格式"

    req2 = ProviderRequest(
        prompt="hi",
        leading_user_content_parts=[TextPart(text="ctx")],
    )
    msg2 = await req2.assemble_context()
    assert isinstance(msg2["content"], list), "有 leading 块时不应降级"
    assert msg2["content"][0]["text"] == "ctx"
    assert msg2["content"][1]["text"] == "hi"


class _StubEvent:
    def __init__(self, umo: str, extras: dict):
        self.unified_msg_origin = umo
        self._extras = extras

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value


class _StubContext:
    def __init__(self, cfg: dict):
        self._cfg = cfg

    def get_config(self, umo=None):
        return self._cfg


def _make_cfg() -> dict:
    return {
        "provider_ltm_settings": {
            "image_caption": False,
            "active_reply": {
                "enable": False,
                "method": "possibility_reply",
                "possibility_reply": 0,
                "prompt": "",
                "whitelist": [],
            },
        },
        "provider_settings": {"image_caption_prompt": ""},
    }


@pytest.mark.asyncio
async def test_group_context_goes_leading():
    """群聊上下文钩子应把历史块挂到 leading，extra 保持为空。"""
    ctx = GroupChatContext(acm=None, context=_StubContext(_make_cfg()))
    umo = "aiocqhttp:GroupMessage:12345"
    ctx.raw_records[umo].extend(
        [
            "[张三/10:00:00]: 早",
            "[李四/10:00:05]: 晚",
            "[王五/10:00:10]: 当前触发消息",
        ]
    )
    ctx._record_ids[umo].extend(["id1", "id2", "id3"])
    event = _StubEvent(
        umo,
        {"_group_context_record_id": "id3", "_group_context_raw_idx": 2},
    )
    req = ProviderRequest(prompt="当前触发消息")
    await ctx.on_req_llm(event, req)

    assert not req.extra_user_content_parts, "不应再追加到 extra"
    assert len(req.leading_user_content_parts) == 1, "应挂载到 leading"
    block = req.leading_user_content_parts[0].text
    assert "--- BEGIN CONTEXT ---" in block
    assert "[张三/10:00:00]: 早" in block
    assert "[李四/10:00:05]: 晚" in block
    assert "当前触发消息" not in block, "触发消息本身不注入"
    assert "before the current user message" in block, "footer 应说明这是当前消息之前的历史"
