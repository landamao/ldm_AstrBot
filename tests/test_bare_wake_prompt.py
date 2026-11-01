"""空唤醒（仅 @ 机器人/全体成员或仅唤醒前缀）正常走 LLM 管线验证。

背景：原先空艾特由 builtin_stars 的 handle_empty_mention 特殊处理——
60 秒等待窗口 + 绕开正常管线直接 request_llm，群聊上下文注入不生效；
关闭 empty_mention_waiting 后空艾特更是被 internal 阶段当空消息直接跳过。

改造后：
- handle_empty_mention 已删除，空艾特与普通消息一样走唤醒逻辑
- internal 阶段对空唤醒补一条 system_reminder 提示词后继续 LLM 请求，
  并设置 _bare_wake 标记；群聊上下文 on_req_llm 据此注入全部缓冲历史
- platform_settings.bare_wake_prompt.enable 可关闭提示词注入（与
  「补回艾特自身」等插件重复时用）；关闭后不写 message_str，交由
  on_waiting_llm_request 钩子里的插件补全，插件也没补出内容则跳过请求
- 私聊纯空白消息、@ 别人等场景维持跳过，不被误判

运行：python tests/test_bare_wake_prompt.py
"""

import asyncio
import sys
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.builtin_stars.astrbot.group_chat_context import (  # noqa: E402
    GroupChatContext,
)
from astrbot.builtin_stars.astrbot.main import Main  # noqa: E402
from astrbot.core.message.components import At, AtAll, Image, Plain  # noqa: E402
from astrbot.core.pipeline.process_stage.method.agent_sub_stages import (  # noqa: E402
    internal,
)
from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (  # noqa: E402
    _BARE_WAKE_PROMPT,
    InternalAgentSubStage,
)
from astrbot.core.platform.message_type import MessageType  # noqa: E402

WAKE_PREFIX = "小助手"
UMO = "aiocqhttp:GroupMessage:456"


def make_stage(prompt_enabled: bool = True) -> InternalAgentSubStage:
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    stage.ctx = SimpleNamespace(astrbot_config={"wake_prefix": [WAKE_PREFIX]})
    stage.bare_wake_prompt_enable = prompt_enabled
    return stage


class FakeEvent:
    def __init__(self, comps, is_wake=True, self_id="bot123", message_str=""):
        self.message_obj = SimpleNamespace(message=list(comps))
        self.is_wake = is_wake
        self.unified_msg_origin = UMO
        self._self_id = self_id
        self.message_str = message_str
        self._extras = {}

    def get_self_id(self) -> str:
        return self._self_id

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value


def make_group_context(buffered: list[str]) -> GroupChatContext:
    gc = GroupChatContext.__new__(GroupChatContext)
    gc._locks = {}
    gc.raw_records = defaultdict(deque)
    gc._record_ids = defaultdict(deque)
    gc._record_seq = defaultdict(deque)
    gc._seq = 0
    gc._image_caption_last_at = {}
    gc._pending_images = {}
    gc._caption_cache = OrderedDict()
    gc._caption_inflight = {}
    gc._caption_cache_lock = asyncio.Lock()
    gc._caption_sem = None
    gc._caption_sem_limit = 0
    gc.raw_records[UMO].extend(buffered)
    gc._record_ids[UMO].extend(f"id{i}" for i in range(len(buffered)))
    for _ in buffered:
        gc._seq += 1
        gc._record_seq[UMO].append(gc._seq)
    gc.cfg = lambda event: {"image_caption": False}
    return gc


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


def test_bare_wake_sets_marker_extra():
    """空唤醒补提示词时设置 _bare_wake 标记，供群聊上下文注入识别。"""
    stage = make_stage()
    event = FakeEvent([At(qq="bot123")])
    assert stage._fill_bare_wake_prompt(event) is True
    assert event.get_extra("_bare_wake") is True


def test_disabled_keeps_message_empty_for_plugin():
    """关闭注入：不写 message_str，但仍标记空唤醒，交给插件补全。"""
    stage = make_stage(prompt_enabled=False)
    event = FakeEvent([At(qq="bot123")])
    assert stage._fill_bare_wake_prompt(event) is True
    assert event.message_str == "", "关闭注入后不应写入提示词"
    assert stage._has_message_content(event) is False, "补全前仍无内容"
    assert event.get_extra("_bare_wake") is True, "群聊历史注入标记不受开关影响"


