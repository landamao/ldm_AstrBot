# 更新包应用时保留本地 dashboard 源码测试
#
# 背景：ldm 更新包的 dashboard/ 只带 WebUI 构建产物（dist），而本地
# dashboard/ 下源码、工程文件与 dist 共存。_应用源码包内容曾把顶层
# dashboard 整目录 clear + copytree，一次更新就把本地 WebUI 源码全清掉。
# 现约定：dashboard 不整目录覆盖，dist 由 _应用webui 单独覆盖到
# 实际生效的 WebUI 目录（dashboard/dist 或 --webui-dir/LDMBOT_WEBUI_DIR）。
import zipfile

import pytest

from astrbot.core.updator import AstrBotUpdator


def _make_package_zip(zip_path, version="v4.99.0"):
    """构造 ldm 更新包形态：顶层目录 ldmbot/ + main.py + astrbot/ + dashboard/dist。"""
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ldmbot/main.py", "print('new main')\n")
        zf.writestr("ldmbot/astrbot/__init__.py", "__version__ = '4.99.0'\n")
        zf.writestr("ldmbot/dashboard/dist/index.html", "<html>new</html>\n")
        zf.writestr("ldmbot/dashboard/dist/assets/version", version + "\n")


@pytest.fixture()
def updator(tmp_path, monkeypatch):
    # data 目录指到临时位置，避免测试备份/元数据写进真实 data/
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(tmp_path / "data"))
    updator = AstrBotUpdator()
    updator.MAIN_PATH = str(tmp_path / "project")
    (tmp_path / "project" / "astrbot").mkdir(parents=True)
    return updator


def test_dashboard_source_preserved_and_dist_replaced(updator, tmp_path):
    """源码/工程文件原样保留，dist 换成新构建产物且旧残留清除。"""
    项目根 = tmp_path / "project"
    dashboard = 项目根 / "dashboard"
    (dashboard / "src").mkdir(parents=True)
    (dashboard / "node_modules" / "pkg").mkdir(parents=True)
    (dashboard / "dist" / "assets").mkdir(parents=True)
    (dashboard / "src" / "App.vue").write_text(
        "<template>local</template>", encoding="utf-8"
    )
    (dashboard / "package.json").write_text('{"name": "local"}', encoding="utf-8")
    (dashboard / "node_modules" / "pkg" / "index.js").write_text(
        "local", encoding="utf-8"
    )
    (dashboard / "dist" / "index.html").write_text("<html>old</html>", encoding="utf-8")
    (dashboard / "dist" / "assets" / "version").write_text("v4.0.0\n", encoding="utf-8")
    (dashboard / "dist" / "stale.js").write_text("stale", encoding="utf-8")
    (项目根 / "astrbot" / "__init__.py").write_text(
        "__version__ = '4.0.0'\n", encoding="utf-8"
    )
    (项目根 / "main.py").write_text("print('old main')\n", encoding="utf-8")

    包 = tmp_path / "update.zip"
    _make_package_zip(包)
    updator.apply_update_package(包)

    # 本地 dashboard 源码与工程文件原样保留
    assert (
        dashboard / "src" / "App.vue"
    ).read_text(encoding="utf-8") == "<template>local</template>"
    assert (dashboard / "package.json").read_text(
        encoding="utf-8"
    ) == '{"name": "local"}'
    assert (dashboard / "node_modules" / "pkg" / "index.js").read_text(
        encoding="utf-8"
    ) == "local"
    # dist 整体替换为新构建产物，旧残留不保留
    assert (
        dashboard / "dist" / "index.html"
    ).read_text(encoding="utf-8") == "<html>new</html>\n"
    assert (
        dashboard / "dist" / "assets" / "version"
    ).read_text(encoding="utf-8").strip() == "v4.99.0"
    assert not (dashboard / "dist" / "stale.js").exists()
    # 其他顶层内容照常覆盖
    assert (项目根 / "main.py").read_text(
        encoding="utf-8"
    ) == "print('new main')\n"
    assert (项目根 / "astrbot" / "__init__.py").read_text(
        encoding="utf-8"
    ) == "__version__ = '4.99.0'\n"


