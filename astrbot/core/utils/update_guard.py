"""LDMBOT_DISABLE_UPDATE 环境变量的统一判定与提示文案。

启用后本次启动禁止一切主程序更新操作：核心源码、WebUI、备份回滚
（回滚同样会覆盖源码），无任何强制继续途径。插件更新不在管辖范围。

判定实时读取进程环境变量：外部无法在运行中修改进程环境，
效果上等同于启动时固化。update_rollback.py 因纯标准库约束
无法 import 本模块，在 rollback() 内内联了同一判定（两处需同步修改）。
"""

import os

真值集合 = ("true", "1", "t")

禁用提示 = (
    "更新已被环境变量 LDMBOT_DISABLE_UPDATE 禁用：本次启动拒绝一切"
    "主程序更新（源码与 WebUI）及回滚操作。如需更新，请移除该环境变量后重启。"
)


def is_update_disabled() -> bool:
    """LDMBOT_DISABLE_UPDATE 置为真值（true/1/t）时本次启动禁用主程序更新。"""
    return os.getenv("LDMBOT_DISABLE_UPDATE", "").strip().lower() in 真值集合