def test_disabled_allows_plugin_filled_content():
    """关闭注入：插件在钩子里补上内容后即视为可请求。"""
    stage = make_stage(prompt_enabled=False)
    event = FakeEvent([At(qq="bot123")])
    stage._fill_bare_wake_prompt(event)
    event.message_str = " @咪咪(myself) "
    assert stage._has_message_content(event) is True


def test_disabled_ignores_non_bare_events():
    """关闭注入后，非空唤醒事件仍不改写、不标记。"""
    stage = make_stage(prompt_enabled=False)
    event = FakeEvent([Plain("你好")], message_str="你好")
    assert stage._fill_bare_wake_prompt(event) is False
    assert event.message_str == "你好"
    assert event.get_extra("_bare_wake") is None


def test_bare_wake_on_req_llm_injects_full_buffer():
    """空唤醒请求：水位=全部记录时，注入全部历史并清空缓冲。"""
    gc = make_group_context(["[A/10:00]: 早啊", "[B/10:01]: 早", "[C/10:02]: 在聊啥"])
    event = FakeEvent([At(qq="bot123")])
    event.set_extra("_bare_wake", True)
    # 唤醒时缓冲里已有这三条
    gc.mark_wake_watermark(event)
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    assert len(req.leading_user_content_parts) == 1, "应注入一个群聊历史块"
    block = req.leading_user_content_parts[0].text
    for line in ("早啊", "早", "在聊啥"):
        assert line in block, f"历史块应包含缓冲记录：{line}"
    assert len(gc.raw_records[UMO]) == 0, "缓冲应被消费清空"


def test_bare_wake_watermark_excludes_later_messages():
    """空唤醒后、请求前新到的消息不算历史：不注入，且留在缓冲给下一轮。"""
    gc = make_group_context(["[A/10:00]: 早啊", "[B/10:01]: 早"])
    event = FakeEvent([At(qq="bot123")])
    event.set_extra("_bare_wake", True)
    # 唤醒时缓冲只有前两条
    gc.mark_wake_watermark(event)
    # 等待窗口内又来了两条（例如插件在 on_llm_request 里等 6 秒）
    gc.raw_records[UMO].extend(["[C/10:03]: 看图", "[C/10:04]: [Image: 一只猫]"])
    gc._record_ids[UMO].extend(["id2", "id3"])
    gc._record_seq[UMO].extend([3, 4])
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    block = req.leading_user_content_parts[0].text
    assert "早啊" in block and "早" in block, "水位之前的历史应注入"
    assert "看图" not in block, "唤醒之后的消息不应算历史"
    assert "一只猫" not in block, "唤醒之后的图片不应被转述注入"
    assert list(gc.raw_records[UMO]) == ["[C/10:03]: 看图", "[C/10:04]: [Image: 一只猫]"], (
        "水位之后的记录应完整保留在缓冲，留给下一轮"
    )
    assert list(gc._record_seq[UMO]) == [3, 4], "序号需与记录同步保留"
    assert list(gc._record_ids[UMO]) == ["id2", "id3"], "记录 ID 需与记录同步保留"


def test_bare_wake_watermark_only_later_messages_no_history():
    """水位为 0（唤醒前缓冲为空）：本轮不注入历史，后到消息全部留给下一轮。"""
    gc = make_group_context([])
    event = FakeEvent([At(qq="bot123")])
    event.set_extra("_bare_wake", True)
    gc.mark_wake_watermark(event)
    gc.raw_records[UMO].append("[C/10:03]: 看图")
    gc._record_ids[UMO].append("id0")
    gc._record_seq[UMO].append(1)
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    assert req.leading_user_content_parts == [], "没有历史可注入时不应塞空块"
    assert list(gc.raw_records[UMO]) == ["[C/10:03]: 看图"], "后到消息不能丢"


