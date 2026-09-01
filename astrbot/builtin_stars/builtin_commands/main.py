from astrbot.api import star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.star.filter.command import GreedyStr

from .commands import (
    AboutCommand,
    AdminCommands,
    AlterCmdCommands,
    ConversationCommands,
    FlowCommand,
    HelpCommand,
    ImageCommands,
    LLMCommands,
    NameCommand,
    PersonaCommands,
    PluginCommands,
    ProviderCommands,
    SetUnsetCommands,
    SIDCommand,
    T2ICommand,
    TTSCommand,
)


class Main(star.Star):
    def __init__(self, context: star.Context, config=None) -> None:
        self.context = context
        self.config = config

        self.help_c = HelpCommand(self.context)
        self.image_c = ImageCommands(self.context, config)
        self.llm_c = LLMCommands(self.context, config)
        self.plugin_c = PluginCommands(self.context)
        self.admin_c = AdminCommands(self.context)
        self.conversation_c = ConversationCommands(self.context)
        self.provider_c = ProviderCommands(self.context)
        self.persona_c = PersonaCommands(self.context)
        self.alter_cmd_c = AlterCmdCommands(self.context)
        self.setunset_c = SetUnsetCommands(self.context)
        self.name_c = NameCommand(self.context)
        self.t2i_c = T2ICommand(self.context)
        self.tts_c = TTSCommand(self.context)
        self.flow_c = FlowCommand(self.context)
        self.sid_c = SIDCommand(self.context)
        self.about_c = AboutCommand(self.context)

    @filter.command("help")
    async def help(self, event: AstrMessageEvent) -> None:
        """查看帮助"""
        try:
            await self.help_c.help(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("llm")
    async def llm(
        self, event: AstrMessageEvent, sid: str = "", 操作: str = ""
    ) -> None:
        """开关会话 LLM，详见 /llm help"""
        try:
            await self.llm_c.llm(event, sid, 操作)
        finally:
            event.should_call_llm(True)

    @filter.on_llm_request()
    async def _llm_request_gate(self, event: AstrMessageEvent, req) -> None:
        """LLM 请求前拦截被关闭会话的请求。"""
        await self.llm_c.on_llm_request(event, req)

    @filter.command_group("plugin")
    def plugin(self) -> None:
        """插件管理"""

    @plugin.command("ls")
    async def plugin_ls(self, event: AstrMessageEvent) -> None:
        """获取已经安装的插件列表。"""
        try:
            await self.plugin_c.plugin_ls(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @plugin.command("off")
    async def plugin_off(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """禁用插件"""
        try:
            await self.plugin_c.plugin_off(event, plugin_name)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @plugin.command("on")
    async def plugin_on(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """启用插件"""
        try:
            await self.plugin_c.plugin_on(event, plugin_name)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @plugin.command("get")
    async def plugin_get(self, event: AstrMessageEvent, plugin_repo: str = "") -> None:
        """安装插件"""
        try:
            await self.plugin_c.plugin_get(event, plugin_repo)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @plugin.command("restart")
    async def plugin_restart(
        self, event: AstrMessageEvent, plugin_name: str = ""
    ) -> None:
        """重启插件"""
        try:
            await self.plugin_c.plugin_restart(event, plugin_name)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @plugin.command("update")
    async def plugin_update(
        self, event: AstrMessageEvent, plugin_name: str = ""
    ) -> None:
        """更新插件"""
        try:
            await self.plugin_c.plugin_update(event, plugin_name)
        finally:
            event.should_call_llm(True)

    @plugin.command("help")
    async def plugin_help(self, event: AstrMessageEvent, plugin_name: str = "") -> None:
        """获取插件帮助"""
        try:
            await self.plugin_c.plugin_help(event, plugin_name)
        finally:
            event.should_call_llm(True)

    @filter.command_group("image")
    def image(self) -> None:
        """生图"""

    @image.command("star", alias={"start"})
    async def image_star(
        self, event: AstrMessageEvent, prompt: GreedyStr = ""
    ) -> None:
        """使用默认生图模型生成图片"""
        try:
            await self.image_c.star(event, prompt)
        finally:
            event.should_call_llm(True)

    @image.command("model")
    async def image_model(
        self,
        event: AstrMessageEvent,
        model_id: str = "",
        prompt: GreedyStr = "",
    ) -> None:
        """指定生图模型生成图片"""
        try:
            await self.image_c.model(event, model_id, prompt)
        finally:
            event.should_call_llm(True)

    @image.command("stop")
    async def image_stop(self, event: AstrMessageEvent, task_id: str = "") -> None:
        """停止生图任务"""
        try:
            await self.image_c.stop(event, task_id)
        finally:
            event.should_call_llm(True)

    @image.command("mlist")
    async def image_mlist(self, event: AstrMessageEvent) -> None:
        """列出可用生图模型"""
        try:
            await self.image_c.mlist(event)
        finally:
            event.should_call_llm(True)

    @image.command("tlist")
    async def image_tlist(self, event: AstrMessageEvent, user_id: str = "") -> None:
        """列出正在进行的生图任务"""
        try:
            await self.image_c.tlist(event, user_id)
        finally:
            event.should_call_llm(True)

    @image.command("help")
    async def image_help(self, event: AstrMessageEvent) -> None:
        """查看生图指令帮助"""
        try:
            await self.image_c.help(event)
        finally:
            event.should_call_llm(True)

    @image.command("*")
    async def image_fallback(self, event: AstrMessageEvent, cmd: str = "") -> None:
        """未匹配的生图子指令，发送帮助"""
        try:
            await self.image_c.help(event)
        finally:
            event.should_call_llm(True)

    @filter.command("t2i")
    async def t2i(self, event: AstrMessageEvent) -> None:
        """开关文本转图片"""
        try:
            await self.t2i_c.t2i(event)
        finally:
            event.should_call_llm(True)

    @filter.command("tts")
    async def tts(self, event: AstrMessageEvent) -> None:
        """开关文本转语音（会话级别）"""
        try:
            await self.tts_c.tts(event)
        finally:
            event.should_call_llm(True)

    @filter.command("flow")
    async def flow(self, event: AstrMessageEvent, arg: str = "") -> None:
        """开关当前会话的流式输出。传入 unset 恢复跟随全局配置"""
        try:
            await self.flow_c.flow(event, arg)
        finally:
            event.should_call_llm(True)

    @filter.command("sid")
    async def sid(self, event: AstrMessageEvent) -> None:
        """获取会话 ID 信息"""
        try:
            await self.sid_c.sid(event)
        finally:
            event.should_call_llm(True)

    @filter.command("about", alias={'ldm'})
    async def about(self, event: AstrMessageEvent) -> None:
        """查看 ldm 版本与运行环境信息"""
        try:
            await self.about_c.about(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("name")
    async def name(self, event: AstrMessageEvent, alias: GreedyStr) -> None:
        """设置当前会话的显示名称。传入 unset 清除"""
        try:
            await self.name_c.name(event, alias)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("op")
    async def op(self, event: AstrMessageEvent, admin_id: str = "") -> None:
        """授权管理员。op <admin_id>"""
        try:
            await self.admin_c.op(event, admin_id)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("deop")
    async def deop(self, event: AstrMessageEvent, admin_id: str) -> None:
        """取消授权管理员。deop <admin_id>"""
        try:
            await self.admin_c.deop(event, admin_id)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("wl")
    async def wl(self, event: AstrMessageEvent, sid: str = "") -> None:
        """添加白名单。wl <sid>"""
        try:
            await self.admin_c.wl(event, sid)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("dwl")
    async def dwl(self, event: AstrMessageEvent, sid: str) -> None:
        """删除白名单。dwl <sid>"""
        try:
            await self.admin_c.dwl(event, sid)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("provider")
    async def provider(
        self,
        event: AstrMessageEvent,
        idx: str | int | None = None,
        idx2: int | None = None,
    ) -> None:
        """查看或者切换 LLM 提供商"""
        try:
            await self.provider_c.provider(event, idx, idx2)
        finally:
            event.should_call_llm(True)

    @filter.command("reset")
    async def reset(self, event: AstrMessageEvent) -> None:
        """清除当前对话上下文"""
        try:
            await self.conversation_c.reset(event)
        finally:
            event.should_call_llm(True)

    @filter.command("stop")
    async def stop(self, event: AstrMessageEvent) -> None:
        """停止当前会话中正在运行的 Agent"""
        try:
            await self.conversation_c.stop(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("model")
    async def model_ls(
        self,
        event: AstrMessageEvent,
        idx_or_name: int | str | None = None,
    ) -> None:
        """查看或者切换模型"""
        try:
            await self.provider_c.model_ls(event, idx_or_name)
        finally:
            event.should_call_llm(True)

    @filter.command("history")
    async def his(self, event: AstrMessageEvent, page: int = 1) -> None:
        """查看对话记录"""
        try:
            await self.conversation_c.his(event, page)
        finally:
            event.should_call_llm(True)

    @filter.command("ls")
    async def convs(self, event: AstrMessageEvent, page: int = 1) -> None:
        """查看对话列表，可用 /switch <序号> 切换"""
        try:
            await self.conversation_c.convs(event, page)
        finally:
            event.should_call_llm(True)

    @filter.command("new")
    async def new_conv(self, event: AstrMessageEvent) -> None:
        """创建新对话"""
        try:
            await self.conversation_c.new_conv(event)
        finally:
            event.should_call_llm(True)

    @filter.command("status")
    async def status(self, event: AstrMessageEvent) -> None:
        """查看当前对话 Agent 状态及 Token 用量"""
        try:
            await self.conversation_c.status(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("groupnew")
    async def groupnew_conv(self, event: AstrMessageEvent, sid: str) -> None:
        """创建新群聊对话"""
        try:
            await self.conversation_c.groupnew_conv(event, sid)
        finally:
            event.should_call_llm(True)

    @filter.command("switch")
    async def switch_conv(
        self, event: AstrMessageEvent, index: int | None = None
    ) -> None:
        """通过 /ls 前面的序号切换对话"""
        try:
            await self.conversation_c.switch_conv(event, index)
        finally:
            event.should_call_llm(True)

    @filter.command("rename")
    async def rename_conv(self, event: AstrMessageEvent, new_name: str) -> None:
        """重命名对话"""
        try:
            await self.conversation_c.rename_conv(event, new_name)
        finally:
            event.should_call_llm(True)

    @filter.command("del")
    async def del_conv(self, event: AstrMessageEvent) -> None:
        """删除当前对话"""
        try:
            await self.conversation_c.del_conv(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("key")
    async def key(self, event: AstrMessageEvent, index: int | None = None) -> None:
        """查看或者切换 Key"""
        try:
            await self.provider_c.key(event, index)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("persona")
    async def persona(self, event: AstrMessageEvent) -> None:
        """查看或者切换人格情景"""
        try:
            await self.persona_c.persona(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("restart")
    async def restart(self, event: AstrMessageEvent) -> None:
        """重启 ldm 框架"""
        try:
            await self.admin_c.restart(event)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("upldm")
    async def up_ldm(self, event: AstrMessageEvent) -> None:
        """更新ldmbot并自动重启"""
        try:
            await self.admin_c.up_ldm(event)
        finally:
            event.should_call_llm(True)

    @filter.command("set")
    async def set_variable(self, event: AstrMessageEvent, key: str, value: str) -> None:
        """设置会话变量（供 Agent 使用）"""
        try:
            await self.setunset_c.set_variable(event, key, value)
        finally:
            event.should_call_llm(True)

    @filter.command("unset")
    async def unset_variable(self, event: AstrMessageEvent, key: str) -> None:
        """移除会话变量"""
        try:
            await self.setunset_c.unset_variable(event, key)
        finally:
            event.should_call_llm(True)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("alter_cmd", alias={"alter"})
    async def alter_cmd(self, event: AstrMessageEvent) -> None:
        """修改命令权限"""
        try:
            await self.alter_cmd_c.alter_cmd(event)
        finally:
            event.should_call_llm(True)
