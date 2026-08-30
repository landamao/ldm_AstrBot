"""人格系统提示词副本（查看 / 备份 / 双向时间同步）。

主数据默认以数据库 ``personas.system_prompt`` 为准，同时在
``data/persona_prompts/<人格名>.txt`` 维护一份可人工查看/备份的副本。

当数据库的 ``system_prompt_stored_at`` 与本地文件 mtime 相差超过 1 秒时：
- 较新的一侧覆盖较旧的一侧；
- 若时间在 1 秒内，则不读内容、不覆盖；
- 若时间差 > 1 秒，会再比对内容；内容不一致才同步。

直接改本地文件是允许的，会在 LLM 取人格 / WebUI 登录 / 启动全量检查时
被识别并写回数据库（若文件更新）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from astrbot import logger
from astrbot.core.utils.astrbot_path import get_astrbot_persona_prompts_path

# Windows / 跨平台不安全字符；路径分隔符也去掉
_不安全文件名字符 = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_README_文件名 = "README.md"
_注意事项_文件名 = "注意事项.md"
_时间容差秒 = 1.0
_说明内容 = """# 人格提示词副本目录

## 说明

1. 这里是人格系统提示词的本地副本，可用于**查看、备份和编辑**。
2. 可以在这里直接编辑对应人格的 `.txt` 提示词内容。
3. **新建人格、删除人格请在 WebUI 操作**，不要在本目录手动新建/删除文件来管理人格。
4. WebUI 修改人格提示词后，会同步更新本目录对应 `.txt`。
5. 在本目录改过提示词后：下次 LLM 使用该人格、登录 WebUI、打开该人格详情、或启动检查时，
   会按修改时间与数据库比对（允许 1 秒误差），较新且内容不同的一侧会覆盖另一侧。
6. 文件名 = 人格名（persona_id），内容 = 系统提示词（UTF-8，保留真实换行）。
7. 说明文件为 `README.md`、`注意事项.md`，与人格提示词 `.txt` 后缀区分。

## 同步规则

- 比较：数据库 `system_prompt_stored_at` ↔ 文件 mtime
- 允许 1 秒误差：差值 ≤ 1 秒视为同时，不覆盖
- 差值 > 1 秒：再比对内容；内容不一致时以**较新**一侧覆盖较旧一侧
- 文件不存在：从数据库写出
- 数据库无该人格：不会因本地文件自动创建人格

## 注意事项