def test_watermark_survives_trim():
    """水位按序号而非下标切：期间发生出队裁剪也不会切错。"""
    gc = make_group_context(["[A/10:00]: 一", "[B/10:01]: 二"])
    event = FakeEvent([At(qq="bot123")])
    event.set_extra("_bare_wake", True)
    gc.mark_wake_watermark(event)  # 水位=2
    # 模拟裁剪：旧记录被挤掉，新记录接着排队
    gc.raw_records[UMO].popleft()
    gc._record_ids[UMO].popleft()
    gc._record_seq[UMO].popleft()
    gc.raw_records[UMO].extend(["[C/10:02]: 三", "[C/10:03]: 四"])
    gc._record_ids[UMO].extend(["id2", "id3"])
    gc._record_seq[UMO].extend([3, 4])
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    block = req.leading_user_content_parts[0].text
    assert "二" in block, "水位内幸存的记录仍应注入"
    assert "三" not in block and "四" not in block, "水位之后的记录不入历史"
    assert list(gc.raw_records[UMO]) == ["[C/10:02]: 三", "[C/10:03]: 四"]


def test_bare_wake_without_watermark_keeps_legacy_full_flush():
    """没有水位信息（异常路径）时维持旧行为：全量注入并清空。"""
    gc = make_group_context(["[A/10:00]: 早啊", "[B/10:01]: 早"])
    event = FakeEvent([At(qq="bot123")])
    event.set_extra("_bare_wake", True)
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    block = req.leading_user_content_parts[0].text
    assert "早啊" in block and "早" in block
    assert len(gc.raw_records[UMO]) == 0


def test_mark_wake_watermark_uses_current_seq():
    """水位取"此刻已分配到的序号"，之后新增的记录不会落进水位。"""
    gc = make_group_context(["[A/10:00]: 早啊"])
    event = FakeEvent([At(qq="bot123")])
    gc.mark_wake_watermark(event)
    assert event.get_extra("_group_context_wake_seq") == 1

    gc.raw_records[UMO].append("[B/10:01]: 早")
    gc._record_seq[UMO].append(2)
    assert event.get_extra("_group_context_wake_seq") == 1, "水位不随后续消息变化"


def test_bare_wake_with_record_uses_record_index():
    """有落位记录的空唤醒（仅唤醒前缀）仍按记录下标切，优先于水位。"""
    gc = make_group_context(["[A/10:00]: 早啊", "[B/10:01]: 小助手", "[C/10:02]: 后续"])
    event = FakeEvent([Plain(WAKE_PREFIX)])
    event.set_extra("_bare_wake", True)
    event.set_extra("_group_context_record_id", "id1")
    # 故意把水位放到含自己记录的位置：走水位就会把自己也当成历史
    event.set_extra("_group_context_wake_seq", 2)
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    block = req.leading_user_content_parts[0].text
    assert "早啊" in block
    assert "小助手" not in block, "触发消息本身走主 prompt，不应进历史块"
    assert list(gc.raw_records[UMO]) == ["[C/10:02]: 后续"]


def test_bare_wake_empty_buffer_no_inject():
    """空唤醒但缓冲为空：不注入，不报错。"""
    gc = make_group_context([])
    event = FakeEvent([At(qq="bot123")])
    event.set_extra("_bare_wake", True)
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    assert req.leading_user_content_parts == []


def test_main_on_message_sets_watermark():
    """Main.on_message 在消息阶段就给群消息打上水位（早于任何 on_llm_request）。"""
    gc = make_group_context(["[A/10:00]: 早啊"])
    main = Main.__new__(Main)
    main.group_chat_context = gc
    main.context = SimpleNamespace(
        get_config=lambda **_kwargs: {
            "provider_ltm_settings": {
                "group_icl_enable": False,
                "active_reply": {"enable": False},
            },
        },
    )
    event = FakeEvent([At(qq="bot123")])
    event.get_message_type = lambda: MessageType.GROUP_MESSAGE

    async def run():
        async for _ in main.on_message(event):
            pass

    asyncio.run(run())

    assert event.get_extra("_group_context_wake_seq") == 1, "应留下唤醒时刻的水位"


def test_plain_event_without_extras_unchanged():
    """无落位 extra 且非空唤醒的事件维持原行为：不注入。"""
    gc = make_group_context(["[A/10:00]: 早啊"])
    event = FakeEvent([At(qq="bot123")])
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    assert req.leading_user_content_parts == []
    assert len(gc.raw_records[UMO]) == 1, "缓冲不应被消费"


