"""把 stealer 插件数据库里的绝对路径改成本机路径。

背景：从另一台机器（Linux）恢复备份后，emoji.db 里的 path 仍然是
/root/ldmbot/... 这类旧路径。插件启动时 _cleanup_orphans() 用
os.path.isfile() 判定"文件不存在"，会把全部索引记录删掉。

用法（先停掉 AstrBot）：
    python scripts/fix_stealer_paths.py --db <emoji.db> \
        --old-prefix /root/ldmbot/data/ \
        --new-root D:/code/ldm-bot/ldmbot/data

不传 --db 时默认处理 data/plugin_data/astrbot_plugin_stealer/cache/emoji.db。
--dry-run 只报告不改写。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

PATH_TABLES = ("emoji", "emoji_tag", "emoji_scene", "emoji_embedding", "search_refresh")


def rewrite(old: str, old_prefix: str, new_root: Path) -> str:
    rel = old[len(old_prefix):]
    parts = [p for p in rel.split("/") if p]
    return str(new_root.joinpath(*parts))


def main() -> int:
    repo_data = Path(__file__).resolve().parent.parent / "data"
    default_db = repo_data / "plugin_data" / "astrbot_plugin_stealer" / "cache" / "emoji.db"

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(default_db))
    ap.add_argument("--old-prefix", required=True)
    ap.add_argument("--new-root", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"数据库不存在: {db_path}")
        return 1

    new_root = Path(args.new_root)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM emoji").fetchone()[0]
    print(f"数据库: {db_path}  ({total} 条 emoji)")

    old_paths = [r["path"] for r in conn.execute("SELECT path FROM emoji")]
    mapping: dict[str, str] = {}
    skipped = 0
    for old in old_paths:
        if not old.startswith(args.old_prefix):
            skipped += 1
            continue
        mapping[old] = rewrite(old, args.old_prefix, new_root)

    missing = [p for p in mapping.values() if not os.path.isfile(p)]
    print(f"可改写 {len(mapping)} 条, 前缀不匹配 {skipped} 条, 目标缺失 {len(missing)} 个文件")
    for p in missing[:8]:
        print(f"  缺失: {p}")
    if not mapping:
        print("没有需要改写的内容")
        conn.close()
        return 0
    if missing:
        print("目标文件不完整，已中止（避免生成半坏索引）")
        conn.close()
        return 1
    if args.dry_run:
        conn.close()
        return 0

    backup = db_path.with_suffix(db_path.suffix + ".before-pathfix")
    if not backup.exists():
        shutil.copy2(db_path, backup)
        print(f"已备份原库: {backup}")

    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        for table in PATH_TABLES:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not has:
                continue
            # 子表先改，主表后改，避免主键更新时的外键校验
            for old, new in mapping.items():
                conn.execute(f"UPDATE {table} SET path=? WHERE path=?", (new, old))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        raise

    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    left = conn.execute(
        "SELECT COUNT(*) FROM emoji WHERE path LIKE ?", (args.old_prefix + "%",)
    ).fetchone()[0]
    print(f"外键冲突 {len(violations)} 条, 残留旧路径 {left} 条")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    # 校验
    check = sqlite3.connect(db_path)
    n = check.execute("SELECT COUNT(*) FROM emoji").fetchone()[0]
    bad = sum(
        1 for (p,) in check.execute("SELECT path FROM emoji")
        if not os.path.isfile(p)
    )
    tags = check.execute("SELECT COUNT(*) FROM emoji_tag").fetchone()[0]
    scenes = check.execute("SELECT COUNT(*) FROM emoji_scene").fetchone()[0]
    check.close()
    print(f"完成: emoji={n} tag={tags} scene={scenes} 仍缺失文件={bad}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