- 新建 / 删除人格：请到 WebUI 人格管理操作。
- 编辑提示词：可在 WebUI，也可直接改本目录对应 `.txt`。
- 人格名含 `/ : * ?` 等不安全字符时，文件名会替换为 `_`。
- 同步失败只记日志，不阻断聊天 / 登录。
"""
# 受保护的说明文件，不会被孤儿清理删除
_受保护说明文件 = frozenset({_README_文件名, _注意事项_文件名})


@dataclass
class 同步结果:
    """单个人格的同步结果。"""

    action: str  # none / wrote_file / wrote_db / created_file
    persona_id: str
    changed: bool = False
    message: str = ""


def get_persona_prompts_dir() -> Path:
    return Path(get_astrbot_persona_prompts_path())


def sanitize_persona_filename(persona_id: str) -> str:
    """把人格名变成可落盘文件名（尽量保持原名）。"""
    name = (persona_id or "").strip()
    if not name:
        return "_unnamed"
    name = _不安全文件名字符.sub("_", name)
    name = name.rstrip(" .")
    return name or "_unnamed"


def persona_prompt_path(persona_id: str) -> Path:
    # 纯文本，保留真实换行；.txt 便于编辑器直接打开
    return get_persona_prompts_dir() / f"{sanitize_persona_filename(persona_id)}.txt"


def ensure_persona_prompts_dir() -> Path:
    directory = get_persona_prompts_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for 文件名 in (_README_文件名, _注意事项_文件名):
        path = directory / 文件名
        try:
            # 说明文案可能随版本更新，始终覆盖写入最新内容
            path.write_text(_说明内容, encoding="utf-8", newline="\n")
        except OSError as exc:
            logger.warning("写入人格提示词副本说明文件失败 %s: %s", 文件名, exc)
    return directory


def normalize_prompt_text(content: str | None) -> str:
    """统一换行，避免 \\r\\n / \\r 干扰比对。"""
    text = content if content is not None else ""
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    elif "\r" in text:
        text = text.replace("\r", "\n")
    return text


def _to_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        # SQLite 常返回 naive UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _datetime_to_epoch(dt: datetime | None) -> float | None:
    aware = _to_aware_utc(dt)
    if aware is None:
        return None
    return aware.timestamp()


def _epoch_to_utc_datetime(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def write_persona_prompt_mirror(
    persona_id: str,
    system_prompt: str | None,
    *,
    mtime: float | datetime | None = None,
) -> float | None:
    """按内容写入副本；可指定 mtime（epoch 或 datetime）。

    Returns:
        写入后的文件 mtime epoch；失败返回 None。
    """
    if not persona_id:
        return None
    try:
        ensure_persona_prompts_dir()
        path = persona_prompt_path(persona_id)
        content = normalize_prompt_text(system_prompt)
        path.write_text(content, encoding="utf-8", newline="\n")
        if mtime is not None:
            if isinstance(mtime, datetime):
                epoch = _datetime_to_epoch(mtime)
            else:
                epoch = float(mtime)
            if epoch is not None:
                os.utime(path, (epoch, epoch))
        return path.stat().st_mtime
    except OSError as exc:
        logger.warning(
            "写入人格提示词副本失败 persona_id=%s path=%s: %s",
            persona_id,
            persona_prompt_path(persona_id),
            exc,
        )
        return None


def delete_persona_prompt_mirror(persona_id: str) -> None:
    """删除对应副本文件。失败只记日志。"""
    if not persona_id:
        return
    try:
        path = persona_prompt_path(persona_id)
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.warning(
            "删除人格提示词副本失败 persona_id=%s path=%s: %s",
            persona_id,
            persona_prompt_path(persona_id),
            exc,
        )


def read_persona_prompt_file(persona_id: str) -> tuple[str | None, float | None]:
    """读取本地副本内容与 mtime。不存在返回 (None, None)。"""
    path = persona_prompt_path(persona_id)
    try:
        if not path.exists() or not path.is_file():
            return None, None
        st = path.stat()
        text = normalize_prompt_text(path.read_text(encoding="utf-8"))
        return text, st.st_mtime
    except OSError as exc:
        logger.warning(
            "读取人格提示词副本失败 persona_id=%s path=%s: %s",
            persona_id,
            path,
            exc,
        )
        return None, None


def decide_sync_direction(
    *,
    db_epoch: float | None,
    file_epoch: float | None,
    db_text: str,
    file_text: str | None,
    file_exists: bool,
) -> str:
    """决定同步方向。

    Returns:
        none | write_file | write_db | create_file
    """
    db_text = normalize_prompt_text(db_text)
    if not file_exists or file_text is None or file_epoch is None:
        return "create_file"

    file_text_n = normalize_prompt_text(file_text)

    # 没有 DB 时间：只能靠内容；内容不同默认以 DB 为准写文件（兼容旧数据）
    if db_epoch is None:
        if db_text == file_text_n:
            return "none"
        return "write_file"

    delta = abs(db_epoch - file_epoch)
    if delta <= _时间容差秒:
        return "none"

    if db_text == file_text_n:
        # 内容已一致，仅时间漂了：把文件 mtime 对齐到 DB，避免反复判定
        return "align_file_mtime"

    if file_epoch > db_epoch:
        return "write_db"
    return "write_file"


def reconcile_persona_prompt(
    persona,
) -> 同步结果:
    """对单个人格做 DB ↔ 文件 时间/内容同步（同步函数，不写 DB）。

    若需要写回 DB，返回 action=write_db 且 result 里带 new_prompt / new_stored_at，
    由调用方执行数据库更新（避免本模块反向依赖 DB）。
    """
    persona_id = getattr(persona, "persona_id", None) or ""
    if not persona_id:
        return 同步结果(action="none", persona_id="", message="empty persona_id")

    db_text = normalize_prompt_text(getattr(persona, "system_prompt", "") or "")
    db_epoch = _datetime_to_epoch(getattr(persona, "system_prompt_stored_at", None))
    file_text, file_epoch = read_persona_prompt_file(persona_id)
    file_exists = file_text is not None and file_epoch is not None

    direction = decide_sync_direction(
        db_epoch=db_epoch,
        file_epoch=file_epoch,
        db_text=db_text,
        file_text=file_text,
        file_exists=file_exists,
    )

    if direction == "none":
        return 同步结果(action="none", persona_id=persona_id, changed=False)

    if direction == "create_file":
        mtime = write_persona_prompt_mirror(
            persona_id,
            db_text,
            mtime=db_epoch,
        )
        return 同步结果(
            action="created_file",
            persona_id=persona_id,
            changed=True,
            message=f"created mirror mtime={mtime}",
        )

    if direction == "align_file_mtime":
        # 内容一致，只对齐文件时间到 DB 存储时间
        mtime = write_persona_prompt_mirror(
            persona_id,
            db_text,
            mtime=db_epoch,
        )
        return 同步结果(
            action="aligned_file_mtime",
            persona_id=persona_id,
            changed=False,
            message=f"aligned mtime={mtime}",
        )

    if direction == "write_file":
        mtime = write_persona_prompt_mirror(
            persona_id,
            db_text,
            mtime=db_epoch if db_epoch is not None else None,
        )
        return 同步结果(
            action="wrote_file",
            persona_id=persona_id,
            changed=True,
            message=f"db→file mtime={mtime}",
        )

    if direction == "write_db":
        # 文件更新：调用方负责写 DB；这里只返回需要写入的内容与时间
        assert file_text is not None and file_epoch is not None
        return 同步结果(
            action="write_db",
            persona_id=persona_id,
            changed=True,
            message=f"file→db file_mtime={file_epoch}",
        )

    return 同步结果(action="none", persona_id=persona_id)


def extract_file_prompt_for_db_write(persona_id: str) -> tuple[str, datetime] | None:
    """读取文件侧待写回 DB 的 (prompt, stored_at)。失败返回 None。"""
    file_text, file_epoch = read_persona_prompt_file(persona_id)
    if file_text is None or file_epoch is None:
        return None
    return file_text, _epoch_to_utc_datetime(file_epoch)


def prune_orphan_persona_prompt_files(expected_filenames: set[str]) -> None:
    """删除库中已不存在的孤儿副本（不删说明文件）。"""
    try:
        directory = get_persona_prompts_dir()
        if not directory.exists():
            return
        for path in directory.glob("*.txt"):
            if path.name in _受保护说明文件:
                continue
            if path.name not in expected_filenames:
                try:
                    path.unlink()
                except OSError as exc:
                    logger.warning("清理孤儿人格提示词副本失败 %s: %s", path, exc)
    except OSError as exc:
        logger.warning("清理孤儿人格提示词副本目录失败: %s", exc)


def sync_persona_prompt_mirrors(
    personas: list,
    *,
    prune_orphans: bool = True,
) -> None:
    """兼容旧调用：仅把数据库内容写出到文件（不读回）。

    双向同步请用 PersonaManager 的 reconcile 方法。
    """
    try:
        ensure_persona_prompts_dir()
        expected: set[str] = set()
        for persona in personas:
            persona_id = getattr(persona, "persona_id", None)
            if not persona_id:
                continue
            stored_at = getattr(persona, "system_prompt_stored_at", None)
            write_persona_prompt_mirror(
                persona_id,
                getattr(persona, "system_prompt", "") or "",
                mtime=stored_at,
            )
            expected.add(persona_prompt_path(persona_id).name)

        if prune_orphans:
            prune_orphan_persona_prompt_files(expected)
    except Exception as exc:  # noqa: BLE001 — 副本同步绝不能拖垮主流程
        logger.warning("同步人格提示词副本失败: %s", exc)
