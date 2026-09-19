"""备份/恢复闭环回归测试。

覆盖:
- 导出→导入闭环:主库数据、配置文件(cmd_config/mcp_server/skills)、附件、目录
- manifest 完整性字段(has_config / has_knowledge_bases / checksums / export_errors)
- 导入前 checksum 校验:篡改备份直接拒绝,且不清库
- 主库导入失败回滚:清空与导入同事务,失败后现有数据保持原样
- 附件跨环境恢复:path 重写到当前附件目录并同步数据库
- 目录导入原子性:解压暂存目录成功后才替换旧目录
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from astrbot.core.backup.exporter import AstrBotExporter
from astrbot.core.backup.importer import AstrBotImporter
from astrbot.core.db.po import Attachment, Persona
from astrbot.core.db.sqlite import SQLiteDatabase


@pytest.fixture
def data_env(tmp_path, monkeypatch):
    """隔离的 data 目录;get_backup_directories() 等运行时函数跟随该环境变量。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(data_dir))
    return data_dir


@pytest_asyncio.fixture
async def db(tmp_path):
    database = SQLiteDatabase(str(tmp_path / "data_v4.db"))
    await database.initialize()
    yield database
    await database.engine.dispose()


async def _add_persona(db: SQLiteDatabase, persona_id: str) -> None:
    async with db.get_db() as session:
        async with session.begin():
            session.add(Persona(persona_id=persona_id, system_prompt=f"prompt-{persona_id}"))


async def _count_personas(db: SQLiteDatabase) -> int:
    async with db.get_db() as session:
        result = await session.execute(select(func.count()).select_from(Persona))
        return result.scalar_one()


