"""get_all_commands / collect_commands 验证。

验证行为：
- 仅返回指令类 Handler（CommandFilter/CommandGroupFilter），事件监听类不混入
- 已禁用插件注册的指令一律不返回
- only_enabled=True 仅返回已启用的指令
- 子指令 effective_command 由父组签名拼接，并回链父组
- Context.get_all_commands 与 collect_commands 行为一致

运行：python tests/test_get_all_commands.py
"""

import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.star.command_management import collect_commands  # noqa: E402
from astrbot.core.star.context import Context  # noqa: E402
from astrbot.core.star.filter.command import CommandFilter  # noqa: E402
from astrbot.core.star.filter.command_group import CommandGroupFilter  # noqa: E402
from astrbot.core.star.filter.event_message_type import (  # noqa: E402
    EventMessageType,
    EventMessageTypeFilter,
)
from astrbot.core.star.star import StarMetadata, star_map  # noqa: E402
from astrbot.core.star.star_handler import (  # noqa: E402
    EventType,
    StarHandlerMetadata,
    star_handlers_registry,
)


def make_plugin_meta(module_path, name, activated=True):
    return StarMetadata(
        name=name,
        module_path=module_path,
        root_dir_name=name,
        activated=activated,
    )


def make_command_handler(
    module_path,
    handler_name,
    command_name,
    desc="",
    enabled=True,
    parent=None,
    is_group=False,
):
    async def handler(event):
        pass

    if is_group:
        filter_ = CommandGroupFilter(group_name=command_name)
    else:
        filter_ = CommandFilter(
            command_name=command_name,
            parent_command_names=[parent] if parent else None,
        )
    md = StarHandlerMetadata(
        event_type=EventType.AdapterMessageEvent,
        handler_full_name=f"{module_path}_{handler_name}",
        handler_name=handler_name,
        handler_module_path=module_path,
        handler=handler,
        event_filters=[filter_],
        desc=desc,
    )
    if not is_group:
        # CommandFilter 需要 handler_md 才能解析参数签名
        filter_.init_handler_md(md)
        if parent:
            md.extras_configs["sub_command"] = True
    md.enabled = enabled
    star_handlers_registry.append(md)
    return md


def make_event_handler(module_path, handler_name):
    async def handler(event):
        pass

    md = StarHandlerMetadata(
        event_type=EventType.AdapterMessageEvent,
        handler_full_name=f"{module_path}_{handler_name}",
        handler_name=handler_name,
        handler_module_path=module_path,
        handler=handler,
        event_filters=[EventMessageTypeFilter(EventMessageType.ALL)],
    )
    star_handlers_registry.append(md)
    return md


def make_context():
    return Context(
        event_queue=None,
        config=None,
        db=None,
        provider_manager=None,
        platform_manager=None,
        conversation_manager=None,
        message_history_manager=None,
        persona_manager=None,
        astrbot_config_mgr=None,
        knowledge_base_manager=None,
        cron_manager=None,
    )


def main():
    created = []
    modules = []
    try:
        mod_a = "tests_ldm.fake_plugin_a"
        mod_b = "tests_ldm.fake_plugin_b"
        modules = [mod_a, mod_b]
        star_map[mod_a] = make_plugin_meta(mod_a, "fake_plugin_a", True)
        star_map[mod_b] = make_plugin_meta(mod_b, "fake_plugin_b", False)

        created.append(make_command_handler(mod_a, "cmd_hi", "hi", desc="问好"))
        created.append(
            make_command_handler(mod_a, "cmd_bye", "bye", desc="告别", enabled=False)
        )
        created.append(make_command_handler(mod_a, "grp", "grp", desc="测试组", is_group=True))
        created.append(
            make_command_handler(mod_a, "grp_on", "on", desc="子指令", parent="grp")
        )
        created.append(make_event_handler(mod_a, "on_any_message"))
        created.append(make_command_handler(mod_b, "cmd_hidden", "hidden"))

        all_cmds = collect_commands()
        names = {c.effective_command for c in all_cmds}
        assert names == {"hi", "bye", "grp", "grp on"}, names

        by_name = {c.effective_command: c for c in all_cmds}
        assert by_name["hi"].enabled is True
        assert by_name["bye"].enabled is False
        assert by_name["hi"].plugin_name == "fake_plugin_a"
        assert by_name["hi"].description == "问好"
        assert by_name["grp"].is_group is True
        assert by_name["grp on"].is_sub_command is True
        assert by_name["grp on"].parent_group_handler == f"{mod_a}_grp"

        enabled_cmds = collect_commands(only_enabled=True)
        enabled_names = {c.effective_command for c in enabled_cmds}
        assert enabled_names == {"hi", "grp", "grp on"}, enabled_names

        ctx = make_context()
        assert {c.effective_command for c in ctx.get_all_commands()} == names
        assert {
            c.effective_command for c in ctx.get_all_commands(only_enabled=True)
        } == enabled_names

        # 插件 API 导出可用
        from astrbot.api.star import CommandDescriptor as ExportedDescriptor

        assert ExportedDescriptor is type(all_cmds[0])

        print("全部通过 ✓")
    finally:
        for h in created:
            star_handlers_registry.remove(h)
        for m in modules:
            star_map.pop(m, None)


if __name__ == "__main__":
    main()
