"""AstrBot 备份模块共享常量

此文件定义了导出器和导入器共享的常量，确保两端配置一致。
"""

from sqlmodel import SQLModel

from astrbot.core.db.po import (
    Attachment,
    ChatUIProject,
    CommandConfig,
    CommandConflict,
    ConversationV2,
    Persona,
    PersonaFolder,
    PlatformMessageHistory,
    PlatformSession,
    PlatformStat,
    Preference,
    SessionProjectRelation,
    WebChatThread,
)
from astrbot.core.knowledge_base.models import (
    KBDocument,
    KBMedia,
    KnowledgeBase,
)
from astrbot.core.utils.astrbot_path import (
    get_astrbot_config_path,
    get_astrbot_knowledge_base_path,
    get_astrbot_plugin_data_path,
    get_astrbot_plugin_path,
    get_astrbot_skills_path,
    get_astrbot_t2i_templates_path,
    get_astrbot_webchat_path,
)

# ============================================================
# 共享常量 - 确保导出和导入端配置一致
# ============================================================

# 主数据库模型类映射
MAIN_DB_MODELS: dict[str, type[SQLModel]] = {
    "platform_stats": PlatformStat,
    "conversations": ConversationV2,
    "personas": Persona,
    "persona_folders": PersonaFolder,
    "preferences": Preference,
    "platform_message_history": PlatformMessageHistory,
    "platform_sessions": PlatformSession,
    "webchat_threads": WebChatThread,
    "chatui_projects": ChatUIProject,
    "session_project_relations": SessionProjectRelation,
    "attachments": Attachment,
    "command_configs": CommandConfig,
    "command_conflicts": CommandConflict,
}

# 知识库元数据模型类映射
KB_METADATA_MODELS: dict[str, type[SQLModel]] = {
    "knowledge_bases": KnowledgeBase,
    "kb_documents": KBDocument,
    "kb_media": KBMedia,
}


def get_backup_directories(include_knowledge_base: bool = False) -> dict[str, str]:
    """获取需要备份的目录列表

    使用 astrbot_path 模块动态获取路径，支持通过环境变量 LDMBOT_ROOT 自定义根目录。

    Args:
        include_knowledge_base: 是否把 knowledge_base 整个目录按原始文件一起打包。
            终端备份（``--backup``）没有运行时环境，读不了知识库的向量库，
            只能整目录搬；WebUI 备份有 kb_manager，走结构化导出，无需额外复制。

    Returns:
        dict: 键为备份文件中的目录名称，值为目录的绝对路径
    """
    directories = {
        "plugins": get_astrbot_plugin_path(),  # 插件本体
        "plugin_data": get_astrbot_plugin_data_path(),  # 插件数据
        "config": get_astrbot_config_path(),  # 配置目录
        "t2i_templates": get_astrbot_t2i_templates_path(),  # T2I 模板
        "webchat": get_astrbot_webchat_path(),  # WebChat 数据
        "skills": get_astrbot_skills_path(),  # Skills
    }
    if include_knowledge_base:
        directories["knowledge_base"] = get_astrbot_knowledge_base_path()
    return directories


def get_restorable_directories() -> dict[str, str]:
    """导入端可识别的目录映射（包含导出端可能出现的全部目录类型）。

    备份包里出现哪些目录由 manifest 决定，这里只是「认识」它们；
    旧备份不含 knowledge_base，因此不会因为多了这个映射而改变行为。
    """
    return get_backup_directories(include_knowledge_base=True)


# data 根目录下需要一并备份的配置文件。
# 导出时存入 ZIP 的 config/ 段，导入时写回 data/ 根目录。
BACKUP_CONFIG_FILES: tuple[str, ...] = (
    "cmd_config.json",
    "mcp_server.json",
    "skills.json",
)

# 备份清单版本号
BACKUP_MANIFEST_VERSION = "1.2"
