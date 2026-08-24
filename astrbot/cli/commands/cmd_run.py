import asyncio
import os
import sys
import traceback
from pathlib import Path

import click
from filelock import FileLock, Timeout

from ..utils import check_astrbot_root, check_dashboard, get_astrbot_root

DASHBOARD_RESET_PASSWORD_ENV = "LDMBOT_RESET_DASHBOARD_PASSWORD"


def _prompt_and_set_reset_password() -> None:
    """交互式输入新密码并直接写入配置文件，完成后退出提示重启。

    交互式：提示输入新密码，留空使用默认 "ldm"；
    非交互式：直接使用 "ldm"。
    两种情况都写完配置后退出，不继续启动。
    """
    import json

    password = "ldm"
    if sys.stdin and sys.stdin.isatty():
        try:
            raw = input('请输入新的管理面板密码（留空使用默认 "ldm"）: ').strip()
            if raw:
                password = raw
        except (EOFError, KeyboardInterrupt):
            pass

    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    from astrbot.core.utils.auth_password import (
        hash_dashboard_password,
        hash_md5_dashboard_password,
    )

    data_path = get_astrbot_data_path()
    config_path = str(Path(data_path) / "cmd_config.json")

    if not Path(config_path).exists():
        click.echo("配置文件不存在，请先正常启动一次生成配置。")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8-sig") as f:
        conf = json.load(f)

    if "dashboard" not in conf or not isinstance(conf["dashboard"], dict):
        conf["dashboard"] = {}

    conf["dashboard"]["pbkdf2_password"] = hash_dashboard_password(password)
    conf["dashboard"]["password"] = hash_md5_dashboard_password(password)
    conf["dashboard"]["password_storage_upgraded"] = True
    conf["dashboard"]["password_change_required"] = True

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=4, ensure_ascii=False)

    click.echo(f"管理面板密码已重置为: {password}")
    click.echo("请重新启动 ldm 生效。")
    sys.exit(0)


async def run_astrbot(astrbot_root: Path) -> None:
    """Run AstrBot"""
    from astrbot.core import LogBroker, LogManager, db_helper, logger
    from astrbot.core.initial_loader import InitialLoader

    await check_dashboard(astrbot_root / "data")

    log_broker = LogBroker()
    LogManager.set_queue_handler(logger, log_broker)
    db = db_helper

    core_lifecycle = InitialLoader(db, log_broker)

    await core_lifecycle.start()


@click.option("--reload", "-r", is_flag=True, help="Auto-reload plugins")
@click.option("--port", "-p", help="ldm Dashboard port", required=False, type=str)
@click.option(
    "--reset-password",
    "--重置密码",
    "reset_password",
    is_flag=True,
    help="重置管理面板密码（交互式输入，留空用默认 ldm），改完退出需重启",
)
@click.command()
def run(reload: bool, port: str | None, reset_password: bool) -> None:
    """Run AstrBot"""
    try:
        os.environ["LDMBOT_CLI"] = "1"
        astrbot_root = get_astrbot_root()

        if not check_astrbot_root(astrbot_root):
            raise click.ClickException(
                f"{astrbot_root} is not a valid ldm root directory. Use 'astrbot init' to initialize",
            )

        os.environ["LDMBOT_ROOT"] = str(astrbot_root)
        sys.path.insert(0, str(astrbot_root))

        if port:
            os.environ["LDMBOT_DASHBOARD_PORT"] = port

        if reload:
            click.echo("Plugin auto-reload enabled")
            os.environ["LDMBOT_RELOAD"] = "1"

        if reset_password:
            _prompt_and_set_reset_password()

        lock_file = astrbot_root / "astrbot.lock"
        lock = FileLock(lock_file, timeout=5)
        with lock.acquire():
            asyncio.run(run_astrbot(astrbot_root))
    except KeyboardInterrupt:
        click.echo("ldm has been shut down.")
    except Timeout:
        raise click.ClickException(
            "Cannot acquire lock file. Please check if another instance is running"
        )
    except Exception as e:
        raise click.ClickException(f"Runtime error: {e}\n{traceback.format_exc()}")
