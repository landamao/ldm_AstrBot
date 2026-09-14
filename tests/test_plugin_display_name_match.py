"""plugin 指令显示名匹配验证。

验证行为：
- 插件名精确命中直接执行（原行为不变）
- 显示名精确且唯一命中直接执行，实际操作用真实插件名
- 显示名精确但命中多个 → 编号候选列表提示，不执行
- 模糊匹配唯一命中 → 「你可能要…的插件是…」提示，不执行
- 模糊匹配多个命中 → 编号候选列表提示，不执行
- 全无命中 → 插件不存在提示
- 输入串同时是某插件显示名、另一插件插件名时，插件名优先

运行：python tests/test_plugin_display_name_match.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.builtin_stars.builtin_commands.commands.plugin import PluginCommands  # noqa: E402


def make_plugin(name, display_name=None):
    return SimpleNamespace(
        name=name,
        display_name=display_name,
        activated=True,
        version="1.0.0",
        author="",
        desc="",
        short_desc="",
        repo="",
        module_path="",
        root_dir_name="",
        reserved=False,
    )


class StarManagerStub:
    def __init__(self):
        self.reloaded = []
        self.turned_off = []
        self.turned_on = []

    async def reload(self, name):
        self.reloaded.append(name)
        return True, ""

    async def turn_off_plugin(self, name):
        self.turned_off.append(name)

    async def turn_on_plugin(self, name):
        self.turned_on.append(name)


def make_harness(plugins):
    manager = StarManagerStub()
    context = SimpleNamespace(get_all_stars=lambda: plugins, _star_manager=manager)
    commands = PluginCommands(context)
    return commands, manager


def make_event():
    results = []

    def set_result(result):
        results.append(result)

    return SimpleNamespace(set_result=set_result, results=results)


def result_text(event):
    assert event.results, "应当有回复消息"
    return event.results[0].chain[0].text


def test_off_by_exact_display_name_executes_with_real_name():
    plugins = [make_plugin("astrbot_plugin_stealer", "表情包小偷")]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_off(event, "表情包小偷")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert manager.turned_off == ["astrbot_plugin_stealer"], "应按真实插件名执行禁用"
    assert result_text(event) == "插件「astrbot_plugin_stealer」已禁用。"


def test_restart_by_name_still_works():
    plugins = [make_plugin("astrbot_plugin_stealer", "表情包小偷")]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_restart(event, "astrbot_plugin_stealer")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert manager.reloaded == ["astrbot_plugin_stealer"]
    assert result_text(event) == "插件「astrbot_plugin_stealer」已重启。"


def test_fuzzy_unique_hit_returns_hint_without_executing():
    plugins = [
        make_plugin("astrbot_plugin_stealer", "表情包小偷"),
        make_plugin("astrbot_plugin_news", "新闻推送"),
    ]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_restart(event, "表情包")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert not manager.reloaded, "模糊命中不应直接执行"
    assert result_text(event) == (
        "你可能要重启的插件是\n"
        "→ 表情包小偷（astrbot_plugin_stealer），\n"
        "使用\n"
        "→ /plugin restart astrbot_plugin_stealer\n"
        "重启"
    )


def test_fuzzy_multiple_hits_return_numbered_list():
    plugins = [
        make_plugin("astrbot_plugin_stealer", "表情包小偷"),
        make_plugin("astrbot_plugin_maker", "表情包制作"),
    ]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_restart(event, "表情")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert not manager.reloaded, "多个命中不应直接执行"
    assert result_text(event) == (
        "匹配到多个结果：\n"
        "1.表情包小偷（astrbot_plugin_stealer）\n"
        "2.表情包制作（astrbot_plugin_maker）\n"
        "请确认后使用\n"
        "→ /plugin restart <括号内的值>\n"
        "即可重启插件"
    )


def test_exact_display_hit_multiple_returns_numbered_list():
    plugins = [
        make_plugin("astrbot_plugin_a", "小偷"),
        make_plugin("astrbot_plugin_b", "小偷"),
    ]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_restart(event, "小偷")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert not manager.reloaded, "显示名重复命中不应直接执行"
    text = result_text(event)
    assert text.startswith("匹配到多个结果："), text


def test_no_hit_returns_not_found():
    plugins = [make_plugin("astrbot_plugin_stealer", "表情包小偷")]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_restart(event, "weather")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert not manager.reloaded
    assert result_text(event) == (
        "插件名「weather」不存在，使用'/plugin ls'查找插件名"
    )


def test_name_takes_priority_over_same_display():
    plugins = [
        make_plugin("stealer"),
        make_plugin("astrbot_plugin_other", "stealer"),
    ]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_restart(event, "stealer")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert manager.reloaded == ["stealer"], "插件名精确命中应优先于显示名"


def test_label_always_shows_name_in_parens():
    """无显示名的插件在候选里也用「插件名（插件名）」格式，保持整齐。"""
    plugins = [
        make_plugin("astrbot_plugin_stealer"),
        make_plugin("astrbot_plugin_maker"),
    ]

    async def scenario():
        commands, manager = make_harness(plugins)
        event = make_event()
        await commands.plugin_restart(event, "steal")
        return manager, event

    manager, event = asyncio.run(scenario())
    assert not manager.reloaded, "模糊命中不应直接执行"
    assert result_text(event) == (
        "你可能要重启的插件是\n"
        "→ astrbot_plugin_stealer（astrbot_plugin_stealer），\n"
        "使用\n"
        "→ /plugin restart astrbot_plugin_stealer\n"
        "重启"
    )


def test_help_multi_hit_tail_reads_naturally():
    plugins = [
        make_plugin("astrbot_plugin_a", "小偷一号"),
        make_plugin("astrbot_plugin_b", "小偷二号"),
    ]

    async def scenario():
        commands, _ = make_harness(plugins)
        event = make_event()
        await commands.plugin_help(event, "小偷")
        return event

    event = asyncio.run(scenario())
    text = result_text(event)
    assert text.endswith(
        "请确认后使用\n→ /plugin help <括号内的值>\n即可查看插件帮助"
    ), text


def main():
    tests = [
        test_off_by_exact_display_name_executes_with_real_name,
        test_restart_by_name_still_works,
        test_fuzzy_unique_hit_returns_hint_without_executing,
        test_fuzzy_multiple_hits_return_numbered_list,
        test_exact_display_hit_multiple_returns_numbered_list,
        test_no_hit_returns_not_found,
        test_name_takes_priority_over_same_display,
        test_label_always_shows_name_in_parens,
        test_help_multi_hit_tail_reads_naturally,
    ]
    for test in tests:
        test()
        print(f"✓ {test.__name__}")
    print(f"共 {len(tests)} 项全部通过")


if __name__ == "__main__":
    main()
