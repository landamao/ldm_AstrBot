from pathlib import Path

import click

# Static assets bundled inside the installed wheel (built by hatch_build.py).
_BUNDLED_DIST = Path(__file__).parent.parent.parent / "dashboard" / "dist"


def check_astrbot_root(path: str | Path) -> bool:
    """Check if the path is an AstrBot root directory"""
    if not isinstance(path, Path):
        path = Path(path)
    if not path.exists() or not path.is_dir():
        return False
    if not (path / ".astrbot").exists():
        return False
    return True


def get_astrbot_root() -> Path:
    """Get the AstrBot root directory path"""
    return Path.cwd()


async def check_dashboard(astrbot_root: Path) -> None:
    """检查管理面板是否可用；不自动下载或覆盖本地 WebUI。"""
    from astrbot.core.config.default import VERSION
    from astrbot.core.utils.io import get_dashboard_version

    from .version_comparator import VersionComparator

    # If the wheel ships bundled dashboard assets, no network download is needed.
    if _BUNDLED_DIST.exists():
        click.echo("Dashboard is bundled with the package – skipping download.")
        return

    try:
        dashboard_version = await get_dashboard_version()
        match dashboard_version:
            case None:
                click.echo(
                    "Dashboard is not installed. 已禁用 WebUI 自动下载；请手动构建 dashboard 并复制到 data/dist。"
                )
                return

            case str():
                if VersionComparator.compare_version(VERSION, dashboard_version) <= 0:
                    click.echo("Dashboard is already up to date")
                    return
                click.echo(
                    f"Dashboard version: {dashboard_version}. 已禁用 WebUI 自动下载/覆盖，请手动更新 data/dist。"
                )
                return
    except FileNotFoundError:
        click.echo(
            "Dashboard directory is missing. 已禁用 WebUI 自动下载；请手动构建 dashboard 并复制到 data/dist。"
        )
        return
