"""唤醒前缀工具。

所有面向用户的指令提示（如 /help、/sid、/plugin ls 等）统一通过
「获取第一个唤醒词」拿用户实际配置的第一个唤醒词，替代硬编码的「/」。
每次调用都实时读取当前配置，WebUI 修改后立即生效，不做任何缓存。
"""

DEFAULT_WAKE_PREFIX = "/"


def 获取第一个唤醒词() -> str:
    """实时返回用户配置的第一个唤醒词。

    配置对象是全局单例，WebUI 保存配置时对其原地更新，
    因此每次现读即可拿到最新值。取配置列表里第一个非空项；
    未配置时回退默认「/」。
    """
    try:
        # 函数内延迟导入，避免模块加载期的循环依赖
        from astrbot.core import astrbot_config

        prefixes = astrbot_config.get("wake_prefix")
    except Exception:
        return DEFAULT_WAKE_PREFIX
    if isinstance(prefixes, (list, tuple)):
        for prefix in prefixes:
            if isinstance(prefix, str) and prefix:
                return prefix
    return DEFAULT_WAKE_PREFIX
