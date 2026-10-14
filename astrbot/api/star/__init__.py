from astrbot.core.star import CommandDescriptor, Context, Star, StarTools
from astrbot.core.star.config import *
from astrbot.core.star.register import (
    register_star as register,  # 注册插件（Star）
)

__all__ = ["CommandDescriptor", "Context", "Star", "StarTools", "register"]
