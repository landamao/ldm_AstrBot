import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import psutil

from astrbot.core import logger
from astrbot.core.config.default import VERSION
from astrbot.core.desktop_runtime import (
    DESKTOP_MANAGED_RESTART_MESSAGE,
    is_desktop_managed_backend,
)
from astrbot.core.utils.astrbot_path import get_astrbot_data_path, get_astrbot_path
from astrbot.core.utils.io import ensure_dir, on_error

from .zip_updator import ReleaseInfo, RepoZipUpdator


class AstrBotUpdator(RepoZipUpdator):
    """ldm 魔改版更新器：从 landamao/ldm_AstrBot 检查并应用更新。

    支持：
    1. 仅检查 / 列表 GitHub Releases（按 tag/semver 比较，不再回退 commit）
    2. 从同一源码包同步核心源码与 WebUI（dashboard/dist 或 data/dist）
    """

    # 应用更新时禁止整目录覆盖的顶层项（保护本地运行态）
    # 注意：data 整目录不覆盖；仅单独同步 data/dist（WebUI）
    受保护顶层目录 = {
        "data",
        ".venv",
        "venv",
        "node_modules",
        ".git",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        ".hermes",
        ".idea",
        ".vscode",
    }

    # 这些根文件/目录会从更新包直接覆盖（若包内存在）
    # 例如：main.py、README.md、requirements.txt、pyproject.toml、astrbot/、dashboard/ 等
    # 实际策略：包内除「受保护顶层目录」外全部覆盖，不限于下面示例
    根目录覆盖示例 = {
        "main.py",
        "README.md",
        "readme.md",
        "LICENSE",
        "NOTICE",
        "requirements.txt",
        "pyproject.toml",
        "uv.lock",
        "astrbot",
        "dashboard",
        "scripts",
        "changelogs",
        "docs",
    }

    def __init__(self, repo_mirror: str = "", verify: str | bool | None = None) -> None:
        super().__init__(repo_mirror, verify=verify)
        self.MAIN_PATH = get_astrbot_path()
        self.仓库所有者 = os.environ.get("LDM_ASTRBOT_REPO_OWNER", "landamao").strip()
        self.仓库名 = os.environ.get("LDM_ASTRBOT_REPO_NAME", "ldm_AstrBot").strip()
        self.ASTRBOT_RELEASE_API = (
            f"https://api.github.com/repos/{self.仓库所有者}/{self.仓库名}/releases"
        )
        # 不再走官方 soulter 托管包
        self.CORE_PACKAGE_BASE_URL = ""
        self.仓库网页地址 = f"https://github.com/{self.仓库所有者}/{self.仓库名}"
        # GitHub API 短缓存，降低限流概率
        self._releases_cache: list | None = None
        self._releases_cache_at: float = 0.0
        self._cache_ttl_seconds = int(
            os.environ.get("LDM_ASTRBOT_UPDATE_CACHE_TTL", "300")
        )

    def _更新元数据路径(self) -> Path:
        return Path(get_astrbot_data_path()) / "ldm_update_meta.json"

    def _读取更新元数据(self) -> dict:
        path = self._更新元数据路径()
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.warning(f"读取 ldm 更新元数据失败: {exc}")
            return {}

    def _写入更新元数据(self, **kwargs) -> None:
        path = self._更新元数据路径()
        ensure_dir(path.parent)
        meta = self._读取更新元数据()
        meta.update({k: v for k, v in kwargs.items() if v is not None})
        meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _拼接代理(self, url: str, proxy: str = "") -> str:
        if not proxy:
            return url
        return f"{proxy.removesuffix('/')}/{url}"

    def _构建源码包地址(self, *, tag: str) -> str:
        """仅按 tag 构建 GitHub archive 地址。"""
        if not tag:
            raise ValueError("构建源码包地址需要 tag")
        return f"{self.仓库网页地址}/archive/refs/tags/{tag}.zip"

    def _github_token(self) -> str:
        for key in (
            "LDM_GITHUB_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "ASTRBOT_GITHUB_TOKEN",
        ):
            value = (os.environ.get(key) or "").strip()
            if value:
                return value
        return ""

    def _github_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ldm-AstrBot-updator",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._github_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _atom_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/atom+xml, application/xml, text/xml, */*",
            "User-Agent": "ldm-AstrBot-updator",
        }

    @staticmethod
    def _html_to_text(html: str) -> str:
        import re
        from html import unescape

        text = unescape(html or "")
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
        text = re.sub(r"(?is)<br\s*/?>", "\n", text)
        text = re.sub(r"(?is)</p\s*>", "\n", text)
        text = re.sub(r"(?is)<li\s*>", "- ", text)
        text = re.sub(r"(?is)<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _extract_atom_tag(entry_xml: str, tag: str) -> str:
        import re

        match = re.search(
            rf"<(?:[\w.-]+:)?{tag}(?:\s[^>]*)?>(.*?)</(?:[\w.-]+:)?{tag}>",
            entry_xml,
            re.IGNORECASE | re.DOTALL,
        )
        return (match.group(1).strip() if match else "")

    def _parse_releases_atom(self, xml_text: str) -> list[dict]:
        import re
        from html import unescape

        entries = re.findall(
            r"<(?:[\w.-]+:)?entry(?:\s[^>]*)?>(.*?)</(?:[\w.-]+:)?entry>",
            xml_text or "",
            re.IGNORECASE | re.DOTALL,
        )
        releases: list[dict] = []
        for entry in entries:
            link = ""
            link_match = re.search(
                r'<(?:[\w.-]+:)?link[^>]*rel="alternate"[^>]*href="([^"]+)"',
                entry,
                re.IGNORECASE,
            )
            if not link_match:
                link_match = re.search(
                    r'<(?:[\w.-]+:)?link[^>]*href="([^"]+)"',
                    entry,
                    re.IGNORECASE,
                )
            if link_match:
                link = link_match.group(1).strip()

            tag_name = ""
            if "/releases/tag/" in link:
                tag_name = link.rsplit("/releases/tag/", 1)[-1].strip()
            if not tag_name:
                entry_id = unescape(self._extract_atom_tag(entry, "id"))
                if "/" in entry_id:
                    tag_name = entry_id.rsplit("/", 1)[-1].strip()
            if not tag_name:
                continue

            title = unescape(self._extract_atom_tag(entry, "title"))
            updated = self._extract_atom_tag(entry, "updated")
            content = self._extract_atom_tag(entry, "content")
            body = self._html_to_text(content) if content else title
            releases.append(
                {
                    "version": title or tag_name,
                    "published_at": updated,
                    "body": body or f"Release {tag_name}",
                    "tag_name": tag_name,
                    "zipball_url": self._构建源码包地址(tag=tag_name),
                }
            )

        releases.sort(
            key=lambda item: self._tag_sort_key(str(item.get("tag_name") or "")),
            reverse=True,
        )
        return releases

    async def _fetch_releases_via_atom(self) -> list:
        url = f"{self.仓库网页地址}/releases.atom"
        try:
            async with self._create_httpx_client(timeout=20.0) as client:
                response = await client.get(url, headers=self._atom_headers())
                response.raise_for_status()
                return self._parse_releases_atom(response.text)
        except Exception as exc:
            logger.warning(f"读取 releases.atom 失败: {exc}")
            return []

    def _git_remote_url(self) -> str:
        token = self._github_token()
        if token:
            return (
                f"https://x-access-token:{token}@github.com/"
                f"{self.仓库所有者}/{self.仓库名}.git"
            )
        return f"{self.仓库网页地址}.git"

    async def _git_ls_remote(self, *args: str, ref: str | None = None) -> str:
        """用 git ls-remote 获取 refs，不走 GitHub REST API，可规避 API 限流。

        正确参数顺序：git ls-remote [options] <repository> [<refs>...]
        """
        cmd = ["git", "ls-remote", *args, self._git_remote_url()]
        if ref:
            cmd.append(ref)
        return await asyncio.to_thread(
            lambda: subprocess.check_output(
                cmd,
                text=True,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
        )

    def _parse_ls_remote_tags(self, output: str) -> list[dict]:
        tags: list[dict] = []
        seen: set[str] = set()
        for line in output.splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            sha, ref = line.split("\t", 1)
            if not ref.startswith("refs/tags/"):
                continue
            # annotated tag 的剥皮引用：用^{} 对应 commit sha 覆盖同名 tag
            if ref.endswith("^{}"):
                tag = ref[len("refs/tags/") : -3]
                for item in tags:
                    if item["tag_name"] == tag:
                        item["commit_sha"] = sha
                        break
                else:
                    if tag not in seen:
                        seen.add(tag)
                        tags.append(self._build_tag_release_item(tag, sha))
                continue
            tag = ref[len("refs/tags/") :]
            if tag in seen:
                continue
            seen.add(tag)
            tags.append(self._build_tag_release_item(tag, sha))

        # 新版本在前：支持 v4.26.5 / v4.26.5-v2 / v4.26.5-v3
        tags.sort(key=lambda item: self._tag_sort_key(str(item.get("tag_name") or "")), reverse=True)
        return tags

    def _build_tag_release_item(self, tag: str, sha: str) -> dict:
        return {
            "version": tag,
            "published_at": "",
            "body": f"来自 git ls-remote 的标签 {tag}",
            "tag_name": tag,
            "zipball_url": self._构建源码包地址(tag=tag),
            "commit_sha": sha,
        }

    @staticmethod
    def _tag_sort_key(tag_name: str) -> tuple:
        """为 ldm 标签生成排序键，越大越新。

        规则：
        - v4.26.5      -> (4, 26, 5, 0, ...)
        - v4.26.5-v2   -> (4, 26, 5, 2, ...)
        - v4.26.5-v3   -> (4, 26, 5, 3, ...)
        因此 reverse 后顺序为 v3 > v2 > 正式版。
        """
        import re

        name = (tag_name or "").strip()
        match = re.match(
            r"^v?(?P<base>\d+(?:\.\d+)*)(?:-v?(?P<rev>\d+))?(?P<rest>.*)$",
            name,
            re.IGNORECASE,
        )
        if not match:
            return (0, 0, 0, 0, name)

        base_parts = [int(x) for x in match.group("base").split(".")]
        while len(base_parts) < 3:
            base_parts.append(0)
        base_parts = base_parts[:3]
        rev = int(match.group("rev") or 0)
        rest = match.group("rest") or ""
        return (base_parts[0], base_parts[1], base_parts[2], rev, rest)

    async def _fetch_releases_via_git(self) -> list:
        try:
            output = await self._git_ls_remote("--tags")
        except Exception as exc:
            logger.warning(f"git ls-remote 获取 tags 失败: {exc}")
            return []
        return self._parse_ls_remote_tags(output)

    async def fetch_release_info(self, url: str, latest: bool = True) -> list:
        """优先 GitHub API；失败则 releases.atom；再失败则 git ls-remote。"""
        now = time.time()
        if (
            self._releases_cache is not None
            and now - self._releases_cache_at < self._cache_ttl_seconds
        ):
            return list(self._releases_cache)

        # 1) REST API
        try:
            async with self._create_httpx_client() as client:
                response = await client.get(url, headers=self._github_headers())
                response.raise_for_status()
                result = response.json()
            if not result:
                ret: list = []
            else:
                ret = []
                for release in result:
                    ret.append(
                        {
                            "version": release.get("name") or release.get("tag_name"),
                            "published_at": release.get("published_at") or "",
                            "body": release.get("body") or "",
                            "tag_name": release.get("tag_name") or "",
                            "zipball_url": release.get("zipball_url")
                            or self._构建源码包地址(tag=release.get("tag_name")),
                        }
                    )
            ret.sort(
                key=lambda item: self._tag_sort_key(str(item.get("tag_name") or "")),
                reverse=True,
            )
            self._releases_cache = ret
            self._releases_cache_at = now
            return list(ret)
        except Exception as api_exc:
            logger.warning(f"GitHub Releases API 失败，尝试 Atom/git 回退: {api_exc}")

        # 2) Atom feed（不占 REST 限额，且带发布时间）
        ret = await self._fetch_releases_via_atom()
        if ret:
            self._releases_cache = ret
            self._releases_cache_at = now
            return list(ret)

        # 3) git ls-remote（只有 tag，没有时间）
        ret = await self._fetch_releases_via_git()
        self._releases_cache = ret
        self._releases_cache_at = now
        return list(ret)

    def _build_core_package_url(self, version: str | None) -> str | None:
        """魔改版不使用官方托管包。"""
        return None

    def terminate_child_processes(self) -> None:
        """终止当前进程的所有子进程。"""
        try:
            parent = psutil.Process(os.getpid())
            children = parent.children(recursive=True)
            logger.info(f"正在终止 {len(children)} 个子进程。")
            for child in children:
                logger.info(f"正在终止子进程 {child.pid}")
                child.terminate()
                try:
                    child.wait(timeout=3)
                except psutil.NoSuchProcess:
                    continue
                except psutil.TimeoutExpired:
                    logger.info(f"子进程 {child.pid} 没有被正常终止, 正在强行杀死。")
                    child.kill()
        except psutil.NoSuchProcess:
            pass

    @staticmethod
    def _is_option_arg(arg: str) -> bool:
        return arg.startswith("-")

    @classmethod
    def _collect_flag_values(cls, argv: list[str], flag: str) -> str | None:
        try:
            idx = argv.index(flag)
        except ValueError:
            return None

        if idx + 1 >= len(argv):
            return None

        value_parts: list[str] = []
        for arg in argv[idx + 1 :]:
            if cls._is_option_arg(arg):
                break
            if arg:
                value_parts.append(arg)

        if not value_parts:
            return None

        return " ".join(value_parts).strip() or None

    @classmethod
    def _resolve_webui_dir_arg(cls, argv: list[str]) -> str | None:
        return cls._collect_flag_values(argv, "--webui-dir")

    def _build_frozen_reboot_args(self) -> list[str]:
        argv = list(sys.argv[1:])
        webui_dir = self._resolve_webui_dir_arg(argv)
        if not webui_dir:
            webui_dir = os.environ.get("ASTRBOT_WEBUI_DIR")

        if webui_dir:
            return ["--webui-dir", webui_dir]
        return []

    @staticmethod
    def _reset_pyinstaller_environment() -> None:
        if not getattr(sys, "frozen", False):
            return
        os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        for key in list(os.environ.keys()):
            if key.startswith("_PYI_"):
                os.environ.pop(key, None)

    def _build_reboot_argv(self, executable: str) -> list[str]:
        if os.environ.get("ASTRBOT_CLI") == "1":
            args = sys.argv[1:]
            return [executable, "-m", "astrbot.cli.__main__", *args]
        if getattr(sys, "frozen", False):
            args = self._build_frozen_reboot_args()
            return [executable, *args]
        return [executable, *sys.argv]

    @staticmethod
    def _exec_reboot(executable: str, argv: list[str]) -> None:
        if os.name == "nt" and getattr(sys, "frozen", False):
            quoted_executable = f'"{executable}"' if " " in executable else executable
            quoted_args = [f'"{arg}"' if " " in arg else arg for arg in argv[1:]]
            os.execl(executable, quoted_executable, *quoted_args)
            return
        elif os.name == "nt":
            subprocess.Popen(
                [executable] + argv[1:], creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            os._exit(0)
        os.execv(executable, argv)

    def _reboot(self, delay: int = 3) -> None:
        """重启当前程序。"""
        if is_desktop_managed_backend():
            logger.error(DESKTOP_MANAGED_RESTART_MESSAGE)
            raise RuntimeError(DESKTOP_MANAGED_RESTART_MESSAGE)

        time.sleep(delay)
        self.terminate_child_processes()
        executable = sys.executable

        try:
            self._reset_pyinstaller_environment()
            reboot_argv = self._build_reboot_argv(executable)
            self._exec_reboot(executable, reboot_argv)
        except Exception as e:
            logger.error(f"重启失败（{executable}, {e}），请尝试手动重启。")
            raise e

    async def check_update(
        self,
        url: str | None,
        current_version: str | None,
        consider_prerelease: bool = True,
    ) -> ReleaseInfo | None:
        """检查 landamao/ldm_AstrBot 是否有新 Release（仅 tag，不检查 commit）。"""
        import re

        current = VERSION if current_version is None else current_version
        try:
            releases = await self.fetch_release_info(self.ASTRBOT_RELEASE_API)
        except Exception as exc:
            logger.warning(f"获取 Release 列表失败: {exc}")
            raise

        if not releases:
            logger.info("远端没有可用的 GitHub Release，判定为无更新。")
            return None

        sel = None
        if consider_prerelease:
            sel = releases[0]
        else:
            for data in releases:
                tag_name = str(data.get("tag_name") or "")
                if re.search(
                    r"[\-_.]?(alpha|beta|rc|dev)[\-_.]?\d*$",
                    tag_name,
                    re.IGNORECASE,
                ):
                    continue
                sel = data
                break

        if not sel or not sel.get("tag_name"):
            return None

        tag_name = str(sel["tag_name"])
        # 仅对 vX.Y.Z / 数字开头 tag 做 semver 比较
        if not (tag_name.startswith("v") or tag_name[:1].isdigit()):
            logger.info(f"最新 Release tag 非版本号风格，跳过比较: {tag_name}")
            return None

        if self.compare_version(current, tag_name) < 0:
            return ReleaseInfo(
                version=tag_name,
                published_at=sel.get("published_at") or "",
                body=f"{tag_name}\n\n{sel.get('body') or ''}",
            )
        return None

    async def get_releases(self) -> list:
        """返回可安装的 GitHub Release 列表（仅 tag，新版本在前）。"""
        releases: list = []
        try:
            releases = await self.fetch_release_info(self.ASTRBOT_RELEASE_API)
        except Exception as exc:
            logger.warning(f"获取 GitHub Releases 列表失败: {exc}")
            raise

        # 再保险排一次：API 返回顺序不一定是新→旧，且要处理 -v2/-v3
        releases = sorted(
            releases,
            key=lambda item: self._tag_sort_key(str(item.get("tag_name") or "")),
            reverse=True,
        )
        return releases

    async def update(
        self,
        reboot=False,
        latest=True,
        version=None,
        proxy="",
        progress_callback=None,
    ) -> None:
        zip_path = await self.download_update_package(
            latest=latest,
            version=version,
            proxy=proxy,
            progress_callback=progress_callback,
        )
        self.apply_update_package(zip_path)

        if reboot:
            self._reboot()

    async def download_update_package(
        self,
        latest=True,
        version=None,
        proxy="",
        path: str | Path = "temp.zip",
        progress_callback=None,
    ) -> Path:
        """下载 ldm_AstrBot 源码更新包（不解压）。"""
        if os.environ.get("ASTRBOT_CLI") or os.environ.get("ASTRBOT_LAUNCHER"):
            raise Exception(
                "当前以 CLI/Launcher 方式运行，请改用源码目录方式更新 ldm_AstrBot。"
            )

        file_url = None
        target_version = None
        version_text = "" if version is None else str(version).strip()

        if latest or version_text in {"", "latest"}:
            try:
                update_data = await self.fetch_release_info(self.ASTRBOT_RELEASE_API)
            except Exception as exc:
                raise Exception(f"获取 GitHub Release 失败，无法下载最新版: {exc}") from exc

            if not update_data:
                raise Exception(
                    f"仓库 {self.仓库所有者}/{self.仓库名} 没有可用的 GitHub Release，"
                    "无法按最新版更新（已禁用 commit 回退）。"
                )

            # 只走 Release：即便当前版本不落后，也安装列表中最新的 tag
            latest_version = update_data[0]["tag_name"]
            target_version = latest_version
            file_url = update_data[0].get("zipball_url") or self._构建源码包地址(
                tag=latest_version
            )
            if self.compare_version(VERSION, latest_version) >= 0:
                logger.info(
                    f"当前版本 {VERSION} 不小于最新 Release {latest_version}，"
                    "仍按该 Release 重新安装（不再回退 commit）。"
                )
        elif version_text.startswith("v") or version_text.startswith("V"):
            update_data = await self.fetch_release_info(self.ASTRBOT_RELEASE_API)
            for data in update_data:
                if data["tag_name"] == version_text:
                    target_version = data["tag_name"]
                    file_url = data.get("zipball_url") or self._构建源码包地址(
                        tag=version_text
                    )
                    break
            if not file_url:
                # 没有对应 release 时，直接按 tag 归档下载
                target_version = version_text
                file_url = self._构建源码包地址(tag=version_text)
        else:
            raise Exception(
                f"仅支持按 GitHub Release / tag 更新，不支持 commit 或分支: {version_text!r}"
            )

        if not file_url:
            raise Exception(f"无法解析更新地址: version={version_text!r}")

        logger.info(
            f"准备从 {self.仓库所有者}/{self.仓库名} 下载更新包: {target_version}"
        )
        raw_url = file_url
        file_url = self._拼接代理(file_url, proxy or "")
        if proxy:
            logger.info(
                f"GitHub 加速: 使用中 动作=下载更新包 目标={target_version} "
                f"代理={str(proxy).rstrip('/')} 地址={file_url}"
            )
        else:
            logger.info(
                f"GitHub 加速: 未使用 动作=下载更新包 目标={target_version} "
                f"地址={raw_url}"
            )

        zip_path = Path(path)
        ensure_dir(zip_path.parent)
        await self._download_file(
            file_url,
            str(zip_path),
            progress_callback=progress_callback,
        )
        if not zipfile.is_zipfile(zip_path):
            raise RuntimeError("下载的更新包不是有效 ZIP 文件")

        # 把目标版本信息暂存在旁路 meta，apply 时写入正式元数据
        side_meta = zip_path.with_suffix(zip_path.suffix + ".meta.json")
        side_meta.write_text(
            json.dumps(
                {
                    "version": target_version,
                    "sha": None,
                    "source": f"{self.仓库所有者}/{self.仓库名}",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return zip_path

    def _定位包内webui_dist(self, 包根目录: Path) -> Path | None:
        """在源码包中寻找可部署的 WebUI dist。"""
        候选 = [
            包根目录 / "dashboard" / "dist",
            包根目录 / "data" / "dist",
            包根目录 / "dist",
        ]
        for path in 候选:
            if (path / "index.html").is_file():
                return path
        return None

    def _安全覆盖目录(self, 源目录: Path, 目标目录: Path) -> None:
        ensure_dir(目标目录.parent)
        if 目标目录.exists():
            shutil.rmtree(目标目录, onerror=on_error)
        shutil.copytree(源目录, 目标目录)

    def _应用源码包内容(self, 包根目录: Path) -> Path | None:
        """把更新包内容同步到本地。

        规则：
        1. 覆盖项目根下所有文件与目录（含 main.py、README.md、requirements.txt 等）
        2. 跳过受保护顶层目录（data/.venv/node_modules/.git 等）
        3. data 不整目录覆盖；WebUI 由后续 _应用webui 单独覆盖 data/dist
        """
        目标根 = Path(self.MAIN_PATH)
        ensure_dir(目标根)

        已覆盖: list[str] = []
        已跳过: list[str] = []

        for 名称 in sorted(os.listdir(包根目录)):
            if 名称 in self.受保护顶层目录:
                已跳过.append(名称)
                continue
            if 名称.endswith(".meta.json"):
                continue
            if 名称 in {".", ".."}:
                continue

            源路径 = 包根目录 / 名称
            目标路径 = 目标根 / 名称

            if 源路径.is_dir():
                # 目录：先删后整棵拷贝，确保与更新包一致
                if 目标路径.exists():
                    if 目标路径.is_dir():
                        shutil.rmtree(目标路径, onerror=on_error)
                    else:
                        目标路径.unlink()
                shutil.copytree(源路径, 目标路径)
            else:
                # 根文件：直接覆盖 main.py / README / pyproject.toml 等
                ensure_dir(目标路径.parent)
                if 目标路径.exists():
                    if 目标路径.is_dir():
                        shutil.rmtree(目标路径, onerror=on_error)
                    else:
                        目标路径.unlink()
                shutil.copy2(源路径, 目标路径)

            已覆盖.append(名称)

        logger.info(
            "源码包覆盖完成: "
            f"已覆盖 {len(已覆盖)} 项（含根文件/目录）; "
            f"已跳过保护项={已跳过 or ['无']}"
        )
        if 已覆盖:
            预览 = ", ".join(已覆盖[:20])
            if len(已覆盖) > 20:
                预览 += f" ...(+{len(已覆盖) - 20})"
            logger.info(f"本次覆盖项: {预览}")

        # 返回包内 WebUI dist，供后续只覆盖 data/dist
        return self._定位包内webui_dist(包根目录)

    def _应用webui(self, webui_dist: Path | None) -> bool:
        """只覆盖 data/dist，不动 data 下其它内容（配置/数据库/插件数据等）。"""
        if webui_dist is None:
            logger.warning(
                "更新包中未找到 dashboard/dist 或 data/dist（缺 index.html），"
                "跳过 WebUI 覆盖。"
            )
            return False

        目标 = Path(get_astrbot_data_path()) / "dist"
        logger.info(f"正在覆盖 WebUI（仅 data/dist）: {webui_dist} -> {目标}")
        # 尽量保留 assets/version：若新包没有 version 而旧包有，写回旧 version 仅作兜底
        旧version文件 = 目标 / "assets" / "version"
        旧version内容 = None
        if 旧version文件.is_file():
            try:
                旧version内容 = 旧version文件.read_text(encoding="utf-8")
            except Exception:
                旧version内容 = None

        self._安全覆盖目录(webui_dist, 目标)

        新version文件 = 目标 / "assets" / "version"
        if not 新version文件.is_file() and 旧version内容 is not None:
            ensure_dir(新version文件.parent)
            新version文件.write_text(旧version内容, encoding="utf-8")
            logger.info("新 WebUI 缺少 assets/version，已回写旧 version 文件。")
        return True

    def apply_update_package(self, zip_path: str | Path) -> None:
        """应用已下载的 ldm_AstrBot 更新包（核心 + 可选 WebUI）。"""
        zip_path = Path(zip_path)
        if not zipfile.is_zipfile(zip_path):
            raise RuntimeError(f"无效更新包: {zip_path}")

        logger.info("开始应用 ldm_AstrBot 更新包...")
        side_meta_path = zip_path.with_suffix(zip_path.suffix + ".meta.json")
        side_meta = {}
        if side_meta_path.is_file():
            try:
                side_meta = json.loads(side_meta_path.read_text(encoding="utf-8"))
            except Exception:
                side_meta = {}

        with tempfile.TemporaryDirectory(prefix="ldm-astrbot-update-") as tmp:
            tmp_root = Path(tmp)
            with zipfile.ZipFile(zip_path, "r") as archive:
                corrupt = archive.testzip()
                if corrupt:
                    raise RuntimeError(f"更新包校验失败: {corrupt}")
                archive_root_name = self._resolve_archive_root_dir(archive.namelist())
                archive.extractall(tmp_root)

            if archive_root_name:
                包根目录 = tmp_root / archive_root_name
            else:
                包根目录 = tmp_root

            if not 包根目录.is_dir():
                # 兜底：GitHub 归档通常只有一个顶层目录
                children = [p for p in tmp_root.iterdir() if p.is_dir()]
                if not children:
                    raise RuntimeError("更新包内容为空，无法应用。")
                包根目录 = children[0]

            webui_dist = self._应用源码包内容(包根目录)
            webui_applied = self._应用webui(webui_dist)

        try:
            zip_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(f"删除临时更新包失败，可手动删除: {zip_path}")
        try:
            side_meta_path.unlink(missing_ok=True)
        except Exception:
            pass

        self._写入更新元数据(
            version=side_meta.get("version"),
            sha=side_meta.get("sha"),
            source=side_meta.get("source")
            or f"{self.仓库所有者}/{self.仓库名}",
            webui_applied=webui_applied,
            local_version=VERSION,
        )
        logger.info("ldm_AstrBot 更新包应用完成。")

    async def apply_webui_only_from_package(
        self,
        latest=True,
        version=None,
        proxy="",
        progress_callback=None,
    ) -> bool:
        """仅从 ldm_AstrBot 更新包同步 WebUI 到 data/dist。"""
        update_temp_parent = Path(get_astrbot_data_path()) / "temp" / "updates"
        ensure_dir(update_temp_parent)
        zip_path = update_temp_parent / f"webui-only-{int(time.time())}.zip"
        try:
            zip_path = await self.download_update_package(
                latest=latest,
                version=version,
                proxy=proxy or "",
                path=zip_path,
                progress_callback=progress_callback,
            )
            with tempfile.TemporaryDirectory(prefix="ldm-webui-only-") as tmp:
                tmp_root = Path(tmp)
                with zipfile.ZipFile(zip_path, "r") as archive:
                    archive.extractall(tmp_root)
                children = [p for p in tmp_root.iterdir() if p.is_dir()]
                包根目录 = children[0] if children else tmp_root
                webui_dist = self._定位包内webui_dist(包根目录)
                return self._应用webui(webui_dist)
        finally:
            try:
                Path(zip_path).unlink(missing_ok=True)
            except Exception:
                pass
            side = Path(str(zip_path) + ".meta.json")
            try:
                side.unlink(missing_ok=True)
            except Exception:
                pass

    async def download_from_repo_url(
        self, target_path: str, repo_url: str, proxy=""
    ) -> None:
        """按仓库 URL 下载源码 zip（用于通用仓库安装场景）。"""
        author, repo, branch = await self.resolve_github_source_branch(repo_url)
        logger.info(f"正在下载更新 {repo} ...")
        logger.info(f"正在从分支 {branch} 下载 {author}/{repo}")
        release_url = (
            f"https://github.com/{author}/{repo}/archive/refs/heads/{branch}.zip"
        )
        release_url = self._拼接代理(release_url, proxy or "")
        await self._download_file(release_url, target_path + ".zip")

    def unzip_file(self, zip_path: str, target_dir: str) -> None:
        """解压 zip，并把压缩包内第一个根目录内容移动到 target_dir。"""
        ensure_dir(target_dir)
        with zipfile.ZipFile(zip_path, "r") as z:
            update_dir = self._resolve_archive_root_dir(z.namelist())
            z.extractall(target_dir)
        logger.debug(f"解压文件完成: {zip_path}")
        self._finalize_extracted_archive(zip_path, target_dir, update_dir)
