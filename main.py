import os, sys, time, random, threading


# ========== 原始艺术字与颜色定义 ==========
ldmnb = r"""
     __          ______       __    __     __    __     _______ 
    |  |        |       \    |  \  /  |   |  \  |  |   |   _   \
    |  |        |  .--.  |   |   \/   |   |   \ |  |   |  |_)  |
    |  |        |  |  |  |   |        |   |    \|  |   |   _  < 
    |  `----.   |  '--'  |   |  |\/|  |   |  |\    |   |  |_)  |
    |_______|   |_______/    |__|  |__|   |__| \___|   |_______/
"""
ldmbot = r"""
     __          ______      __    __    _______      ____      _________
    |  |        |       \   |  \  /  |  |   _   \    /    \    |___   ___|
    |  |        |  .--.  |  |   \/   |  |  |_)  |   /  __  \       |  |
    |  |        |  |  |  |  |        |  |   _  <   |  |  |  |      |  |
    |  `----.   |  '--'  |  |  |\/|  |  |  |_)  |   \  `'  /       |  |
    |_______|   |_______/   |__|  |__|  |_______/    \____/        |__|
"""

# 基础颜色
red = "\033[31m"
green = "\033[32m"
yellow = "\033[33m"
blue = "\033[34m"
purple = "\033[35m"
cyan = "\033[36m"
reset = "\033[0m"

# 亮色（高亮）版本
bright_red = "\033[1;31m"
bright_green = "\033[1;32m"
bright_yellow = "\033[1;33m"
bright_blue = "\033[1;34m"
bright_purple = "\033[1;35m"
bright_cyan = "\033[1;36m"


def supports_color() -> bool:
    """检测终端是否支持颜色。"""
    if not sys.stdout.isatty():
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    # 这里可以加入更精确的 Windows 判断，简单起见认为现代 Windows 终端都支持
    return True


if not supports_color():
    # 不支持颜色时，将所有颜色代码清空，保留动画
    red = green = yellow = blue = purple = cyan = ""
    bright_red = bright_green = bright_yellow = bright_blue = bright_purple = bright_cyan = ""
    reset = ""
    dark_colors = [red, green, yellow, blue, purple, cyan]
    bright_colors = [
        bright_red,
        bright_green,
        bright_yellow,
        bright_blue,
        bright_purple,
        bright_cyan,
    ]
else:
    dark_colors = [red, green, yellow, blue, purple, cyan]
    bright_colors = [
        bright_red,
        bright_green,
        bright_yellow,
        bright_blue,
        bright_purple,
        bright_cyan,
    ]


def print_art_column_by_column(art, delay=0.03, start_row=1, extra_arts=None):
    """
    从左到右逐列彩色显示艺术字。
    如果提供了 extra_arts（列表，元素为 (start_row, rows, cols, padded_lines)），
    则每一帧都会先以随机颜色重绘这些额外的艺术字，从而让它们保持动态颜色闪烁。
    返回 (rows, cols, padded_lines)。
    """
    lines = art.split("\n")
    if lines and lines[0] == "":
        lines = lines[1:]
    if lines and lines[-1] == "":
        lines = lines[:-1]

    rows = len(lines)
    cols = max(len(line) for line in lines)
    padded = [line.ljust(cols) for line in lines]

    sys.stdout.write("\033[?25l")  # 隐藏光标
    sys.stdout.flush()

    try:
        for c in range(cols):
            # ---- 如果存在已完成的艺术字，先用随机颜色完整重绘它们 ----
            if extra_arts:
                for (sr, erows, ecols, epadded) in extra_arts:
                    for r in range(erows):
                        sys.stdout.write(f"\033[{sr + r};1H")
                        for cc in range(ecols):
                            ch = epadded[r][cc]
                            if ch == " ":
                                sys.stdout.write(" ")
                            else:
                                color = random.choice(bright_colors + dark_colors)
                                sys.stdout.write(f"{color}{ch}")
                        sys.stdout.write(reset)
                sys.stdout.flush()

            # ---- 绘制当前艺术字的第 0..c 列 ----
            sys.stdout.write(f"\033[{start_row};1H")
            for r in range(rows):
                sys.stdout.write(f"\033[{start_row + r};1H")
                for cc in range(c + 1):
                    ch = padded[r][cc]
                    if ch == " ":
                        sys.stdout.write(" ")
                    else:
                        color = random.choice(bright_colors + dark_colors)
                        sys.stdout.write(f"{color}{ch}")
            sys.stdout.write(reset)
            sys.stdout.flush()
            time.sleep(delay)

        # 光标移到整体下方
        sys.stdout.write(f"\033[{start_row + rows};1H\n")
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    return rows, cols, padded


