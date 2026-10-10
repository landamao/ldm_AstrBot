# 更新回滚备份补全根目录文件测试
#
# 背景：更新策略是「包内除受保护目录外全部覆盖」，requirements.txt、
# pyproject.toml、uv.lock 等根目录文件更新时都会被新版覆盖；而回滚备份
# 曾只含 astrbot/ + dist/ + main.py，回滚后出现「旧源码 + 新依赖清单」的
# 新旧混杂。现约定：根目录备份文件 清单内的文件更新前一并备份、回滚时
# 同步恢复；.env 等运行态文件更新永不触碰，不进备份。
import zipfile

import pytest

from astrbot.core.utils import update_rollback


@pytest.fixture(autouse=True)
def _清理环境(monkeypatch):
    """防本机环境变量泄漏影响备份/回滚目录解析与禁更闸门。"""
    monkeypatch.delenv("LDMBOT_WEBUI_DIR", raising=False)
    monkeypatch.delenv("LDMBOT_DISABLE_UPDATE", raising=False)
    yield


# 全部清单文件各给一份可辨识内容（正文含文件名，防交叉写错）
根文件样例 = {
    "main.py": "print('main v1')",
    "runtime_bootstrap.py": "# bootstrap v1",
    "requirements.txt": "astrbot==1.0.0",
    "pyproject.toml": "[project]\nname = 'v1'",
    "uv.lock": "# lock v1",
    ".env.example": "# EXAMPLE_V1=1",
    ".gitignore": ".venv/",
    "LICENSE": "LICENSE v1",
    "NOTICE": "NOTICE v1",
    "README.md": "# README v1",
    "CHANGELOG.md": "## v1",
    "从官方迁移教程.txt": "迁移教程正文 v1",
}


def _准备项目(tmp_path, 根文件们):
    """构造带根目录文件的项目源码，返回 (project, webui_dir, data_dir)。"""
    project = tmp_path / "project"
    src = project / "astrbot"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("__version__ = '1.0.0'", encoding="utf-8")
    for 名字, 内容 in 根文件们.items():
        # newline="\n"：Windows 默认会把 \n 写成 \r\n，而 zip 存的是原始字节，
        # 回读比对会多出 \r
        (project / 名字).write_text(内容, encoding="utf-8", newline="\n")
    webui_dir = tmp_path / "webui"
    webui_dir.mkdir()
    (webui_dir / "index.html").write_text("<html>old</html>", encoding="utf-8")
    return project, webui_dir, tmp_path / "data"


def test_backup_contains_all_root_files(tmp_path):
    """清单内根文件全部进备份包（含中文名与点开头文件）；.env 不进。"""
    project, webui_dir, data_dir = _准备项目(tmp_path, 根文件样例)
    (project / ".env").write_text("SECRET=1", encoding="utf-8")

    backup = update_rollback.backup_current_version(
        project_root=str(project),
        version="1.0.0",
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )
    assert backup.is_file()
    with zipfile.ZipFile(backup) as zf:
        名字们 = set(zf.namelist())
        for 名字, 内容 in 根文件样例.items():
            assert 名字 in 名字们
            assert zf.read(名字).decode("utf-8") == 内容
        assert ".env" not in 名字们
        # astrbot/ 源码照旧在包内
        assert zf.read("astrbot/__init__.py").decode("utf-8") == "__version__ = '1.0.0'"


def test_backup_missing_root_files_skipped(tmp_path):
    """项目缺失的根文件跳过不报错（自定义裁剪安装场景）。"""
    project, webui_dir, data_dir = _准备项目(tmp_path, {"main.py": "print('main')"})
    (project / ".env.example").write_text("# EXAMPLE=1", encoding="utf-8")

    backup = update_rollback.backup_current_version(
        project_root=str(project),
        version="1.0.0",
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )
    with zipfile.ZipFile(backup) as zf:
        名字们 = set(zf.namelist())
    assert "main.py" in 名字们
    assert ".env.example" in 名字们
    assert "uv.lock" not in 名字们
    assert "从官方迁移教程.txt" not in 名字们


def test_rollback_restores_root_files(tmp_path):
    """更新覆盖根文件与源码后回滚：清单文件全部恢复为备份时内容。"""
    project, webui_dir, data_dir = _准备项目(tmp_path, 根文件样例)
    update_rollback.backup_current_version(
        project_root=str(project),
        version="1.0.0",
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )

    # 模拟更新覆盖根文件与源码
    for 名字 in 根文件样例:
        (project / 名字).write_text(f"{名字} 被新版覆盖", encoding="utf-8")
    (project / "astrbot" / "__init__.py").write_text(
        "__version__ = '2.0.0'", encoding="utf-8"
    )

    ok = update_rollback.rollback(
        version="1.0.0",
        project_root=str(project),
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )
    assert ok is True
    for 名字, 内容 in 根文件样例.items():
        assert (project / 名字).read_text(encoding="utf-8") == 内容
    assert (
        (project / "astrbot" / "__init__.py").read_text(encoding="utf-8")
        == "__version__ = '1.0.0'"
    )


def test_rollback_old_backup_with_main_only(tmp_path):
    """旧格式备份（astrbot/ + dist/ + main.py）：main.py 恢复，其余根文件保持现状。"""
    project, webui_dir, data_dir = _准备项目(
        tmp_path, {"requirements.txt": "astrbot==2.0.0"}
    )
    (project / "main.py").write_text("新版 main", encoding="utf-8")

    旧备份目录 = update_rollback.get_rollback_dir(str(data_dir))
    旧备份目录.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(旧备份目录 / "ldmbot_0.9.0.zip", "w") as zf:
        zf.writestr("astrbot/__init__.py", "__version__ = '0.9.0'")
        zf.writestr("main.py", "旧版 main")
        zf.writestr("dist/index.html", "<html>0.9.0</html>")

    ok = update_rollback.rollback(
        version="0.9.0",
        project_root=str(project),
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )
    assert ok is True
    assert (project / "main.py").read_text(encoding="utf-8") == "旧版 main"
    # 备份中没有的根文件保持现状（新版内容）
    assert (
        (project / "requirements.txt").read_text(encoding="utf-8")
        == "astrbot==2.0.0"
    )
    assert (
        (project / "astrbot" / "__init__.py").read_text(encoding="utf-8")
        == "__version__ = '0.9.0'"
    )
    assert (webui_dir / "index.html").read_text(
        encoding="utf-8"
    ) == "<html>0.9.0</html>"


def test_rollback_backup_without_any_root_files_keeps_all(tmp_path, capsys):
    """包内没有任何根文件时：全部保持现状，打印一次旧备份提示。"""
    project, webui_dir, data_dir = _准备项目(
        tmp_path, {"main.py": "新版 main", "uv.lock": "# lock v2"}
    )

    旧备份目录 = update_rollback.get_rollback_dir(str(data_dir))
    旧备份目录.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(旧备份目录 / "ldmbot_0.9.0.zip", "w") as zf:
        zf.writestr("astrbot/__init__.py", "__version__ = '0.9.0'")

    ok = update_rollback.rollback(
        version="0.9.0",
        project_root=str(project),
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )
    assert ok is True
    assert (project / "main.py").read_text(encoding="utf-8") == "新版 main"
    assert (project / "uv.lock").read_text(encoding="utf-8") == "# lock v2"
    assert "旧备份" in capsys.readouterr().out
