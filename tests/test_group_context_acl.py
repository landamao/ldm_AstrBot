"""群聊上下文增强的群黑白名单验证。

背景：群聊上下文增强原先只能整体开关，无法按群控制；图片转述早已有
`image_caption_group_list`。本测试覆盖新增的 `group_icl_group_list`：
- 规则与图片转述一致：空=不限制、`all`/`*`=全部、`/群号`=黑名单、其余为白名单
- 群号与会话 ID 任一命中即通过，黑名单优先（避免 `all + /群号` 被通配符绕过）
- 未通过的群不记录消息、不注入历史，且不消费已积累的缓冲

运行：python tests/test_group_context_acl.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.builtin_stars.astrbot.group_chat_context import (  # noqa: E402
    GroupChatContext,
    _群列表放行,
)
from astrbot.core.message.components import Plain  # noqa: E402
from astrbot.core.platform.message_type import MessageType  # noqa: E402


class FakeEvent:
    def __init__(self, umo, group_id):
        self.unified_msg_origin = umo
        self._group_id = group_id
        self._extras = {}
        self.message_obj = SimpleNamespace(
            message=[Plain("hello")],
            sender=SimpleNamespace(nickname="tester"),
        )
        self.message_str = "hello"

    def get_group_id(self):
        return self._group_id

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE

    def get_messages(self):
        return self.message_obj.message

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value


class FakeContext:
    """只实现 GroupChatContext 用到的 get_config。"""

    def __init__(self, group_list):
        self.group_list = group_list

    def get_config(self, umo=None):
        return {
            "provider_ltm_settings": {
                "group_icl_enable": True,
                "group_message_max_cnt": 10,
                "group_icl_group_list": self.group_list,
                "image_caption": False,
                "image_caption_provider_id": "",
                "image_caption_group_list": [],
                "image_caption_min_interval": 0,
                "image_caption_lazy": True,
                "image_caption_concurrency": 2,
                "image_caption_max_size_mb": 0,
                "group_message_history_enable": False,
                "group_message_history_max_cnt": 700,
                "active_reply": {
                    "enable": False,
                    "method": "possibility_reply",
                    "possibility_reply": 0.1,
                    "prompt": "",
                    "whitelist": [],
                },
            },
            "provider_settings": {"image_caption_prompt": "desc"},
        }


class FakeRequest:
    def __init__(self):
        self.leading_user_content_parts = []


def make_group_context(group_list) -> GroupChatContext:
    return GroupChatContext(None, FakeContext(group_list))


def test_acl_rules() -> None:
    cases = [
        ("空列表不限制", ["", "111", []], True),
        ("all 放行", ["", "111", ["all"]], True),
        ("* 放行", ["", "111", ["*"]], True),
        ("all 扣除黑名单", ["", "111", ["all", "/111"]], False),
        ("白名单命中", ["", "111", ["111", "222"]], True),
        ("白名单未命中", ["", "333", ["111", "222"]], False),
        ("会话 ID 命中", ["111", "333", ["111"]], True),
        ("/all 全部拦截", ["", "111", ["/all"]], False),
        ("纯空白项拦截", ["", "111", [" ", ""]], False),
    ]
    for desc, (umo, group_id, group_list), expected in cases:
        actual = _群列表放行(FakeEvent(umo, group_id), group_list)
        assert actual is expected, f"{desc}: {actual} != {expected}"
        print(f"PASS | {desc}")


def test_record_blocked() -> None:
    async def run(group_list, group_id):
        gc = make_group_context(group_list)
        event = FakeEvent("aiocqhttp:GroupMessage:1", group_id)
        await gc.handle_message(event)
        return len(gc.raw_records.get(event.unified_msg_origin, []))

    assert asyncio.run(run(["111"], "111")) == 1
    assert asyncio.run(run(["111"], "333")) == 0
    assert asyncio.run(run(["all", "/111"], "111")) == 0
    print("PASS | 群黑白名单控制是否记录消息")


def test_inject_blocked() -> None:
    async def run_inject(group_list, group_id):
        gc = make_group_context(group_list)
        first = FakeEvent("aiocqhttp:GroupMessage:2", group_id)
        await gc.handle_message(first)
        trigger = FakeEvent("aiocqhttp:GroupMessage:2", group_id)
        await gc.handle_message(trigger)
        req = FakeRequest()
        await gc.on_req_llm(trigger, req)
        return len(req.leading_user_content_parts)

    assert asyncio.run(run_inject(["111"], "111")) == 1

    async def run_blocked():
        gc = make_group_context(["all", "/111"])
        gc.raw_records["aiocqhttp:GroupMessage:3"].append("[tester/00:00:00]: old")
        gc._record_ids["aiocqhttp:GroupMessage:3"].append("old-id")
        gc._record_seq["aiocqhttp:GroupMessage:3"].append(1)
        gc._seq = 1
        trigger = FakeEvent("aiocqhttp:GroupMessage:3", "111")
        trigger.set_extra("_group_context_record_id", "not-in-buffer")
        req = FakeRequest()
        await gc.on_req_llm(trigger, req)
        return len(req.leading_user_content_parts), len(
            gc.raw_records["aiocqhttp:GroupMessage:3"]
        )

    assert asyncio.run(run_blocked()) == (0, 1)
    print("PASS | 未通过时不注入历史，也不消费缓冲")


def main() -> None:
    test_acl_rules()
    test_record_blocked()
    test_inject_blocked()
    print("ALL PASS")


if __name__ == "__main__":
    main()
