# WebUI「立即备份」服务方法测试
#
# 范围：UpdateService.backup_current_state 把当前版本源码打包到回滚目录
# （含上一变更补全的根目录文件与 dist），返回带文件名的成功消息；
# 核心更新任务进行中拒绝；禁更环境变量 LDMBOT_DISABLE_UPDATE 不拦截
# 立即备份——备份只读打包、不改源码，与更新/回滚（会改源码）不同。
import asyncio
import zipfile

import pytest

from astrbot.core.config.default import VERSION
from astrbot.core.updator import AstrBotUpdator
from astrbot.core.utils import update_rollback
from astrbot.dashboard.services.update_service import (
    UpdateService,
    UpdateServiceError,
)


@pytest.fixture(autouse=True)
def _清理环境(monkeypatch):
    """防本机环境变量泄漏影响目录解析与禁更闸门。"""
    monkeypatch.delenv("LDMBOT_WEBUI_DIR", raising=False)
    monkeypatch.delenv("LDMBOT_DISABLE_UPDATE", raising=False)
    yield


def _服务(tmp_path, monkeypatch):
    """构造指向临时项目/数据目录的 UpdateService，不打包真实源码与 data/。"""
    project = tmp_path / "project"
    (project / "astrbot").mkdir(parents=True)
    (project / "astrbot" / "__init__.py").write_text("X = 1", encoding="utf-8")
    (project / "main.py").write_text("print('main')", encoding="utf-8")
    (project / "requirements.txt").write_text("astrbot==1.0.0", encoding="utf-8")
    webui = tmp_path / "webui"
    webui.mkdir()
    (webui / "version").write_text("v9.9.9", encoding="utf-8")
    data_dir = tmp_path / "data"

    monkeypatch.setenv("LDMBOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LDMBOT_WEBUI_DIR", str(webui))
    updator = AstrBotUpdator()
    updator.MAIN_PATH = str(project)
    service = UpdateService(
        updator,
        None,
        get_dashboard_version_func=None,
        pip_install_func=None,
        demo_mode=False,
        clear_site_data_headers={},
    )
    return service, data_dir


def test_backup_current_state_creates_zip(tmp_path, monkeypatch):
    """立即备份生成 ldmbot_<当前版本>.zip，消息带文件名，根目录文件在包内。"""
    service, data_dir = _服务(tmp_path, monkeypatch)

    result = asyncio.run(service.backup_current_state())

    assert result.status == "ok"
    assert result.data["filename"] == f"ldmbot_{VERSION}.zip"
    assert f"ldmbot_{VERSION}.zip" in result.message
    backups = update_rollback.list_backups(str(data_dir))
    assert len(backups) == 1
    assert backups[0].name == f"ldmbot_{VERSION}.zip"
    with zipfile.ZipFile(backups[0]) as zf:
        名字们 = set(zf.namelist())
    assert "main.py" in 名字们
    assert "requirements.txt" in 名字们
    assert "astrbot/__init__.py" in 名字们
    assert "dist/version" in 名字们


def test_backup_current_state_refused_while_updating(tmp_path, monkeypatch):
    """核心更新任务进行中拒绝立即备份，不产生备份文件。"""
    service, data_dir = _服务(tmp_path, monkeypatch)
    service._core_update_running = True

    with pytest.raises(UpdateServiceError, match="更新任务"):
        asyncio.run(service.backup_current_state())

    assert update_rollback.list_backups(str(data_dir)) == []


def test_backup_current_state_allowed_with_disable_update(tmp_path, monkeypatch):
    """禁更环境变量不拦截立即备份（只读操作，与更新/回滚不同）。"""
    monkeypatch.setenv("LDMBOT_DISABLE_UPDATE", "1")
    service, data_dir = _服务(tmp_path, monkeypatch)

    result = asyncio.run(service.backup_current_state())

    assert result.status == "ok"
    assert len(update_rollback.list_backups(str(data_dir))) == 1