def flash_unified_bright_dark(art_info_list, total_duration=3, interval=0.1):
    """
    整体闪烁：所有艺术字统一颜色，一亮一暗交替
    art_info_list: [(start_row, rows, cols, padded_lines), ...]
    """
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()
    start_time = time.time()

    use_bright = True

    try:
        while time.time() - start_time < total_duration:
            if use_bright:
                color = random.choice(bright_colors)
            else:
                color = random.choice(dark_colors)

            for start_row, rows, cols, padded in art_info_list:
                for r in range(rows):
                    sys.stdout.write(f"\033[{start_row + r};1H")
                    for c in range(cols):
                        ch = padded[r][c]
                        if ch == " ":
                            sys.stdout.write(" ")
                        else:
                            sys.stdout.write(f"{color}{ch}")
                    sys.stdout.write(reset)
            sys.stdout.flush()

            use_bright = not use_bright
            time.sleep(interval)
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def _should_show_startup_banner(argv: list[str] | None = None) -> bool:
    """判断是否需要播放启动横幅动画。"""
    if not sys.stdout.isatty():
        return False
    if os.environ.get("ASTRBOT_NO_BANNER"):
        return False
    argv = sys.argv[1:] if argv is None else argv
    if "-h" in argv or "--help" in argv:
        return False
    return True


def _run_startup_banner() -> None:
    """在后台线程中执行启动横幅动画，不阻塞主线程导入模块。"""
    try:
        if os.name == "nt":  # Windows
            os.system("cls")
        else:  # Linux / macOS
            os.system("clear")

        # 开头提示：动画与程序加载并行
        tip_color = bright_cyan if bright_cyan else cyan
        sys.stdout.write(
            f"{tip_color}正在启动中… 此动画不会阻塞程序加载{reset}\n\n"
        )
        sys.stdout.flush()

        # 艺术字从提示下方开始画（提示占 2 行：文案 + 空行）
        banner_start_row = 3
        lines_ldmnb = [line for line in ldmnb.split("\n") if line != ""]
        rows_first = len(lines_ldmnb)

        # 1. 第一个艺术字正常打印
        info1 = print_art_column_by_column(
            ldmnb, delay=0.03, start_row=banner_start_row
        )

        # 2. 第二个艺术字打印时，把第一个艺术字作为 extra_arts 传入，使其保持彩色闪烁
        extra_art = (banner_start_row, info1[0], info1[1], info1[2])
        info2 = print_art_column_by_column(
            ldmbot,
            delay=0.03,
            start_row=banner_start_row + rows_first + 1,
            extra_arts=[extra_art],
        )

        # 3. 统一颜色、亮暗交替闪烁 3 秒
        flash_unified_bright_dark(
            [
                (banner_start_row, info1[0], info1[1], info1[2]),
                (
                    banner_start_row + rows_first + 1,
                    info2[0],
                    info2[1],
                    info2[2],
                ),
            ],
            total_duration=3,
            interval=0.12,
        )

        # 4. 光标下移，结束
        total_rows = max(
            1, banner_start_row + rows_first + 1 + info2[0] - 1
        )
        sys.stdout.write(f"\033[{total_rows + 1};1H\n")
        sys.stdout.flush()
    except Exception:
        # 横幅动画失败不影响主程序启动
        try:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()
        except Exception:
            pass


_startup_banner_thread: threading.Thread | None = None


def start_startup_banner_async() -> None:
    """把启动横幅放到后台线程，与模块导入并行。"""
    global _startup_banner_thread
    if not _should_show_startup_banner():
        _startup_banner_thread = None
        return
    thread = threading.Thread(
        target=_run_startup_banner,
        name="startup-banner",
        daemon=True,
    )
    thread.start()
    _startup_banner_thread = thread


def wait_startup_banner() -> None:
    """等待启动横幅结束，避免与后续日志抢占终端。"""
    thread = _startup_banner_thread
    if thread is not None and thread.is_alive():
        thread.join()


# 尽早启动横幅，后续 import / bootstrap 与动画并行
start_startup_banner_async()

import runtime_bootstrap  # noqa: E402
import argparse
import asyncio
import mimetypes
from pathlib import Path
runtime_bootstrap.initialize_runtime_bootstrap()

DASHBOARD_RESET_PASSWORD_ENV = "ASTRBOT_RESET_DASHBOARD_PASSWORD"


def _apply_startup_env_flags(argv: list[str]) -> None:
    """Apply startup flags that must take effect before core imports.

    Args:
        argv: Command-line arguments excluding the executable name.
    """

    if "-h" in argv or "--help" in argv:
        return

    startup_parser = argparse.ArgumentParser(add_help=False)
    startup_parser.add_argument("--reset-password", action="store_true")
    startup_args, _ = startup_parser.parse_known_args(argv)
    if startup_args.reset_password:
        os.environ[DASHBOARD_RESET_PASSWORD_ENV] = "1"


_apply_startup_env_flags(sys.argv[1:])

from astrbot.core import LogBroker, LogManager, db_helper, logger  # noqa: E402
from astrbot.core.config.default import VERSION  # noqa: E402
from astrbot.core.initial_loader import InitialLoader  # noqa: E402
from astrbot.core.utils.astrbot_path import (  # noqa: E402
    get_astrbot_config_path,
    get_astrbot_data_path,
    get_astrbot_knowledge_base_path,
    get_astrbot_plugin_path,
    get_astrbot_root,
    get_astrbot_site_packages_path,
    get_astrbot_temp_path,
)
from astrbot.core.utils.io import (  # noqa: E402
    get_bundled_dashboard_dist_path,
    get_dashboard_dist_version,
    is_dashboard_dist_compatible,
    is_dashboard_version_compatible,
)
from astrbot.core.utils.runtime_env import is_packaged_desktop_runtime  # noqa: E402

