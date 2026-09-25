import asyncio
from pathlib import Path

import click
from filelock import FileLock, Timeout


async def check_dashboard(astrbot_root: Path) -> None:
    """Check whether dashboard assets are available.

    Args:
        astrbot_root: AstrBot data directory path.
    """
    from ..utils import check_dashboard as _check_dashboard

    await _check_dashboard(astrbot_root)


async def initialize_astrbot(astrbot_root: Path) -> None:
    """Execute AstrBot initialization logic"""
    from ..utils import resolve_cli_data_path

    dot_astrbot = astrbot_root / ".astrbot"

    if not dot_astrbot.exists():
        if click.confirm(
            f"Install ldm to this directory? {astrbot_root}",
            default=True,
            abort=True,
        ):
            dot_astrbot.touch()
            click.echo(f"Created {dot_astrbot}")

    data_path = resolve_cli_data_path(astrbot_root)
    paths = {
        "data": data_path,
        "config": data_path / "config",
        "plugins": data_path / "plugins",
        "temp": data_path / "temp",
    }

    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        click.echo(f"{'Created' if not path.exists() else 'Directory exists'}: {path}")

    await check_dashboard(astrbot_root)


@click.command()
def init() -> None:
    """Initialize AstrBot"""
    from ..utils import get_astrbot_root

    click.echo("Initializing ldm...")

    astrbot_root = get_astrbot_root()
    lock_file = astrbot_root / "astrbot.lock"
    lock = FileLock(lock_file, timeout=5)

    try:
        with lock.acquire():
            asyncio.run(initialize_astrbot(astrbot_root))
            click.echo("Done! You can now run 'astrbot run' to start ldm")
    except Timeout:
        raise click.ClickException(
            "Cannot acquire lock file. Please check if another instance is running"
        )

    except Exception as e:
        raise click.ClickException(f"Initialization failed: {e!s}")
