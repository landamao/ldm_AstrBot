"""指标上传已移除。此模块保留空 Metric 类以兼容可能的第三方插件引用。"""


class Metric:
    """空实现，不做任何指标上传。"""

    @staticmethod
    async def upload(**kwargs) -> None:
        pass

    @staticmethod
    async def flush() -> None:
        pass

    @staticmethod
    def get_installation_id() -> str:
        return "null"
