# LDMBOT_DISABLE_UPDATE 禁用主程序更新测试
#
# 范围：环境变量启用后，本次启动一切主程序更新（核心源码/WebUI）与回滚
# 操作全部拒绝、无强制继续途径——覆盖 WebUI 四个写入口、/upldm 聊天指令、
# 底层下载/应用漏斗、update_rollback.rollback()（WebUI 回滚与启动参数
# --rollback 共用同一闸门）以及 main.py --rollback 入口本身。
# 插件更新（star/updater、/plugin 指令组）不在管辖范围，保持原样。
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from astrbot.builtin_stars.builtin_commands.commands.admin import AdminCommands
from astrbot.core.updator import AstrBotUpdator
from astrbot.core.utils import update_rollback
from astrbot.core.utils.update_guard import is_update_disabled
from astrbot.dashboard.services.update_service import (
    UpdateService,
    UpdateServiceError,
)

ENV = "LDMBOT_DISABLE_UPDATE"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _默认未启用(monkeypatch):
    """每条用例先清掉该变量，防本机环境泄漏影响判定。"""
    monkeypatch.delenv(ENV, raising=False)
    yield


@pytest.fixture()
def updator(tmp_path, monkeypatch):
    # data 目录指到临时位置，避免元数据写进真实 data/
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(tmp_path / "data"))
    return AstrBotUpdator()


@pytest.fixture()
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(tmp_path / "data"))
    return UpdateService(
        AstrBotUpdator(),
        None,  # core_lifecycle：闸门必须先于任何 lifecycle 访问触发
        get_dashboard_version_func=None,
        pip_install_func=None,
        demo_mode=False,
        clear_site_data_headers={},
    )


@pytest.mark.parametrize("value", ["1", "true", "T", " True "])
def test_truthy_values_enable(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert is_update_disabled()


@pytest.mark.parametrize("value", ["0", "false", "", "no"])
def test_falsy_values_keep_enabled(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    assert not is_update_disabled()


def test_unset_means_enabled():
    assert not is_update_disabled()


def test_download_update_package_refused(updator, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(Exception, match="LDMBOT_DISABLE_UPDATE"):
        asyncio.run(updator.download_update_package(latest=True))


def test_apply_update_package_refused_before_validation(
    updator, monkeypatch, tmp_path
):
    """闸门先于 zip 校验：即便包无效也优先报「已禁用」。"""
    monkeypatch.setenv(ENV, "1")
    dummy = tmp_path / "pkg.zip"
    dummy.write_bytes(b"not a zip")
    with pytest.raises(RuntimeError, match="LDMBOT_DISABLE_UPDATE"):
        updator.apply_update_package(dummy)


def _准备可回滚环境(tmp_path, monkeypatch):
    """构造项目源码 + 一份有效备份，供回滚闸门测试使用。"""
    monkeypatch.delenv("LDMBOT_WEBUI_DIR", raising=False)
    project = tmp_path / "project"
    src = project / "astrbot"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("X = 1", encoding="utf-8")
    # webui_dir 指到临时空目录，避免备份/恢复触碰真实 dashboard/dist
    webui_dir = tmp_path / "webui"
    webui_dir.mkdir()
    data_dir = tmp_path / "data"
    backup = update_rollback.backup_current_version(
        project_root=str(project),
        version="9.9.9",
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )
    assert backup.is_file()
    return project, webui_dir, data_dir


def test_rollback_refused_with_valid_backup(tmp_path, monkeypatch, capsys):
    """有有效备份时仍被拒绝：闸门在任何动作之前触发，源码分毫未动。"""
    monkeypatch.setenv(ENV, "1")
    project, webui_dir, data_dir = _准备可回滚环境(tmp_path, monkeypatch)

    ok = update_rollback.rollback(
        version="9.9.9",
        project_root=str(project),
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )

    assert ok is False
    assert "LDMBOT_DISABLE_UPDATE" in capsys.readouterr().out
    assert (project / "astrbot" / "__init__.py").read_text(
        encoding="utf-8"
    ) == "X = 1"


def test_rollback_allowed_without_env(tmp_path, monkeypatch, capsys):
    """对照组：未启用环境变量时同一份备份可正常回滚，证明上面确为闸门拦截。"""
    project, webui_dir, data_dir = _准备可回滚环境(tmp_path, monkeypatch)

    ok = update_rollback.rollback(
        version="9.9.9",
        project_root=str(project),
        webui_dir=str(webui_dir),
        data_dir=str(data_dir),
    )

    assert ok is True


def test_service_update_project_refused(service, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(UpdateServiceError, match="LDMBOT_DISABLE_UPDATE"):
        asyncio.run(service.update_project({"version": "latest"}))


def test_service_rollback_refused(service, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(UpdateServiceError, match="LDMBOT_DISABLE_UPDATE"):
        asyncio.run(service.rollback_to_version({"version": "9.9.9"}))


def test_service_upload_refused(service, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(UpdateServiceError, match="LDMBOT_DISABLE_UPDATE"):
        asyncio.run(service.update_from_upload(file_bytes=b"PK", filename="a.zip"))


def test_service_apply_upload_refused(service, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(UpdateServiceError, match="LDMBOT_DISABLE_UPDATE"):
        asyncio.run(service.apply_uploaded_package("whatever.zip"))


class _FakeEvent:
    def __init__(self):
        self.chains = []

    async def send(self, chain):
        self.chains.append(chain)


def test_upldm_command_refused(monkeypatch):
    """/upldm 指令直接回拒绝文案，不进入检查/下载流程。"""
    monkeypatch.setenv(ENV, "1")
    admin = AdminCommands(context=None)
    event = _FakeEvent()

    asyncio.run(admin.up_ldm(event))

    assert len(event.chains) == 1
    assert "LDMBOT_DISABLE_UPDATE" in event.chains[0].get_plain_text()


def test_main_rollback_arg_refused(tmp_path):
    """启动参数 --rollback 在 main.py 入口即被拒绝，早于备份列举与交互输入。

    兜底：data 目录指向临时位置——若闸门失效误入启动流程，也不会碰真实 data。
    """
    env = os.environ.copy()
    env[ENV] = "1"
    env["LDMBOT_NO_BANNER"] = "1"
    env["LDMBOT_DATA_DIR"] = str(tmp_path / "data")
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "main.py", "--rollback"],
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 1
    assert "LDMBOT_DISABLE_UPDATE" in proc.stdout
