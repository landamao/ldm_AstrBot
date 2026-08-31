from __future__ import annotations

from astrbot.core.config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from . import HandlerFilter
from .command import CommandFilter
from .custom_filter import CustomFilter


# 指令组受到 wake_prefix 的制约。
class CommandGroupFilter(HandlerFilter):
    def __init__(
        self,
        group_name: str,
        alias: set | None = None,
        parent_group: CommandGroupFilter | None = None,
    ) -> None:
        self.group_name = group_name
        self.alias = alias if alias else set()
        self._original_group_name = group_name
        self.sub_command_filters: list[CommandFilter | CommandGroupFilter] = []
        self.custom_filter_list: list[CustomFilter] = []
        self.parent_group = parent_group

        # Cache for complete command names list
        self._cmpl_cmd_names: list | None = None

    def add_sub_command_filter(
        self,
        sub_command_filter: CommandFilter | CommandGroupFilter,
    ) -> None:
        self.sub_command_filters.append(sub_command_filter)
        # 兜底子指令（指令名为 *）需要反向引用所属指令组，用于未匹配时判断
        if (
            isinstance(sub_command_filter, CommandFilter)
            and sub_command_filter.command_name == "*"
        ):
            sub_command_filter.parent_group = self

    def add_custom_filter(self, custom_filter: CustomFilter) -> None:
        self.custom_filter_list.append(custom_filter)

    def get_complete_command_names(self) -> list[str]:
        """遍历父节点获取完整的指令名。

        新版本 v3.4.29 采用预编译指令，不再从指令组递归遍历子指令，因此这个方法是返回包括别名在内的整个指令名列表。
        """
        if self._cmpl_cmd_names is not None:
            return self._cmpl_cmd_names

        parent_cmd_names = (
            self.parent_group.get_complete_command_names() if self.parent_group else []
        )

        if not parent_cmd_names:
            # 根节点
            return [self.group_name] + list(self.alias)

        result = []
        candidates = [self.group_name] + list(self.alias)
        for parent_cmd_name in parent_cmd_names:
            for candidate in candidates:
                result.append(parent_cmd_name + " " + candidate)
        self._cmpl_cmd_names = result
        return result

    # 以树的形式打印出来
    def print_cmd_tree(
        self,
        sub_command_filters: list[CommandFilter | CommandGroupFilter],
        prefix: str = "",
        event: AstrMessageEvent | None = None,
        cfg: AstrBotConfig | None = None,
    ) -> str:
        parts = []
        for sub_filter in sub_command_filters:
            if isinstance(sub_filter, CommandFilter):
                custom_filter_pass = True
                if event and cfg:
                    custom_filter_pass = sub_filter.custom_filter_ok(event, cfg)
                if custom_filter_pass:
                    cmd_th = sub_filter.print_types()
                    cmd_display = sub_filter.command_name
                    if cmd_display == "*":
                        cmd_display = "* (未匹配时处理)"
                    line = f"{prefix}├── {cmd_display}"
                    if cmd_th:
                        line += f" ({cmd_th})"
                    else:
                        line += " (无参数指令)"

                    if sub_filter.handler_md and sub_filter.handler_md.desc:
                        line += f": {sub_filter.handler_md.desc}"

                    parts.append(line + "\n")
            elif isinstance(sub_filter, CommandGroupFilter):
                custom_filter_pass = True
                if event and cfg:
                    custom_filter_pass = sub_filter.custom_filter_ok(event, cfg)
                if custom_filter_pass:
                    parts.append(f"{prefix}├── {sub_filter.group_name}\n")
                    parts.append(
                        sub_filter.print_cmd_tree(
                            sub_filter.sub_command_filters,
                            prefix + "│   ",
                            event=event,
                            cfg=cfg,
                        )
                    )

        return "".join(parts)

    def custom_filter_ok(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        for custom_filter in self.custom_filter_list:
            if not custom_filter.filter(event, cfg):
                return False
        return True

    def startswith(self, message_str: str) -> bool:
        """判断消息是否以本指令组（含别名）开头，指令名后需为空格或结尾，避免前缀重叠误判。"""
        for full_cmd in self.get_complete_command_names():
            if message_str == full_cmd or message_str.startswith(f"{full_cmd} "):
                return True
        return False

    def equals(self, message_str: str) -> bool:
        return message_str in self.get_complete_command_names()

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        if not event.is_at_or_wake_command:
            return False

        # 判断当前指令组的自定义过滤器
        if not self.custom_filter_ok(event, cfg):
            return False

        message_str = event.message_str.strip()
        if self.equals(message_str):
            tree = (
                self.group_name
                + "\n"
                + self.print_cmd_tree(self.sub_command_filters, event=event, cfg=cfg)
            )
            # 仅输入指令组名 / 未提供子指令名。plugin 组会在 waking_check 里替换为美化帮助。
            raise ValueError(
                f"子指令名不存在。{self.group_name} 指令组下有如下指令，请参考：\n"
                + tree,
            )

        if not self.startswith(message_str):
            return False

        # 组前缀命中：确认是否有子指令匹配，或存在 * 兜底子指令
        has_fallback = any(
            isinstance(sf, CommandFilter) and sf.command_name == "*"
            for sf in self.sub_command_filters
        )
        if has_fallback:
            # 兜底子指令的 filter 会自行判断是否生效
            return True
        for sf in self.sub_command_filters:
            if isinstance(sf, CommandGroupFilter):
                if sf.startswith(message_str):
                    return True
            elif sf.matches(message_str):
                return True
        # 子指令名不存在：抛出提示，由 waking_check 捕获后发送并停止事件（不唤醒 LLM）
        tree = (
            self.group_name
            + "\n"
            + self.print_cmd_tree(self.sub_command_filters, event=event, cfg=cfg)
        )
        raise ValueError(
            f"子指令名不存在。{self.group_name} 指令组下有如下指令，请参考：\n" + tree,
        )