def test_record_id_flow_unchanged():
    """普通唤醒消息的原有注入流程不受影响：注入之前的记录、保留之后的。"""
    gc = make_group_context(["[A/10:00]: 早啊", "[B/10:01]: @bot 看这个", "[C/10:02]: 后续"])
    event = FakeEvent([At(qq="bot123"), Plain("看这个")])
    event.set_extra("_group_context_record_id", "id1")
    req = SimpleNamespace(leading_user_content_parts=[])

    asyncio.run(gc.on_req_llm(event, req))

    block = req.leading_user_content_parts[0].text
    assert "早啊" in block, "应注入当前消息之前的记录"
    assert "看这个" not in block, "当前消息本身不应出现在历史块"
    assert "@bot" not in block
    assert "后续" in str(gc.raw_records[UMO]), "之后的记录应保留在缓冲"


# ---------- process 层：关闭注入后的放行与跳过 ----------

_REACHED = {"request": False}


@dataclass
class _BuildCfg:
    """替代 MainAgentBuildConfig，够 replace() 用即可。"""

    provider_wake_prefix: str = ""
    streaming_response: bool = False


async def _stub_build_main_agent(**_kwargs):
    _REACHED["request"] = True
    return None


class ProcessEvent(FakeEvent):
    """process() 需要的额外事件接口。"""

    def __init__(self, comps, message_str=""):
        super().__init__(comps, message_str=message_str)
        self.set_extra("enable_streaming", False)

    def get_platform_name(self):
        return "aiocqhttp"

    def is_private_chat(self):
        return False

    def get_messages(self):
        return list(self.message_obj.message)

    def get_message_outline(self):
        return "[At]"

    def is_stopped(self):
        return False

    async def send_typing(self):
        return None

    async def stop_typing(self):
        return None

    async def send(self, chain):
        return None


def make_process_stage(prompt_enabled: bool) -> InternalAgentSubStage:
    stage = InternalAgentSubStage.__new__(InternalAgentSubStage)
    conf = {
        "platform_settings": {
            "interrupt_reply": {"enable": False},
            "message_debounce": {"enable": False},
        },
    }
    stage.ctx = SimpleNamespace(
        astrbot_config={"wake_prefix": [WAKE_PREFIX]},
        plugin_manager=SimpleNamespace(
            context=SimpleNamespace(get_config=lambda *a, **k: conf),
        ),
    )
    stage.streaming_response = False
    stage.show_reasoning = False
    stage.bare_wake_prompt_enable = prompt_enabled
    stage.unsupported_streaming_strategy = "turn_off"
    stage.main_agent_cfg = _BuildCfg()
    return stage


def run_process(stage, event, hook) -> str:
    """跑一遍 process()，返回 requested（走到建 agent）或 skipped。"""

    async def run():
        original_build = internal.build_main_agent
        original_hook = internal.call_event_hook
        original_follow_up = internal.try_capture_follow_up
        internal.build_main_agent = _stub_build_main_agent
        internal.call_event_hook = hook
        internal.try_capture_follow_up = lambda _event: None
        _REACHED["request"] = False
        gen = stage.process(event, "")
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass
        finally:
            await gen.aclose()
            internal.build_main_agent = original_build
            internal.call_event_hook = original_hook
            internal.try_capture_follow_up = original_follow_up
        return "requested" if _REACHED["request"] else "skipped"

    return asyncio.run(run())


async def _hook_keep(event, _event_type, *_args, **_kwargs):
    return False


async def _hook_fill(event, _event_type, *_args, **_kwargs):
    # 模拟「补回艾特自身」在 on_waiting_llm_request 里补内容
    event.message_str = " @咪咪(myself) "
    return False


def test_process_disabled_skips_without_plugin():
    """关闭注入且没有插件补内容：跳过请求，不给模型发空消息。"""
    event = ProcessEvent([At(qq="bot123")])
    assert run_process(make_process_stage(False), event, _hook_keep) == "skipped"
    assert event.message_str == ""
    assert event.get_extra("_bare_wake") is True


def test_process_disabled_requests_after_plugin_fill():
    """关闭注入但插件补出了内容：照常请求。"""
    event = ProcessEvent([At(qq="bot123")])
    assert run_process(make_process_stage(False), event, _hook_fill) == "requested"
    assert event.message_str == " @咪咪(myself) "


def test_process_enabled_injects_prompt():
    """开关默认开启：沿用注入提示词后请求。"""
    event = ProcessEvent([At(qq="bot123")])
    assert run_process(make_process_stage(True), event, _hook_keep) == "requested"
    assert event.message_str == _BARE_WAKE_PROMPT


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")
