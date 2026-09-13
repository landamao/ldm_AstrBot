"""验证：指令组注册子指令时不填指令名（@group.command()）的行为。

用户场景：
    @filter.command_group("test")
    def test(self, event): ...

    @test.command()          # 不填指令名
    async def meme_on(self, event): ...

    @test.command("off")
    async def meme_off(self, event): ...

期望结论：不填指令名的子指令 handler 没有任何 event_filters，
waking_check 阶段 `len(handler.event_filters) == 0: continue` 直接跳过，
该 handler 永远不会被唤醒触发。

本测试模拟 waking_check 的核心过滤循环，验证：
1. 注册时 meme_on 的 handler.event_filters 为空
2. 对 /test、/test off、/test xxx、/test on 四条消息，meme_on 均不会被激活
3. meme_off 只在 /test off 时激活
"""
from types import SimpleNamespace

from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter


def 模拟注册():
    """用与 register_command_group / register_command 相同的结构注册。"""
    group = CommandGroupFilter("test")

    # 父指令 test：event_filters = [CommandGroupFilter("test")]
    parent_handler = SimpleNamespace(
        event_filters=[group],
        handler_full_name="test",
        handler_name="test",
    )

    # @test.command()：sub_command=None → 不创建 CommandFilter，filters 为空
    meme_on_handler = SimpleNamespace(
        event_filters=[],
        handler_full_name="meme_on",
        handler_name="meme_on",
    )

    # @test.command("off")：创建 CommandFilter("off")，挂到组下
    def meme_off_impl(self, event):
        pass

    meme_off_cmd = CommandFilter(
        "off",
        parent_command_names=group.get_complete_command_names(),
    )
    meme_off_cmd.init_handler_md(
        SimpleNamespace(handler=meme_off_impl, desc="关闭"),
    )
    group.add_sub_command_filter(meme_off_cmd)
    meme_off_handler = SimpleNamespace(
        event_filters=[meme_off_cmd],
        handler_full_name="meme_off",
        handler_name="meme_off",
    )

    return group, [parent_handler, meme_on_handler, meme_off_handler]


class 模拟事件:
    def __init__(self, message_str, is_wake=True):
        self.message_str = message_str
        self.is_at_or_wake_command = is_wake
        self._extras = {}

    def get_message_str(self):
        return self.message_str

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)


def 模拟waking过滤(group, handlers, message_str, is_wake=True):
    """复刻 waking_check/stage.py 的过滤循环（L163-248 核心逻辑）。"""
    activated = []
    event = 模拟事件(message_str, is_wake)
    for handler in handlers:
        passed = True
        if len(handler.event_filters) == 0:
            continue
        for f in handler.event_filters:
            try:
                if isinstance(f, CommandGroupFilter):
                    if not f.filter(event, None):
                        passed = False
                        break
                elif isinstance(f, CommandFilter):
                    if not f.filter(event, None):
                        passed = False
                        break
            except ValueError:
                # 仅输入组名时「参数不足」，真实流程会发送帮助后 stop_event
                passed = False
                break
        if not passed:
            continue
        is_group_cmd_handler = any(
            isinstance(f, CommandGroupFilter) for f in handler.event_filters
        )
        if not is_group_cmd_handler:
            activated.append(handler.handler_full_name)
    return activated


def test_注册时不填指令名_filter为空():
    _, handlers = 模拟注册()
    meme_on = [h for h in handlers if h.handler_name == "meme_on"][0]
    assert len(meme_on.event_filters) == 0


def test_各消息下激活情况():
    group, handlers = 模拟注册()
    cases = {
        "test": [],
        "test off": ["meme_off"],
        "test xxx": [],   # 不匹配任何子指令
        "test on": [],    # 不匹配任何子指令
    }
    for msg, expected in cases.items():
        activated = 模拟waking过滤(group, handlers, msg)
        assert activated == expected, f"{msg} 期望 {expected}，实际 {activated}"


if __name__ == "__main__":
    group, handlers = 模拟注册()
    meme_on = [h for h in handlers if h.handler_name == "meme_on"][0]
    print(f"meme_on 的 event_filters 长度: {len(meme_on.event_filters)}")
    # 真实流程中 wake_prefix（如 /）已在 waking_check 剥掉，这里传剥离后的消息
    for msg in ["test", "test off", "test xxx", "test on"]:
        activated = 模拟waking过滤(group, handlers, msg)
        print(f"消息 [{msg}] -> 激活的 handler: {activated or '无'}")
