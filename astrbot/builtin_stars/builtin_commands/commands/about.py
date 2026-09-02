from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.core.utils.about_info import get_about_info


class AboutCommand:
    """关于指令类"""

    def __init__(self, context: star.Context) -> None:
        self.context = context

    async def about(self, event: AstrMessageEvent) -> None:
        """查看 ldm 版本、运行环境与相关目录信息"""
        info = await get_about_info()
        system = info["system"]
        ldm = info["ldm"]
        version = ldm["version"]
        webui_version = ldm["webui_version"] or "未知"
        version_header = f"ldm v{version}（WebUI: {webui_version}）"

        ret = (
            f"{version_header}\n"
            f"系统信息:\n"
            f"  操作系统: {system['os']} ({system['version']} · {system['arch']})\n"
            f"  Python: {system['python']}\n"
            f"ldm 信息:\n"
            f"  主程序版本: v{version}\n"
            f"  WebUI版本: {webui_version}\n"
            f"  启动目录: {ldm['startup_dir']}\n"
            f"  启动命令: {ldm['startup_command']}\n"
            f"  启动时间: {ldm['startup_time']}\n"
            f"  已运行时长: {ldm['uptime']}\n"
            f"  全局代理: {ldm['global_proxy']}\n"
            f"  WebUI目录: {ldm['webui_dir'] or '未知'}\n"
            f"  数据目录: {ldm['data_dir']}\n"
            f"  插件目录: {ldm['plugin_dir']}\n"
            f"  插件数据目录: {ldm['plugin_data_dir']}\n"
            f"  备份目录: {ldm['backup_dir']}\n"
            f"  版本回滚目录: {ldm['rollback_dir']}\n"
            f"项目地址: {info['project_url']}\n"
            f"作者GitHub: {info['author_url']}"
        )

        event.set_result(MessageEventResult().message(ret).use_t2i(False))
