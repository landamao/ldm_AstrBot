# ruff: noqa: F401, F403, F811, I001
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.api import logger
from astrbot.core import html_renderer
from astrbot.core.star.register import register_llm_tool as llm_tool

# event
from astrbot.core.message.message_event_result import (
    MessageEventResult,
    MessageChain,
    CommandResult,
    EventResultType,
)
from astrbot.core.platform import AstrMessageEvent

# star register
from astrbot.core.star.register import (
    register_command as command,
    register_command_group as command_group,
    register_event_message_type as event_message_type,
    register_regex as regex,
    register_platform_adapter_type as platform_adapter_type,
)
from astrbot.core.star.filter.event_message_type import (
    EventMessageTypeFilter,
    EventMessageType,
)
from astrbot.core.star.filter.platform_adapter_type import (
    PlatformAdapterTypeFilter,
    PlatformAdapterType,
)
from astrbot.core.star.register import (
    register_star as register,  # 注册插件（Star）
)
from astrbot.core.star import Context, Star
from astrbot.core.star.config import *


# provider
from astrbot.core.provider import Provider, ProviderMetaData
from astrbot.core.db.po import Personality

# platform
from astrbot.core.platform import (
    AstrMessageEvent,
    Platform,
    AstrBotMessage,
    MessageMember,
    MessageType,
    PlatformMetadata,
)

from astrbot.core.platform.register import register_platform_adapter

from .message_components import *

# message_components 通过 `from astrbot.core.message.components import *` 间接导出了全局 logger，
# 覆盖了上面 `from astrbot.api import logger` 设置的 _PluginContextLogger。
# 在这里重新从 astrbot.api 导入，确保插件 `from astrbot.api.all import logger` 拿到的是
# 能按插件名路由的 _PluginContextLogger，而非全局 logger。
from astrbot.api import logger  # noqa: F811
