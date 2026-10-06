"""把根目录 CHANGELOG.md 的每个版本章节拆成 changelogs/v<版本>.md。

WebUI「更新日志」弹窗走官方 AstrBot 的方式：后端从 changelogs/ 目录
逐版本读取（/api/v1/changelogs），每个版本一个文件；CHANGELOG.md 本身
保留不动，继续作为发版说明的原始记录（GitHub 上也直接看它）。

用法（在项目根目录执行）：
    python scripts/split_changelog.py

CHANGELOG.md 的版本章节格式（由发版时手工维护）：

    <details>
    <summary><strong>[4.27.13] — 2026-08-28</strong> — 标题</summary>

    正文……
    </details>

拆分规则：
- 每个 <details> 版本块生成 changelogs/v<版本>.md，内容为
  「# [版本] — 日期 — 标题」加正文，正文原样保留；
- 重复执行会重新生成全部版本文件（以 CHANGELOG.md 为准覆盖）；
- 末尾不带版本号的章节（如「## 扩展功能增强」）不属于任何版本，
  不拆分，只在 CHANGELOG.md 里保留。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# <summary><strong>[版本] — 日期</strong> — 标题</summary>（标题可省略）
_SUMMARY_RE = re.compile(
    r"^<summary><strong>\[(?P<version>[^\]]+)\] — (?P<date>[^<]+?)</strong>"
    r"(?: — (?P<title>.*?))?</summary>\s*$",
    re.DOTALL,
)


def split_changelog(changelog_path: Path, out_dir: Path) -> list[Path]:
    text = changelog_path.read_text(encoding="utf-8")

    written: list[Path] = []
    seen_versions: set[str] = set()
    pos = 0
    while True:
        start = text.find("<details>", pos)
        if start == -1:
            break
        end = text.find("</details>", start)
        if end == -1:
            raise SystemExit(f"CHANGELOG.md 第 {text[:start].count(chr(10)) + 1} 行的 <details> 没有对应的 </details>。")
        pos = end + len("</details>")

        block = text[start + len("<details>") : end]
        # 块内第一行是 <summary>，其余为正文
        block = block.lstrip("\n")
        first_line_end = block.find("\n")
        summary = block[:first_line_end].strip() if first_line_end != -1 else block.strip()
        body = block[first_line_end + 1 :] if first_line_end != -1 else ""

        match = _SUMMARY_RE.match(summary)
        if not match:
            raise SystemExit(f"无法解析 <summary> 行（应为 [版本] — 日期 — 标题 格式）：{summary!r}")
        version = match.group("version").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", version):
            raise SystemExit(f"版本号含非法字符，跳过会破坏文件名：{version!r}")
        if version in seen_versions:
            raise SystemExit(f"CHANGELOG.md 中存在重复版本号：{version}")
        seen_versions.add(version)

        date = match.group("date").strip()
        title = (match.group("title") or "").strip()
        heading = f"# [{version}] — {date}" + (f" — {title}" if title else "")

        content = heading + "\n\n" + body.strip("\n") + "\n"
        out_file = out_dir / f"v{version}.md"
        out_file.write_text(content, encoding="utf-8", newline="\n")
        written.append(out_file)

    return written


def main() -> None:
    changelog_path = PROJECT_ROOT / "CHANGELOG.md"
    out_dir = PROJECT_ROOT / "changelogs"
    if not changelog_path.is_file():
        raise SystemExit(f"未找到 {changelog_path}。")
    out_dir.mkdir(exist_ok=True)

    written = split_changelog(changelog_path, out_dir)
    print(f"已从 CHANGELOG.md 拆出 {len(written)} 个版本文件到 {out_dir}：")
    for path in written:
        print(f"  {path.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
