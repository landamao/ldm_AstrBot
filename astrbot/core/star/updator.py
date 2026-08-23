import asyncio
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import yaml

from astrbot.core import logger
from astrbot.core.repository import (
    GitUnavailableError,
    normalize_repository_url,
    parse_repository_url,
)
from astrbot.core.utils.astrbot_path import (
    get_astrbot_plugin_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.io import ensure_dir, remove_dir

from ..star.star import StarMetadata
from ..updator import RepoZipUpdator

PLUGIN_METADATA_FILENAMES = ("metadata.yaml", "metadata.yml")
PLUGIN_METADATA_REQUIRED_FIELDS = ("name", "desc", "version", "author")
PLUGIN_METADATA_MAX_BYTES = 1024 * 1024
PLUGIN_REPOSITORY_TIMEOUT_SECONDS = 15
PLUGIN_GIT_CLONE_TIMEOUT_SECONDS = 180


class PluginUpdator(RepoZipUpdator):
    def __init__(self, repo_mirror: str = "", verify: str | bool | None = None) -> None:
        super().__init__(repo_mirror, verify=verify)
        self.plugin_store_path = get_astrbot_plugin_path()

    def get_plugin_store_path(self) -> str:
        return self.plugin_store_path

    async def _clone_repository(self, repo_url: str, target_path: str | Path) -> None:
        """浅克隆远程 Git 仓库，不保留 .git 元数据。"""
        git_executable = shutil.which("git")
        if not git_executable:
            raise GitUnavailableError(
                "安装此仓库需要 Git，但当前运行环境中未找到 git 命令。"
            )

        target = Path(target_path)
        if target.exists():
            raise RuntimeError(f"Git clone 目标已存在: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        process_env = os.environ.copy()
        process_env["GIT_TERMINAL_PROMPT"] = "0"
        process = await asyncio.create_subprocess_exec(
            git_executable,
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--no-tags",
            "--",
            repo_url,
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=PLUGIN_GIT_CLONE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            if target.exists():
                remove_dir(str(target))
            raise RuntimeError("Git clone 超时。") from exc

        if process.returncode != 0:
            if target.exists():
                remove_dir(str(target))
            detail = stderr.decode("utf-8", errors="replace").strip()[-2000:]
            raise RuntimeError(f"Git clone 失败: {detail or '未知错误'}")

        git_metadata = target / ".git"
        if git_metadata.exists():
            remove_dir(str(git_metadata))

    async def inspect_repository(
        self,
        repo_url: str,
        proxy: str = "",
    ) -> dict[str, object]:
        """读取并校验仓库中的插件元数据。GitHub HTTP 仓库走加速前缀。"""
        try:
            normalized_url = normalize_repository_url(repo_url)
            repository = parse_repository_url(normalized_url)
        except ValueError as exc:
            raise ValueError("请输入有效的 Git 仓库地址。") from exc

        metadata: object | None = None
        metadata_entry = ""
        metadata_branch = repository.branch or ""
        if repository.transport == "git":
            temp_parent = Path(get_astrbot_temp_path()) / "repository-inspection"
            temp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temp_parent) as temp_dir:
                checkout_path = Path(temp_dir) / repository.name
                await self._clone_repository(normalized_url, checkout_path)
                inspection = self.inspect_plugin_directory(checkout_path)
                metadata = inspection["metadata"]
                metadata_entry = str(inspection.get("metadata_entry") or "")
        else:
            author, repo, branch = await self.resolve_github_source_branch(
                normalized_url,
                proxy=proxy,
            )
            metadata_branch = branch
            proxy = (proxy or "").strip().rstrip("/")
            async with self._create_httpx_client(
                timeout=PLUGIN_REPOSITORY_TIMEOUT_SECONDS
            ) as client:
                for filename in PLUGIN_METADATA_FILENAMES:
                    raw_url = (
                        f"https://raw.githubusercontent.com/"
                        f"{author}/{repo}/{branch}/{filename}"
                    )
                    request_url = self._拼接github加速(raw_url, proxy)
                    async with client.stream("GET", request_url) as response:
                        if response.status_code != 200:
                            continue

                        content_length = response.headers.get("Content-Length")
                        if content_length:
                            try:
                                too_large = (
                                    int(content_length) > PLUGIN_METADATA_MAX_BYTES
                                )
                            except ValueError:
                                too_large = False
                            if too_large:
                                raise ValueError(f"{filename} 超过 1MB。")

                        metadata_bytes = bytearray()
                        async for chunk in response.aiter_bytes():
                            metadata_bytes.extend(chunk)
                            if len(metadata_bytes) > PLUGIN_METADATA_MAX_BYTES:
                                raise ValueError(f"{filename} 超过 1MB。")

                    try:
                        metadata_text = bytes(metadata_bytes).decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise ValueError(f"{filename} 必须使用 UTF-8 编码。") from exc
                    try:
                        metadata = yaml.safe_load(metadata_text)
                    except yaml.YAMLError as exc:
                        raise ValueError(f"{filename} 格式错误。") from exc
                    try:
                        self.validate_plugin_metadata(metadata, filename)
                    except ValueError as exc:
                        raise ValueError(f"插件校验失败：{exc!s}") from exc
                    metadata_entry = filename
                    break

        if metadata is None:
            raise ValueError("未在仓库根目录找到 metadata.yaml 或 metadata.yml。")
        normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if "desc" not in normalized_metadata and "description" in normalized_metadata:
            normalized_metadata["desc"] = normalized_metadata["description"]
        return {
            "valid": True,
            "metadata_entry": metadata_entry,
            "metadata_branch": metadata_branch,
            "name": str(normalized_metadata.get("name") or ""),
            "display_name": normalized_metadata.get("display_name"),
            "desc": str(normalized_metadata.get("desc") or ""),
            "version": str(normalized_metadata.get("version") or ""),
            "author": normalized_metadata.get("author"),
            "repo": str(normalized_metadata.get("repo") or normalized_url),
        }

    async def install(self, repo_url: str, proxy="", download_url: str = "") -> str:
        normalized_url = normalize_repository_url(repo_url)
        repository = parse_repository_url(normalized_url)
        repo_name = self.format_name(repository.name)
        plugin_path = os.path.join(self.plugin_store_path, repo_name)
        if os.path.exists(plugin_path):
            raise Exception(f"安装失败：目录 {repo_name} 已存在。")
        if download_url:
            logger.info(f"正在下载插件压缩包 {repo_name}: {download_url}")
            await self._download_file(download_url, plugin_path + ".zip")
        elif repository.transport == "git":
            try:
                await self._clone_repository(normalized_url, plugin_path)
                self.inspect_plugin_directory(plugin_path)
            except Exception:
                if os.path.exists(plugin_path):
                    remove_dir(plugin_path)
                raise
            return plugin_path
        else:
            await self.download_from_repo_url(plugin_path, repo_url, proxy)
        self.unzip_file(plugin_path + ".zip", plugin_path)

        return plugin_path

    async def update(
        self,
        plugin: StarMetadata,
        proxy="",
        download_url: str = "",
        repo_url: str = "",
    ) -> str:
        repo_url = repo_url or plugin.repo

        if not repo_url and not download_url:
            raise Exception(
                f"插件 {plugin.name} 未指定仓库地址或下载地址。"
            )

        if not plugin.root_dir_name:
            raise Exception(
                f"插件 {plugin.name} 未指定根目录名。"
            )

        plugin_path = os.path.join(self.plugin_store_path, plugin.root_dir_name)

        logger.info(
            f"正在更新插件，路径: {plugin_path}，仓库: {repo_url}",
        )
        normalized_url = normalize_repository_url(repo_url) if repo_url else ""
        repository = parse_repository_url(normalized_url) if normalized_url else None
        if download_url:
            logger.info(
                f"正在下载插件更新压缩包 {plugin.name}: {download_url}"
            )
            await self._download_file(download_url, plugin_path + ".zip")
        elif repository and repository.transport == "git":
            ensure_dir(self.plugin_store_path)
            with tempfile.TemporaryDirectory(
                prefix=".plugin-update-",
                dir=self.plugin_store_path,
            ) as temp_dir:
                checkout_path = Path(temp_dir) / repository.name
                await self._clone_repository(normalized_url, checkout_path)
                self.inspect_plugin_directory(checkout_path)
                remove_dir(plugin_path)
                shutil.move(str(checkout_path), plugin_path)
            return plugin_path
        elif repo_url:
            await self.download_from_repo_url(plugin_path, repo_url, proxy=proxy)

        self.validate_plugin_archive(plugin_path + ".zip")
        try:
            remove_dir(plugin_path)
        except BaseException as e:
            logger.error(
                f"删除旧插件目录 {plugin_path} 失败: {e!s}；改为覆盖安装。",
            )

        self.unzip_file(plugin_path + ".zip", plugin_path)

        return plugin_path

    @classmethod
    def find_plugin_metadata_entry(cls, entries: list[str]) -> str | None:
        """Find AstrBot plugin metadata in archive entries.

        Args:
            entries: Zip archive member names.

        Returns:
            The original archive entry name for plugin metadata, or None.
        """
        update_dir = cls._resolve_archive_root_dir(entries)
        portable_update_dir = os.path.normpath(update_dir).replace("\\", "/")
        if portable_update_dir == ".":
            portable_update_dir = ""

        entries_by_portable_path = {}
        for entry in entries:
            portable_entry = os.path.normpath(entry).replace("\\", "/")
            if portable_entry in ("", "."):
                continue
            entries_by_portable_path[portable_entry] = entry

        metadata_candidates = (
            [
                f"{portable_update_dir}/{filename}"
                for filename in PLUGIN_METADATA_FILENAMES
            ]
            if portable_update_dir
            else list(PLUGIN_METADATA_FILENAMES)
        )
        for candidate in metadata_candidates:
            if candidate in entries_by_portable_path:
                return entries_by_portable_path[candidate]
        return None

    @staticmethod
    def validate_plugin_metadata(metadata: object, metadata_label: str) -> None:
        """Validate AstrBot plugin metadata content.

        Args:
            metadata: Parsed metadata YAML content.
            metadata_label: Metadata filename or archive entry for error messages.

        Raises:
            ValueError: If metadata is malformed or misses required fields.
        """
        if not isinstance(metadata, dict):
            raise ValueError(f"{metadata_label} 格式错误。")

        normalized_metadata = dict(metadata)
        if "desc" not in normalized_metadata and "description" in normalized_metadata:
            normalized_metadata["desc"] = normalized_metadata["description"]

        missing_fields = [
            field
            for field in PLUGIN_METADATA_REQUIRED_FIELDS
            if field not in normalized_metadata
        ]
        if missing_fields:
            raise ValueError(
                f"{metadata_label} 中缺少必需字段: {', '.join(missing_fields)}。"
            )

        invalid_fields = [
            field
            for field in PLUGIN_METADATA_REQUIRED_FIELDS
            if not isinstance(normalized_metadata[field], str)
            or not normalized_metadata[field].strip()
        ]
        if invalid_fields:
            raise ValueError(
                f"{metadata_label} 中字段 {', '.join(invalid_fields)} 必须是非空字符串。"
            )

    @classmethod
    def inspect_plugin_directory(cls, plugin_path: str | Path) -> dict[str, object]:
        """校验已检出仓库目录中的插件元数据。"""
        root = Path(plugin_path)
        for filename in PLUGIN_METADATA_FILENAMES:
            metadata_path = root / filename
            if not metadata_path.is_file():
                continue
            if metadata_path.stat().st_size > PLUGIN_METADATA_MAX_BYTES:
                raise ValueError(f"{filename} 超过 1MB。")
            try:
                metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            except UnicodeDecodeError as exc:
                raise ValueError(f"{filename} 必须使用 UTF-8 编码。") from exc
            except yaml.YAMLError as exc:
                raise ValueError(f"{filename} 格式错误。") from exc
            cls.validate_plugin_metadata(metadata, filename)
            return {"metadata_entry": filename, "metadata": metadata}
        raise ValueError("未在仓库根目录找到 metadata.yaml 或 metadata.yml。")

    @classmethod
    def inspect_plugin_archive(cls, zip_path: str) -> dict[str, object]:
        """Inspect plugin metadata in an AstrBot plugin archive.

        Args:
            zip_path: Path to the plugin archive.

        Returns:
            A dict containing the metadata entry and parsed metadata.

        Raises:
            ValueError: If the archive is not a valid AstrBot plugin.
        """
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                metadata_entry = cls.find_plugin_metadata_entry(z.namelist())
                if metadata_entry is None:
                    raise ValueError(
                        "压缩包不是合法的 AstrBot 插件：未找到 metadata.yaml 或 metadata.yml。"
                    )

                try:
                    metadata_text = z.read(metadata_entry).decode("utf-8")
                    metadata = yaml.safe_load(metadata_text)
                except UnicodeDecodeError as exc:
                    raise ValueError(f"{metadata_entry} 必须使用 UTF-8 编码。") from exc
                except yaml.YAMLError as exc:
                    raise ValueError(f"{metadata_entry} 格式错误。") from exc

                cls.validate_plugin_metadata(metadata, metadata_entry)
                return {
                    "metadata_entry": metadata_entry,
                    "metadata": metadata,
                }
        except zipfile.BadZipFile as exc:
            raise ValueError("插件压缩包格式错误。") from exc

    @classmethod
    def validate_plugin_archive(cls, zip_path: str) -> str:
        """Validate that an archive contains a valid AstrBot plugin.

        Args:
            zip_path: Path to the plugin archive.

        Returns:
            The archive entry name of the plugin metadata file.

        Raises:
            ValueError: If the archive is not a valid AstrBot plugin.
        """
        inspection = cls.inspect_plugin_archive(zip_path)
        return str(inspection["metadata_entry"])

    def unzip_file(self, zip_path: str, target_dir: str) -> None:
        self.validate_plugin_archive(zip_path)
        ensure_dir(target_dir)
        logger.info(f"Extracting archive: {zip_path}")
        with zipfile.ZipFile(zip_path, "r") as z:
            update_dir = self._resolve_archive_root_dir(z.namelist())
            z.extractall(target_dir)

        self._finalize_extracted_archive(zip_path, target_dir, update_dir)
