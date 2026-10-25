#!/usr/bin/env python3
"""Build the five release assets uploaded to GitHub Releases.

Output (same naming as previous releases):
  ldmbot_<version>.zip
  ldmbot.zip
  ldmbot_install_embedded_<version>.sh
  ldmbot_install_embedded.sh
  ldmbot_install.sh

The embedded installer is the shell template followed by the raw zip bytes;
the script extracts them at runtime with ``tail -c "$ARCHIVE_SIZE"``.

Usage:
  python scripts/release/package_release.py
  python scripts/release/package_release.py --out "D:/somewhere"
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_OUT_DIR = REPO_ROOT.parent / "ldmbot资源"

ARCHIVE_ROOT = "ldmbot"
EXCLUDED_TOP_DIRS = {"data", "data-bak", "__pycache__", "node_modules", ".venv", ".git"}
EXCLUDED_ANY_DIRS = {"__pycache__", "node_modules", ".venv", ".git"}
EXCLUDED_TOP_FILES = {".env", ".env.example", ".gitignore"}
GZIP_SUFFIXES = {
    ".css",
    ".htm",
    ".html",
    ".js",
    ".json",
    ".map",
    ".md",
    ".svg",
    ".txt",
    ".webmanifest",
    ".woff",
    ".xml",
}


def load_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml 中找不到 version")
    return match.group(1)


def normalize_version(value: str) -> str:
    value = value.strip()
    return value[1:] if value[:1].lower() == "v" else value


def validate_tree(version: str) -> None:
    errors: list[str] = []
    init_text = (REPO_ROOT / "astrbot" / "__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in init_text:
        errors.append(f'astrbot/__init__.py 的 __version__ 不是 "{version}"')

    for rel in ("dashboard/dist/version", "dashboard/dist/assets/version"):
        path = REPO_ROOT / rel
        if not path.is_file():
            errors.append(f"缺少 {rel}")
            continue
        if normalize_version(path.read_text(encoding="utf-8")) != version:
            errors.append(f"{rel} 的版本不是 {version}")

    required = (
        "main.py",
        "requirements.txt",
        "uv.lock",
        "dashboard/dist/index.html",
        f"changelogs/v{version}.md",
    )
    for rel in required:
        if not (REPO_ROOT / rel).is_file():
            errors.append(f"缺少 {rel}")

    detail_dir = REPO_ROOT / "详细变更日志" / f"v{version}"
    if not detail_dir.is_dir() or not any(detail_dir.iterdir()):
        errors.append(f"缺少 详细变更日志/v{version}/ 下的明细文件")

    if errors:
        print("发版前检查失败：", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)


def collect_paths() -> tuple[list[str], list[str]]:
    raw = subprocess.check_output(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ]
    )
    existing: list[str] = []
    missing: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = item.decode("utf-8")
        parts = PurePosixPath(rel).parts
        if not parts:
            continue
        if parts[0] in EXCLUDED_TOP_DIRS or parts[0].startswith("data-"):
            continue
        if parts[0].startswith(".") or rel in EXCLUDED_TOP_FILES:
            continue
        if any(part in EXCLUDED_ANY_DIRS for part in parts):
            continue
        if (REPO_ROOT / rel).is_file():
            existing.append(rel)
        else:
            missing.append(rel)
    return sorted(set(existing)), sorted(set(missing))


def zip_datetime(timestamp: float) -> tuple[int, int, int, int, int, int]:
    st = time.localtime(timestamp)
    return (st.tm_year, st.tm_mon, st.tm_mday, st.tm_hour, st.tm_min, st.tm_sec)


def make_zip_info(name: str, is_dir: bool, timestamp: float) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=zip_datetime(timestamp))
    info.create_system = 3  # Unix, same as previous release packages
    if is_dir:
        info.external_attr = (0o40755 << 16) | 0x10
    else:
        info.external_attr = (0o100644 << 16) | 0x20
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def build_zip(zip_path: Path, paths: list[str], include_gzip_assets: bool) -> None:
    tmp_path = zip_path.with_name(zip_path.name + ".tmp")
    dirs: set[str] = {""}
    for rel in paths:
        parent = PurePosixPath(rel).parent
        while str(parent) != ".":
            dirs.add(str(parent))
            parent = parent.parent

    now = time.time()
    with zipfile.ZipFile(
        tmp_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as zf:
        for rel_dir in sorted(dirs):
            name = f"{ARCHIVE_ROOT}/{rel_dir}/" if rel_dir else f"{ARCHIVE_ROOT}/"
            zf.writestr(make_zip_info(name, True, now), b"")

        for rel in paths:
            source = REPO_ROOT / rel
            if not source.is_file():
                raise SystemExit(f"待打包文件不存在: {rel}")
            data = source.read_bytes()
            mtime = source.stat().st_mtime
            zf.writestr(
                make_zip_info(f"{ARCHIVE_ROOT}/{rel}", False, mtime),
                data,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            )

            if (
                include_gzip_assets
                and rel.startswith("dashboard/dist/")
                and PurePosixPath(rel).suffix.lower() in GZIP_SUFFIXES
            ):
                gz_data = gzip.compress(data, compresslevel=6, mtime=0)
                zf.writestr(
                    make_zip_info(f"{ARCHIVE_ROOT}/{rel}.gz", False, mtime),
                    gz_data,
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                )

    os.replace(tmp_path, zip_path)


def verify_zip(zip_path: Path, version: str) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise SystemExit(f"zip 校验失败，损坏条目: {bad}")

        names = set(zf.namelist())
        required = {
            f"{ARCHIVE_ROOT}/main.py",
            f"{ARCHIVE_ROOT}/astrbot/__init__.py",
            f"{ARCHIVE_ROOT}/dashboard/dist/index.html",
            f"{ARCHIVE_ROOT}/dashboard/dist/assets/version",
            f"{ARCHIVE_ROOT}/changelogs/v{version}.md",
        }
        missing = required - names
        if missing:
            raise SystemExit(f"zip 缺少必要条目: {sorted(missing)}")

        for forbidden in (
            f"{ARCHIVE_ROOT}/.git/",
            f"{ARCHIVE_ROOT}/.venv/",
            f"{ARCHIVE_ROOT}/data/",
            f"{ARCHIVE_ROOT}/data-bak/",
            f"{ARCHIVE_ROOT}/__pycache__/",
        ):
            if any(name.startswith(forbidden) for name in names):
                raise SystemExit(f"zip 不应包含 {forbidden}")

        packaged_version = zf.read(
            f"{ARCHIVE_ROOT}/dashboard/dist/assets/version"
        ).decode("utf-8")
        if normalize_version(packaged_version) != version:
            raise SystemExit(
                f"zip 内 WebUI 版本 {packaged_version!r} 与 {version} 不一致"
            )

        for info in zf.infolist():
            if any(ord(ch) > 127 for ch in info.filename) and not (
                info.flag_bits & 0x800
            ):
                raise SystemExit(f"中文条目缺少 UTF-8 标志: {info.filename}")


def build_embedded_installer(
    template_path: Path,
    zip_path: Path,
    output_path: Path,
    version: str,
) -> None:
    template = template_path.read_text(encoding="utf-8")
    zip_size = zip_path.stat().st_size
    header = template.replace("__ARCHIVE_SIZE__", str(zip_size)).replace(
        "__EMBEDDED_VERSION__", version
    )
    if "__ARCHIVE_SIZE__" in header or "__EMBEDDED_VERSION__" in header:
        raise SystemExit("内嵌安装脚本模板仍有未替换的占位符")
    output_path.write_bytes(header.encode("utf-8") + zip_path.read_bytes())


def verify_embedded_installer(
    installer_path: Path,
    zip_path: Path,
    version: str,
) -> None:
    data = installer_path.read_bytes()
    zip_bytes = zip_path.read_bytes()
    if not data.endswith(zip_bytes):
        raise SystemExit(f"{installer_path.name} 末尾没有完整的 zip 数据")
    header = data[: len(data) - len(zip_bytes)].decode("utf-8")
    if f"ARCHIVE_SIZE={len(zip_bytes)}" not in header:
        raise SystemExit(f"{installer_path.name} 的 ARCHIVE_SIZE 不正确")
    if f'EMBEDDED_VERSION="{version}"' not in header:
        raise SystemExit(f"{installer_path.name} 的 EMBEDDED_VERSION 不正确")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"输出目录（默认 {DEFAULT_OUT_DIR}）",
    )
    parser.add_argument("--version", default=None, help="覆盖 pyproject.toml 中的版本")
    parser.add_argument(
        "--gzip-assets",
        action="store_true",
        help="预生成 dashboard/dist 下的 .gz 静态缓存（默认不生成，服务端首次访问会自动生成）",
    )
    args = parser.parse_args()

    version = args.version or load_version()
    validate_tree(version)
    paths, missing = collect_paths()
    if not paths:
        raise SystemExit("没有收集到任何待打包文件")
    if missing:
        preview = "、".join(missing[:3])
        suffix = " 等" if len(missing) > 3 else ""
        print(f"跳过 {len(missing)} 个已删除/不存在的文件（{preview}{suffix}）")

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    zip_versioned = out_dir / f"ldmbot_{version}.zip"
    zip_unversioned = out_dir / "ldmbot.zip"
    embedded_versioned = out_dir / f"ldmbot_install_embedded_{version}.sh"
    embedded_unversioned = out_dir / "ldmbot_install_embedded.sh"
    install_mirror = out_dir / "ldmbot_install.sh"
    latest_zip = out_dir / "latest.zip"

    print(f"版本: {version}")
    print(f"输出目录: {out_dir}")
    print(f"待打包文件: {len(paths)}")

    build_zip(zip_versioned, paths, args.gzip_assets)
    verify_zip(zip_versioned, version)
    shutil.copyfile(zip_versioned, zip_unversioned)
    shutil.copyfile(zip_versioned, latest_zip)

    build_embedded_installer(
        TEMPLATE_DIR / "install_embedded.header.sh",
        zip_versioned,
        embedded_versioned,
        version,
    )
    verify_embedded_installer(embedded_versioned, zip_versioned, version)
    shutil.copyfile(embedded_versioned, embedded_unversioned)

    shutil.copyfile(TEMPLATE_DIR / "install_mirror.sh", install_mirror)

    assets = (
        zip_unversioned,
        zip_versioned,
        install_mirror,
        embedded_unversioned,
        embedded_versioned,
    )
    print("\n生成完成:")
    for asset in assets:
        print(f"  {asset.name}  {asset.stat().st_size}  bytes  {sha256(asset)}")
    print(f"\n镜像用副本: {latest_zip.name}（上传到 MIRROR_BASE 时改名为 latest.zip）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
