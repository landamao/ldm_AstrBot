"""Centralized AstrBot path helpers.

Project path:
- Fixed to the source tree location.

Data path:
- Can be overridden with the ``LDMBOT_DATA_DIR`` environment variable.

Root path:
- Derived from the data path's parent.
- Can be overridden with the ``LDMBOT_ROOT`` environment variable
  (data directory = ``<root>/data``).
- ``LDMBOT_DATA_DIR`` takes precedence over ``LDMBOT_ROOT``.
"""

import os
import tempfile

from astrbot.core.utils.runtime_env import is_packaged_desktop_runtime


def get_astrbot_path() -> str:
    """Return the AstrBot project source path."""
    return os.path.realpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../"),
    )


def get_astrbot_data_path() -> str:
    """Return the AstrBot data directory path.

    Priority: LDMBOT_DATA_DIR env var > <LDMBOT_ROOT>/data > <cwd>/data.
    """
    if data_dir := os.environ.get("LDMBOT_DATA_DIR"):
        return os.path.realpath(data_dir)
    return os.path.realpath(os.path.join(get_astrbot_root(), "data"))


def get_astrbot_root() -> str:
    """Return the AstrBot root directory (parent of data)."""
    if path := os.environ.get("LDMBOT_ROOT"):
        return os.path.realpath(path)
    if is_packaged_desktop_runtime():
        return os.path.realpath(os.path.join(os.path.expanduser("~"), ".astrbot"))
    return os.path.realpath(os.getcwd())


def get_astrbot_config_path() -> str:
    """Return the AstrBot config directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "config"))


def get_astrbot_plugin_path() -> str:
    """Return the AstrBot plugin directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "plugins"))


def get_astrbot_plugin_data_path() -> str:
    """Return the AstrBot plugin data directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "plugin_data"))


def get_astrbot_t2i_templates_path() -> str:
    """Return the AstrBot T2I templates directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "t2i_templates"))


def get_astrbot_webchat_path() -> str:
    """Return the AstrBot WebChat data directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "webchat"))


def get_astrbot_temp_path() -> str:
    """Return the AstrBot temporary data directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "temp"))


def get_astrbot_skills_path() -> str:
    """Return the AstrBot skills directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "skills"))


def get_astrbot_workspaces_path() -> str:
    """Return the AstrBot workspaces directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "workspaces"))


def get_astrbot_system_tmp_path() -> str:
    """Return the shared system temporary directory used by local tools."""
    return os.path.realpath(os.path.join(tempfile.gettempdir(), ".astrbot"))


def get_astrbot_site_packages_path() -> str:
    """Return the AstrBot third-party site-packages directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "site-packages"))


def get_astrbot_knowledge_base_path() -> str:
    """Return the AstrBot knowledge base root path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "knowledge_base"))


def get_astrbot_backups_path() -> str:
    """Return the AstrBot backups directory path."""
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "backups"))


def get_astrbot_persona_prompts_path() -> str:
    """Return the persona prompt mirror directory path.

    仅用于查看/备份人格系统提示词，运行时仍以数据库为准。
    """
    return os.path.realpath(os.path.join(get_astrbot_data_path(), "persona_prompts"))