# 将父目录添加到 sys.path
sys.path.append(Path(__file__).parent.as_posix())


def check_env() -> None:
    if not (sys.version_info.major == 3 and sys.version_info.minor >= 10):
        logger.error("请使用 Python3.10+ 运行本项目。")
        exit()

    astrbot_root = get_astrbot_root()
    if astrbot_root not in sys.path:
        sys.path.insert(0, astrbot_root)

    site_packages_path = get_astrbot_site_packages_path()
    if not is_packaged_desktop_runtime() and site_packages_path not in sys.path:
        sys.path.append(site_packages_path)

    os.makedirs(get_astrbot_config_path(), exist_ok=True)
    os.makedirs(get_astrbot_plugin_path(), exist_ok=True)
    os.makedirs(get_astrbot_temp_path(), exist_ok=True)
    os.makedirs(get_astrbot_knowledge_base_path(), exist_ok=True)
    os.makedirs(site_packages_path, exist_ok=True)

    # 针对问题 #181 的临时解决方案
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/javascript", ".mjs")
    mimetypes.add_type("application/json", ".json")


async def check_dashboard_files(webui_dir: str | None = None):
    """Resolve and repair dashboard static files for startup.

    Args:
        webui_dir: Optional explicit WebUI directory path from CLI.

    Returns:
        The directory path to serve, or None when no usable WebUI can be prepared.
    """

    # 指定webui目录
    if webui_dir:
        if os.path.exists(webui_dir):
            logger.info("使用指定的 WebUI 目录: %s", webui_dir)
            return webui_dir
        logger.warning("指定的 WebUI 目录不存在: %s，将使用默认逻辑。", webui_dir)

    data_dist_path = Path(get_astrbot_data_path()) / "dist"
    bundled_dist = get_bundled_dashboard_dist_path()
    if data_dist_path.exists():
        v = get_dashboard_dist_version(data_dist_path)
        if is_dashboard_dist_compatible(data_dist_path, VERSION):
            logger.info("WebUI 版本已是最新。")
            return str(data_dist_path)

        if is_dashboard_version_compatible(v, VERSION):
            logger.warning(
                "WebUI files are incomplete for v%s. 为保护本地自定义 WebUI，已禁止自动重新下载/覆盖 data/dist。",
                VERSION,
            )
        elif v is not None:
            logger.warning(
                "WebUI version mismatch: %s, expected v%s. 为保护本地自定义 WebUI，已禁止自动重新下载/覆盖 data/dist。",
                v,
                VERSION,
            )
        else:
            logger.warning(
                "WebUI version file is missing. 为保护本地自定义 WebUI，已禁止自动重新下载/覆盖 data/dist。",
            )

        if (data_dist_path / "index.html").is_file():
            logger.warning(
                "继续使用当前 data/dist WebUI。若页面异常，请手动构建 dashboard 并复制到 data/dist，且保留 data/dist/assets/version。"
            )
            return str(data_dist_path)

        logger.warning(
            "data/dist 存在但缺少 index.html，且自动下载 WebUI 已禁用；WebUI 功能将不可用。"
        )
        return None

    if is_dashboard_dist_compatible(bundled_dist, VERSION):
        logger.warning(
            "data/dist 不存在，自动下载 WebUI 已禁用；将临时使用随包 WebUI v%s，不会复制或覆盖 data/dist。",
            get_dashboard_dist_version(bundled_dist),
        )
        return str(bundled_dist)

    logger.warning(
        "data/dist 不存在，且没有兼容的随包 WebUI。自动下载 WebUI 已禁用；WebUI 功能将不可用。"
    )
    return None


async def main_async(webui_dir_arg: str | None) -> None:
    """主异步入口"""
    # 检查仪表板文件
    webui_dir = await check_dashboard_files(webui_dir_arg)
    if webui_dir is None:
        logger.warning(
            "管理面板文件检查失败，WebUI 功能将不可用。"
            "请检查网络连接或手动指定 --webui-dir 参数。"
        )

    db = db_helper

    core_lifecycle = InitialLoader(db, log_broker)
    core_lifecycle.webui_dir = webui_dir
    await core_lifecycle.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AstrBot")
    parser.add_argument(
        "--webui-dir",
        type=str,
        help="指定 WebUI 静态文件目录路径",
        default=None,
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="启动时重置管理面板初始密码，并在启动日志中打印",
    )
    args = parser.parse_args()

    # 等横幅播完再开始打日志，避免终端光标/内容被抢占
    wait_startup_banner()

    check_env()

    # 启动日志代理
    log_broker = LogBroker()
    LogManager.set_queue_handler(logger, log_broker)

    # 只使用一次 asyncio.run()
    asyncio.run(main_async(args.webui_dir))