def test_webui_dir_override_keeps_local_dashboard_intact(updator, tmp_path, monkeypatch):
    """指定 LDMBOT_WEBUI_DIR 时 dist 覆盖到该目录，本地 dashboard 完全不动。"""
    项目根 = tmp_path / "project"
    dashboard = 项目根 / "dashboard"
    (dashboard / "src").mkdir(parents=True)
    (dashboard / "dist" / "assets").mkdir(parents=True)
    (dashboard / "src" / "App.vue").write_text("local", encoding="utf-8")
    (dashboard / "dist" / "index.html").write_text("<html>old</html>", encoding="utf-8")
    (dashboard / "dist" / "assets" / "version").write_text("v4.0.0\n", encoding="utf-8")

    实际webui = tmp_path / "custom-webui"
    实际webui.mkdir()
    monkeypatch.setenv("LDMBOT_WEBUI_DIR", str(实际webui))

    包 = tmp_path / "update.zip"
    _make_package_zip(包)
    updator.apply_update_package(包)

    assert (实际webui / "index.html").read_text(
        encoding="utf-8"
    ) == "<html>new</html>\n"
    assert (实际webui / "assets" / "version").read_text(
        encoding="utf-8"
    ).strip() == "v4.99.0"
    assert (dashboard / "dist" / "index.html").read_text(
        encoding="utf-8"
    ) == "<html>old</html>"
    assert (dashboard / "src" / "App.vue").read_text(encoding="utf-8") == "local"


def test_package_dashboard_extra_entries_ignored(updator, tmp_path):
    """包内 dashboard/ 出现 dist 之外的条目时忽略之，不覆盖本地同名文件。"""
    项目根 = tmp_path / "project"
    dashboard = 项目根 / "dashboard"
    (dashboard / "dist" / "assets").mkdir(parents=True)
    (dashboard / "package.json").write_text('{"name": "local"}', encoding="utf-8")
    (dashboard / "dist" / "index.html").write_text("<html>old</html>", encoding="utf-8")
    (dashboard / "dist" / "assets" / "version").write_text("v4.0.0\n", encoding="utf-8")

    包 = tmp_path / "update.zip"
    with zipfile.ZipFile(包, "w") as zf:
        zf.writestr("ldmbot/main.py", "print('new main')\n")
        zf.writestr("ldmbot/astrbot/__init__.py", "__version__ = '4.99.0'\n")
        zf.writestr("ldmbot/dashboard/dist/index.html", "<html>new</html>\n")
        zf.writestr("ldmbot/dashboard/dist/assets/version", "v4.99.0\n")
        zf.writestr("ldmbot/dashboard/package.json", '{"name": "package"}')
    updator.apply_update_package(包)

    assert (dashboard / "package.json").read_text(
        encoding="utf-8"
    ) == '{"name": "local"}'
    assert (dashboard / "dist" / "index.html").read_text(
        encoding="utf-8"
    ) == "<html>new</html>\n"


def test_dashboard_missing_dist_created(updator, tmp_path):
    """本地 dashboard 只有源码没有 dist（历史 bug 清空后的状态）：dist 正常补齐。"""
    项目根 = tmp_path / "project"
    dashboard = 项目根 / "dashboard"
    (dashboard / "src").mkdir(parents=True)
    (dashboard / "src" / "App.vue").write_text("local", encoding="utf-8")

    包 = tmp_path / "update.zip"
    _make_package_zip(包)
    updator.apply_update_package(包)

    assert (dashboard / "src" / "App.vue").read_text(encoding="utf-8") == "local"
    assert (
        dashboard / "dist" / "index.html"
    ).read_text(encoding="utf-8") == "<html>new</html>\n"
    assert (
        dashboard / "dist" / "assets" / "version"
    ).read_text(encoding="utf-8").strip() == "v4.99.0"
