"""验证指令组 self_handle：纯组名交给指令组函数自己处理。

覆盖：
1. self_handle=True，纯组名（/aa）-> 激活组 handler 本身
2. self_handle=True，带子指令（/aa off）-> 只激活子指令，不激活组
3. self_handle=True + * 兜底（/aa uuu）-> 只激活兜底，不激活组
4. 默认（不传 self_handle）纯组名（/bb）-> 仍抛「子指令名不存在」
5. self_handle=True 且无子指令（/cc）-> 激活组；未知子指令（/cc uuu）-> 抛错
6. 子指令组也支持 self_handle（/dd sub）-> 激活子组 handler，不激活根组
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from astrbot.api.event import filter  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    star_handlers_registry,
)


@register("test_self_handle", "作者", "测试插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ===== aa：self_handle + 子指令 + 兜底 =====
    @filter.command_group("aa", self_handle=True)
    async def aa(self, event):
        """aa 组自身"""

    @aa.command("off")
    async def aa_off(self, event):
        """关闭"""

    @aa.command("*")
    async def aa_fallback(self, event, text: str = None):
        """兜底"""

    # ===== bb：默认行为（兼容旧逻辑） =====
    @filter.command_group("bb")
    async def bb(self, event):
        """bb 组自身"""

    @bb.command("off")
    async def bb_off(self, event):
        """关闭"""

    # ===== cc：self_handle 且没有子指令 =====
    @filter.command_group("cc", self_handle=True)
    async def cc(self, event):
        """cc 组自身"""

    # ===== dd：子指令组也开 self_handle =====
    @filter.command_group("dd", self_handle=True)
    async def dd(self, event):
        """dd 组自身"""

    @dd.group("sub", self_handle=True)
    async def dd_sub(self, event):
        """dd sub 子组自身"""


class 模拟事件:
    def __init__(self, message_str):
        self.message_str = message_str
        self.is_at_or_wake_command = True
        self._extras = {}
        self.stopped = False

    def get_message_str(self):
        return self.message_str

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def stop_event(self):
        self.stopped = True

    async def send(self, *args, **kwargs):
        pass


def 复刻waking循环(event):
    """复刻 waking_check/stage.py 的过滤循环（含 self_handle），返回 (激活列表, 错误文本)。"""
    from astrbot.core.star.filter.command_group import CommandGroupFilter
    from astrbot.core.star.filter.permission import PermissionTypeFilter

    activated = []
    handlers_parsed_params = {}
    err_text = None
    event.plugins_name = None
    for handler in star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent,
        only_activated=False,
        plugins_name=event.plugins_name,
    ):
        if handler.handler_module_path != __name__:
            continue
        passed = True
        self_handle_group = False
        if len(handler.event_filters) == 0:
            continue
        for f in handler.event_filters:
            try:
                if isinstance(f, PermissionTypeFilter):
                    if not f.filter(event, {}):
                        passed = False
                        break
                elif not f.filter(event, {}):
                    passed = False
                    break
                elif (
                    isinstance(f, CommandGroupFilter)
                    and f.self_handle
                    and f.equals(event.message_str.strip())
                ):
                    self_handle_group = True
            except ValueError as e:
                err_text = str(e)
                event.stop_event()
                passed = False
                break
        if passed:
            is_group_cmd_handler = any(
                isinstance(f, CommandGroupFilter) for f in handler.event_filters
            )
            if not is_group_cmd_handler or self_handle_group:
                activated.append(handler.handler_name)
                if "parsed_params" in event.get_extra(default={}):
                    handlers_parsed_params[handler.handler_name] = event.get_extra(
                        "parsed_params"
                    )
        event._extras.pop("parsed_params", None)
    return activated, err_text, handlers_parsed_params


def main():
    print("=== 注册结构 ===")
    for h in star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent, only_activated=False
    ):
        if h.handler_module_path != __name__:
            continue
        desc = []
        for f in h.event_filters:
            t = type(f).__name__
            if t == "CommandGroupFilter":
                desc.append(f"CommandGroupFilter({f.group_name}, self_handle={f.self_handle})")
            elif t == "CommandFilter":
                desc.append(f"CommandFilter({f.command_name})")
            else:
                desc.append(t)
        print(f"  {h.handler_name}: {desc}")

    cases = [
        ("aa", ["aa"], None, "纯组名 -> 组自身"),
        ("aa off", ["aa_off"], None, "子指令 -> 只走子指令"),
        ("aa uuu", ["aa_fallback"], None, "未匹配 -> * 兜底"),
        ("bb", [], "子指令名不存在", "默认组纯组名 -> 报错（兼容）"),
        ("bb off", ["bb_off"], None, "默认组子指令 -> 正常"),
        ("cc", ["cc"], None, "无子指令 + self_handle -> 组自身"),
        ("cc uuu", [], "子指令名不存在", "无子指令但发未知子指令 -> 报错"),
        ("dd", ["dd"], None, "根组纯组名 -> 根组自身"),
        ("dd sub", ["dd_sub"], None, "子指令组纯组名 -> 子组自身"),
    ]

    print("\n=== 行为 ===")
    for msg, want_act, want_err, note in cases:
        act, err, _ = 复刻waking循环(模拟事件(msg))
        print(f"  [{msg}] 激活={act or '无'} 错误={'有' if err else '无'} ({note})")
        assert act == want_act, f"[{msg}] 期望激活 {want_act}，实际 {act}"
        if want_err:
            assert err and err.startswith(want_err), f"[{msg}] 期望报错 {want_err}，实际 {err}"
        else:
            assert err is None, f"[{msg}] 不应报错，实际 {err}"

    print("\n全部断言通过 ✓")


if __name__ == "__main__":
    main()
