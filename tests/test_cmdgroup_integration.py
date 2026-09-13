"""真实装饰器集成验证：指令组 * 兜底子指令（未匹配时处理）。

场景：
1. 无 * 兜底：子指令不匹配（/test uuu）-> 抛「子指令名不存在」，不唤醒 LLM
             纯组名（/test）-> 抛「子指令名不存在」
             正常子指令（/test off）-> 正常激活 meme_off
2. 有 * 兜底（/test2 *）：子指令不匹配（/test2 uuu）-> 激活兜底 handler
             正常子指令（/test2 off）-> 只激活 meme_off，不激活兜底
             纯组名（/test2）-> 抛「子指令名不存在」（* 不接纯组名）
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, "/home/ldm/ldmbot_code")  # 源码树优先（运行目录是 editable 安装旧代码）

from astrbot.api.event import filter  # noqa: E402
from astrbot.api.star import Context, Star, register  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    star_handlers_registry,
)


@register("test_plugin", "作者", "测试插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    # ===== 组 test：无 * 兜底 =====
    @filter.command_group("test")
    def test(self, event):
        yield event.plain_result("111")

    @test.command("off")
    async def meme_off(self, event):
        yield event.plain_result("off")

    # ===== 组 test2：有 * 兜底 =====
    @filter.command_group("test2")
    def test2(self, event):
        yield event.plain_result("222")

    @test2.command("off")
    async def meme_off2(self, event):
        yield event.plain_result("off2")

    @test2.command("*")
    async def meme_fallback(self, event, cmd: str = None):
        yield event.plain_result(f"兜底收到: {cmd or '无'}")


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
    """复刻 waking_check/stage.py L163-248 的过滤循环，返回 (激活列表, 错误文本)。"""
    from astrbot.core.star.filter.command import CommandFilter
    from astrbot.core.star.filter.command_group import CommandGroupFilter
    from astrbot.core.star.filter.permission import PermissionTypeFilter
    from astrbot.core.star.star import star_map

    activated = []
    handlers_parsed_params = {}
    err_text = None
    event.plugins_name = None
    for handler in star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent,
        plugins_name=event.plugins_name,
    ):
        passed = True
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
            except ValueError as e:
                err_text = str(e)
                event.stop_event()
                passed = False
                break
        if passed:
            is_group_cmd_handler = any(
                isinstance(f, CommandGroupFilter) for f in handler.event_filters
            )
            if not is_group_cmd_handler:
                activated.append(handler.handler_full_name)
                if "parsed_params" in event.get_extra(default={}):
                    handlers_parsed_params[handler.handler_full_name] = (
                        event.get_extra("parsed_params")
                    )
        event._extras.pop("parsed_params", None)
    return activated, err_text, handlers_parsed_params


def main():
    handlers = star_handlers_registry.get_handlers_by_event_type(
        EventType.AdapterMessageEvent,
    )
    print("=== 注册结果 ===")
    for h in handlers:
        if "test2" not in h.handler_full_name and "test" not in h.handler_full_name:
            continue
        fdesc = []
        for f in h.event_filters:
            if type(f).__name__ == "CommandGroupFilter":
                fdesc.append(f"CommandGroupFilter({f.group_name})")
            else:
                tag = ""
                if getattr(f, "is_fallback", False):
                    tag = " [兜底]"
                fdesc.append(
                    f"CommandFilter({f.command_name}){tag} parent_group={getattr(getattr(f, 'parent_group', None), 'group_name', None)}"
                )
        print(f"  {h.handler_full_name}: {fdesc or '[]空'}")

    print("\n=== 无 * 兜底（组 test）===")
    for msg in ["test off", "test uuu", "test"]:
        act, err, _ = 复刻waking循环(模拟事件(msg))
        print(f"  [{msg}] -> 激活: {act or '无'} | 错误: {err or '无'}")

    print("\n=== 有 * 兜底（组 test2）===")
    for msg in ["test2 off", "test2 uuu", "test2", "test2 aaa bbb"]:
        act, err, pparams = 复刻waking循环(模拟事件(msg))
        print(f"  [{msg}] -> 激活: {act or '无'} | 错误: {err or '无'} | 参数: {pparams}")

    # ===== 断言 =====
    print("\n=== 断言 ===")
    act, err, _ = 复刻waking循环(模拟事件("test off"))
    assert act == ["__main___meme_off"] and err is None, "test off 应激活 meme_off"
    print("  PASS test off -> meme_off")

    act, err, _ = 复刻waking循环(模拟事件("test uuu"))
    assert act == [] and err and err.startswith("子指令名不存在。"), f"test uuu 应抛子指令名不存在，实际 {err}"
    print(f"  PASS test uuu -> 子指令名不存在（{err[:40]}...）")

    act, err, _ = 复刻waking循环(模拟事件("test"))
    assert act == [] and err and err.startswith("子指令名不存在。"), "test 纯组名应抛子指令名不存在"
    print("  PASS test（纯组名）-> 子指令名不存在")

    act, err, _ = 复刻waking循环(模拟事件("test2 off"))
    assert act == ["__main___meme_off2"] and err is None, "test2 off 应只激活 meme_off2"
    print("  PASS test2 off -> meme_off2（兜底不抢）")

    act, err, pparams = 复刻waking循环(模拟事件("test2 uuu"))
    assert act == ["__main___meme_fallback"] and err is None, "test2 uuu 应激活兜底"
    assert pparams.get("__main___meme_fallback", {}).get("cmd") == "uuu", f"兜底参数应为 uuu，实际 {pparams}"
    print("  PASS test2 uuu -> 兜底 meme_fallback，参数 cmd=uuu")

    act, err, pparams = 复刻waking循环(模拟事件("test2 aaa bbb"))
    assert act == ["__main___meme_fallback"] and err is None, "test2 aaa bbb 应激活兜底"
    assert pparams.get("__main___meme_fallback", {}).get("cmd") == "aaa", f"兜底首参应为 aaa，实际 {pparams}"
    print("  PASS test2 aaa bbb -> 兜底，首参 cmd=aaa")

    act, err, _ = 复刻waking循环(模拟事件("test2"))
    assert act == [] and err and err.startswith("子指令名不存在。"), "test2 纯组名应抛子指令名不存在（* 不接纯组名）"
    print("  PASS test2（纯组名）-> 子指令名不存在（* 不接纯组名）")

    print("\n全部断言通过 ✓")


if __name__ == "__main__":
    main()
