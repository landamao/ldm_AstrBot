"""验证 LDMBOT_DATA_DIR 自定义路径下，插件逻辑包名 data.plugins 可正确导入。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def custom_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data_xxx"
    plugins_dir = data_dir / "plugins" / "demo_plugin"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugins_dir / "main.py").write_text(
        "VALUE = 'from_custom_data_dir'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(data_dir))
    # 清掉可能残留的注入包与已导入模块
    for key in list(sys.modules):
        if key == "data" or key.startswith("data."):
            del sys.modules[key]
    yield data_dir
    for key in list(sys.modules):
        if key == "data" or key.startswith("data."):
            del sys.modules[key]


def test_ensure_plugin_module_importable_maps_custom_data_dir(custom_data_dir):
    from astrbot.core.utils.astrbot_path import (
        ensure_plugin_module_importable,
        get_astrbot_data_path,
        get_astrbot_plugin_path,
    )

    assert Path(get_astrbot_data_path()) == custom_data_dir.resolve()
    assert Path(get_astrbot_plugin_path()) == (custom_data_dir / "plugins").resolve()

    ensure_plugin_module_importable()

    assert "data" in sys.modules
    assert "data.plugins" in sys.modules
    assert list(sys.modules["data"].__path__) == [str(custom_data_dir.resolve())]
    assert list(sys.modules["data.plugins"].__path__) == [
        str((custom_data_dir / "plugins").resolve())
    ]

    module = __import__("data.plugins.demo_plugin.main", fromlist=["main"])
    assert module.VALUE == "from_custom_data_dir"
    assert "demo_plugin" in str(Path(module.__file__).resolve())


def test_ensure_plugin_module_importable_idempotent(custom_data_dir):
    from astrbot.core.utils.astrbot_path import ensure_plugin_module_importable

    ensure_plugin_module_importable()
    first_data = sys.modules["data"]
    first_plugins = sys.modules["data.plugins"]
    ensure_plugin_module_importable()
    assert sys.modules["data"] is first_data
    assert sys.modules["data.plugins"] is first_plugins


def test_log_plugin_path_follows_custom_data_dir(custom_data_dir):
    from astrbot.core.log import _is_plugin_path
    from astrbot.core.utils.astrbot_path import ensure_plugin_module_importable

    ensure_plugin_module_importable()
    plugin_py = custom_data_dir / "plugins" / "demo_plugin" / "main.py"
    assert _is_plugin_path(str(plugin_py)) is True
    assert _is_plugin_path(str(custom_data_dir / "logs" / "app.log")) is False


def test_cli_resolve_data_path_honors_env(custom_data_dir, monkeypatch, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".astrbot").touch()
    monkeypatch.chdir(project)

    from astrbot.cli.utils.basic import resolve_cli_data_path

    resolved = resolve_cli_data_path(project)
    assert resolved == custom_data_dir.resolve()