def _seed_files(data_dir: Path) -> None:
    (data_dir / "cmd_config.json").write_text(
        json.dumps({"dashboard": {"port": 6185}}, ensure_ascii=False), encoding="utf-8"
    )
    (data_dir / "mcp_server.json").write_text(
        json.dumps({"mcpServers": {"srv1": {"url": "http://x"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "skills.json").write_text(
        json.dumps({"skills": {"s1": {"active": True}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    plugin_dir = data_dir / "plugins" / "fake_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "main.py").write_text("# plugin", encoding="utf-8")


def _rewrite_zip(src: str, dst: str, mutate) -> None:
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item.filename, mutate(item.filename, zin.read(item.filename)))


@pytest.mark.asyncio
async def test_export_import_roundtrip(tmp_path, data_env, db):
    await _add_persona(db, "p1")
    _seed_files(data_env)

    # 附件记录 + 文件
    attachments_dir = data_env / "attachments"
    attachments_dir.mkdir()
    attachment_file = attachments_dir / "a1.png"
    attachment_file.write_bytes(b"png-bytes")
    await db.insert_attachment(str(attachment_file), "image", "image/png")

    exporter = AstrBotExporter(
        main_db=db,
        kb_manager=None,
        config_path=str(data_env / "cmd_config.json"),
    )
    zip_path = await exporter.export_all(output_dir=str(tmp_path / "backups"))

    with zipfile.ZipFile(zip_path) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert "config/mcp_server.json" in zf.namelist()
        assert "config/skills.json" in zf.namelist()
        assert "directories/plugins/fake_plugin/main.py" in zf.namelist()
    assert manifest["has_config"] is True
    assert manifest["has_knowledge_bases"] is False
    assert manifest["export_errors"] == []
    assert "databases/main_db.json" in manifest["checksums"]
    assert set(manifest["files"]["config"]) == {"cmd_config.json", "mcp_server.json", "skills.json"}

    # 模拟数据丢失:清表、删附件文件与配置文件

    async with db.get_db() as session:
        async with session.begin():
            for persona in (await session.execute(select(Persona))).scalars().all():
                await session.delete(persona)
    attachment_file.unlink()
    (data_env / "mcp_server.json").unlink()

    importer = AstrBotImporter(
        main_db=db,
        kb_manager=None,
        config_path=str(data_env / "cmd_config.json"),
        kb_root_dir=str(data_env / "knowledge_base"),
    )
    result = await importer.import_all(zip_path, mode="replace")
    assert result.success, result.to_dict()

    assert await _count_personas(db) == 1
    assert attachment_file.exists() and attachment_file.read_bytes() == b"png-bytes"
    assert json.loads((data_env / "mcp_server.json").read_text(encoding="utf-8"))["mcpServers"]["srv1"]["url"] == "http://x"
    assert json.loads((data_env / "skills.json").read_text(encoding="utf-8"))["skills"]["s1"]["active"] is True
    assert (data_env / "plugins" / "fake_plugin" / "main.py").exists()


@pytest.mark.asyncio
async def test_import_rejects_corrupted_backup(tmp_path, data_env, db):
    await _add_persona(db, "p1")
    _seed_files(data_env)

    exporter = AstrBotExporter(
        main_db=db, kb_manager=None, config_path=str(data_env / "cmd_config.json")
    )
    zip_path = await exporter.export_all(output_dir=str(tmp_path / "backups"))

    def mutate(name: str, data: bytes) -> bytes:
        if name == "databases/main_db.json":
            return json.dumps({"personas": []}).encode("utf-8")
        return data

    corrupted = str(tmp_path / "corrupted.zip")
    _rewrite_zip(zip_path, corrupted, mutate)

    importer = AstrBotImporter(
        main_db=db,
        kb_manager=None,
        config_path=str(data_env / "cmd_config.json"),
        kb_root_dir=str(data_env / "knowledge_base"),
    )
    result = await importer.import_all(corrupted, mode="replace")
    assert not result.success
    assert any("校验和不匹配" in e for e in result.errors)
    # 关键:校验失败必须发生在清库之前,现有数据保持原样
    assert await _count_personas(db) == 1


@pytest.mark.asyncio
async def test_maindb_import_failure_rolls_back_clear(tmp_path, data_env, db, monkeypatch):
    await _add_persona(db, "p1")
    _seed_files(data_env)

    exporter = AstrBotExporter(
        main_db=db, kb_manager=None, config_path=str(data_env / "cmd_config.json")
    )
    zip_path = await exporter.export_all(output_dir=str(tmp_path / "backups"))

    # 导入期间本地又写入一条新数据
    await _add_persona(db, "local-only")

    importer = AstrBotImporter(
        main_db=db,
        kb_manager=None,
        config_path=str(data_env / "cmd_config.json"),
        kb_root_dir=str(data_env / "knowledge_base"),
    )
    # 让导入在事务中途抛错,验证清空+导入同事务、失败整体回滚
    def _boom(self, table_name, rows):
        raise RuntimeError("boom")

    monkeypatch.setattr(AstrBotImporter, "_preprocess_main_table_rows", _boom)
    result = await importer.import_all(zip_path, mode="replace")
    assert not result.success
    assert any("已回滚" in e for e in result.errors)
    # 清空被回滚:原有两条记录都在
    assert await _count_personas(db) == 2


@pytest.mark.asyncio
async def test_attachment_path_rewrite_cross_environment(tmp_path, monkeypatch):
    # 源环境 A:备份包含附件,记录指向 A 的绝对路径
    data_dir_a = tmp_path / "machine_a" / "data"
    attachments_dir_a = data_dir_a / "attachments"
    attachments_dir_a.mkdir(parents=True)
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(data_dir_a))

    db_a = SQLiteDatabase(str(tmp_path / "machine_a" / "data_v4.db"))
    await db_a.initialize()
    attachment_file_a = attachments_dir_a / "a1.png"
    attachment_file_a.write_bytes(b"png-bytes")
    await db_a.insert_attachment(str(attachment_file_a), "image", "image/png")

    exporter = AstrBotExporter(
        main_db=db_a, kb_manager=None, config_path=str(data_dir_a / "cmd_config.json")
    )
    zip_path = await exporter.export_all(output_dir=str(tmp_path / "backups"))
    await db_a.engine.dispose()

    # 目标环境 B:完全不同的安装路径
    data_dir_b = tmp_path / "machine_b" / "data"
    data_dir_b.mkdir(parents=True)
    monkeypatch.setenv("LDMBOT_DATA_DIR", str(data_dir_b))
    db_b = SQLiteDatabase(str(tmp_path / "machine_b" / "data_v4.db"))
    await db_b.initialize()

    importer = AstrBotImporter(
        main_db=db_b,
        kb_manager=None,
        config_path=str(data_dir_b / "cmd_config.json"),
        kb_root_dir=str(data_dir_b / "knowledge_base"),
    )
    result = await importer.import_all(zip_path, mode="replace")
    assert result.success, result.to_dict()

    # 附件落到 B 的附件目录,数据库 path 同步重写
    new_file = data_dir_b / "attachments" / "a1.png"
    assert new_file.exists() and new_file.read_bytes() == b"png-bytes"
    async with db_b.get_db() as session:
        from sqlalchemy import select

        rows = (await session.execute(select(Attachment))).scalars().all()
    assert len(rows) == 1
    assert Path(rows[0].path) == new_file
    await db_b.engine.dispose()


@pytest.mark.asyncio
async def test_directory_import_is_atomic(tmp_path, data_env, db):
    _seed_files(data_env)
    # 现有插件目录中有旧文件
    old_file = data_env / "plugins" / "fake_plugin" / "old.py"
    old_file.write_text("# old", encoding="utf-8")

    exporter = AstrBotExporter(
        main_db=db, kb_manager=None, config_path=str(data_env / "cmd_config.json")
    )
    zip_path = await exporter.export_all(output_dir=str(tmp_path / "backups"))

    # 模拟恢复到空目录:旧目录整体不在
    import shutil

    shutil.rmtree(data_env / "plugins")

    importer = AstrBotImporter(
        main_db=db,
        kb_manager=None,
        config_path=str(data_env / "cmd_config.json"),
        kb_root_dir=str(data_env / "knowledge_base"),
    )
    result = await importer.import_all(zip_path, mode="replace")
    assert result.success, result.to_dict()

    assert (data_env / "plugins" / "fake_plugin" / "main.py").exists()
    # 暂存目录已被清理,不会残留在 data 下
    assert not (data_env / ".plugins.importing").exists()


# ---------------------------------------------------------------------------
# 2026-09 冻结事故回归:同秒同名覆盖 / 并发导出 / 死连接日志刷屏限频
# (导出期间阻塞事件循环是有意设计:备份独占数据、不受干扰,不做"解阻塞")
# ---------------------------------------------------------------------------


def test_export_zip_path_collision(tmp_path):
    """同秒多次导出不得互相覆盖:已存在时追加序号而不是截断重写。"""
    from astrbot.core.backup.exporter import resolve_export_zip_path

    first = resolve_export_zip_path(str(tmp_path), "20260915_001835", "")
    assert first.endswith("ldmbot_backup_20260915_001835.zip")
    Path(first).write_bytes(b"x")

    second = resolve_export_zip_path(str(tmp_path), "20260915_001835", "")
    assert second.endswith("ldmbot_backup_20260915_001835_1.zip")
    Path(second).write_bytes(b"x")
    assert resolve_export_zip_path(str(tmp_path), "20260915_001835", "").endswith(
        "ldmbot_backup_20260915_001835_2.zip"
    )
    # 不同秒不受影响
    fresh = resolve_export_zip_path(str(tmp_path), "20260915_001836", "")
    assert fresh.endswith("ldmbot_backup_20260915_001836.zip")


def test_asyncio_conn_lost_spam_filter():
    """死连接刷屏告警 10 秒内只放行一条,其余消息不受影响。"""
    import logging as _logging

    from astrbot.core.log import _AsyncioConnLostSpamFilter

    spam_filter = _AsyncioConnLostSpamFilter()

    def _record(msg: str) -> _logging.LogRecord:
        return _logging.LogRecord(
            name="asyncio",
            level=_logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

    spam = "socket.send() raised exception."
    assert spam_filter.filter(_record(spam)) is True  # 第一条放行
    assert spam_filter.filter(_record(spam)) is False  # 窗口内丢弃
    assert spam_filter.filter(_record("Other asyncio message")) is True  # 其他消息放行

    # 时间前进超过窗口后重新放行
    spam_filter._last_allowed -= 11.0
    assert spam_filter.filter(_record(spam)) is True


@pytest.mark.asyncio
async def test_export_rejects_concurrent_task(tmp_path, data_env, db, monkeypatch):
    """已有导出任务进行中时,再次导出必须被拒绝而不是排队覆盖。"""
    from types import SimpleNamespace

    from astrbot.dashboard.services.backup_service import (
        BackupService,
        BackupServiceError,
    )

    service = BackupService(db, SimpleNamespace(astrbot_config={}))
    service.backup_dir = str(tmp_path / "backups")

    service._init_task("t0", "export", "processing")
    with pytest.raises(BackupServiceError, match="正在进行中"):
        service.export_backup({})

    # 任务结束后防护解除
    service._set_task_result("t0", "completed")

    # 无进行中任务时可正常创建(拦截 create_task,避免后台真跑导出)
    created: dict = {}
    monkeypatch.setattr(
        "astrbot.dashboard.services.backup_service.asyncio.create_task",
        lambda coro, **kwargs: created.setdefault("coro", coro),
    )
    result = service.export_backup({})
    assert result["task_id"]
    if "coro" in created:
        created["coro"].close()  # 未执行的协程显式关闭,避免告警
