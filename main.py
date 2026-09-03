print("正在加载...")
import atexit
import signal
import os, sys, time, random, threading

# ========== 终端光标兜底恢复 ==========
# 横幅动画在 daemon 线程里隐藏光标（\033[?25l），程序退出时 daemon 线程被强杀，
# finally 里的 \033[?25h 可能来不及执行，导致终端光标永久消失。
# 注册 atexit + signal 兜底，确保无论怎么退出都恢复光标。
_ORIG_CURSOR_HANDLER = None


def _restore_terminal_cursor(*_args: object) -> None:
    try:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
    except Exception:
        pass


atexit.register(_restore_terminal_cursor)


def _install_cursor_signal_handler() -> None:
    global _ORIG_CURSOR_HANDLER
    for sig in (signal.SIGTERM,):
        try:
            _ORIG_CURSOR_HANDLER = signal.getsignal(sig)
            signal.signal(sig, _restore_terminal_cursor)
        except (ValueError, OSError):
            pass  # 非主线程或信号不可用


_install_cursor_signal_handler()


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
    if os.environ.get("LDMBOT_NO_BANNER"):
        return False
    argv = sys.argv[1:] if argv is None else argv
    if "-h" in argv or "--help" in argv:
        return False
    if "--reset-password" in argv or "--重置密码" in argv:
        return False
    if "--rollback" in argv or "--回滚" in argv:
        return False
    return True


def _run_startup_banner() -> None:
    """在后台线程中执行启动横幅动画，不阻塞主线程导入模块。"""
    try:
        # 不真正清屏（clear/cls 会连滚动缓冲一起抹掉，旧内容无法回滚）：
        # 输出整屏换行把旧内容推到上方（保留可回滚），再把光标复位到
        # 第 1 行，与清屏后的布局一致（提示语在顶部、艺术字在下方）
        try:
            terminal_rows = os.get_terminal_size().lines
        except OSError:
            terminal_rows = 50
        sys.stdout.write("\n" * (terminal_rows + 1))
        sys.stdout.write("\033[H")  # 光标复位到第 1 行
        sys.stdout.flush()

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
    # 横幅一启动就置位：LogManager 初始化时会读到并挂起控制台
    os.environ["LDMBOT_PAUSE_CONSOLE"] = "1"
    thread = threading.Thread(
        target=_run_startup_banner,
        name="startup-banner",
        daemon=True,
    )
    thread.start()
    _startup_banner_thread = thread

    # 横幅结束后必定恢复控制台（即使未走到 __main__，也不会永久挂起）
    def _flush_after_banner() -> None:
        try:
            thread.join()
        finally:
            try:
                from astrbot.core import LogManager  # noqa: WPS433

                LogManager.resume_console()
            except Exception:
                # LogManager 尚未导入时，清掉环境位即可
                os.environ.pop("LDMBOT_PAUSE_CONSOLE", None)

    threading.Thread(
        target=_flush_after_banner,
        name="startup-banner-flush",
        daemon=True,
    ).start()


def is_startup_banner_running() -> bool:
    """横幅后台线程是否仍在播放。"""
    thread = _startup_banner_thread
    return thread is not None and thread.is_alive()


def wait_startup_banner() -> None:
    """兼容旧调用：仍会阻塞等待横幅结束（不推荐）。"""
    thread = _startup_banner_thread
    if thread is not None and thread.is_alive():
        thread.join()


def arm_startup_banner_console_release() -> None:
    """真正零阻塞：主线程不 join 横幅。

    若横幅仍在播，确保控制台已挂起；结束恢复由 banner 配套 flush 线程负责。
    文件日志 / WebUI 日志队列不受影响，业务启动继续前进。
    """
    from astrbot.core import LogManager  # noqa: WPS433

    if is_startup_banner_running():
        LogManager.pause_console()
        return
    # 横幅已结束或未启用：确保控制台打开
    LogManager.resume_console()


# 尽早启动横幅，后续 import / bootstrap 与动画并行
start_startup_banner_async()

import argparse

# --help/-h：提前处理，跳过所有重模块加载
if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
    _parser = argparse.ArgumentParser(
        prog="python main.py",
        description="LDMBot — 基于 AstrBot 的聊天机器人框架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
启动参数:
  --data-dir <路径>        指定 data 目录路径（等价 LDMBOT_DATA_DIR）
  --webui-dir <路径>       指定 WebUI 静态文件目录路径（默认 data/dist）
  --restore-backup [路径]      交互式恢复数据备份；不填路径时从备份目录选择
  --rollback, --回滚 [版本号]  回滚到旧版本备份；不填版本号时进入交互式选择
  --reset-password, --重置密码  重置管理面板密码（交互式输入新密码，留空用默认 "ldm"）
                           改完直接退出，需重新启动
  -h, --help               显示本帮助信息

环境变量:

  路径与运行模式:
    LDMBOT_DATA_DIR=<路径>              直接指定 data 目录路径
    LDMBOT_ROOT=<路径>                  根目录，data 目录 = $LDMBOT_ROOT/data
                                        （LDMBOT_DATA_DIR 优先级更高）
    LDMBOT_CLI=1                      标记由 CLI 启动（内部使用）
    LDMBOT_RELOAD=1                   启用插件热重载
    LDMBOT_LAUNCHER=1                 标记由 Launcher 启动（内部使用）
    LDMBOT_WEBUI_DIR=<路径>           自定义 WebUI dist 目录

  Dashboard / WebUI:
    LDMBOT_DASHBOARD_PORT=<端口>      监听端口（默认 6185）
    LDMBOT_DASHBOARD_HOST=<地址>      监听地址（默认 0.0.0.0）
    LDMBOT_DASHBOARD_SSL_ENABLE=1     启用 HTTPS
    LDMBOT_DASHBOARD_SSL_CERT=<路径>  SSL 证书文件
    LDMBOT_DASHBOARD_SSL_KEY=<路径>   SSL 私钥文件
    LDMBOT_DASHBOARD_SSL_CA_CERTS=<路径>  SSL CA 证书
    LDMBOT_DASHBOARD_INITIAL_PASSWORD=<密码>  重置密码时使用的密码（配合 RESET 使用，不设默认 "ldm"）
    LDMBOT_RESET_DASHBOARD_PASSWORD=1  触发重置 Dashboard 密码（配合 INITIAL_PASSWORD 使用）
    LDMBOT_DASHBOARD_SKIP_DEFAULT_PASSWORD_AUTH=1  跳过默认密码认证（仅本地）
    LDMBOT_TEST_MODE=true             测试模式（跳过部分初始化）

  Desktop 客户端:
    LDMBOT_DESKTOP_CLIENT=1           打包 Desktop 运行时
    LDMBOT_DESKTOP_MANAGED=1          Desktop 托管模式
    LDMBOT_DESKTOP_CORE_LOCK_PATH=<路径>  Desktop 核心锁文件

  MCP:
    LDMBOT_MCP_INIT_TIMEOUT=<秒>     MCP 初始化超时（默认 180，上限 300）
    LDMBOT_MCP_ENABLE_TIMEOUT=<秒>    MCP 动态启用超时（默认 180）
    LDMBOT_MCP_STDIO_ALLOWED_COMMANDS=<cmd1,cmd2,...>  MCP stdio 命令白名单

  启动行为:
    LDMBOT_NO_BANNER=1                跳过启动横幅动画
    LDMBOT_PAUSE_CONSOLE=1            暂停控制台日志输出（内部使用）

  更新器:
    LDMBOT_REPO_OWNER=<所有者>        GitHub 仓库所有者（默认 landamao）
    LDMBOT_REPO_NAME=<仓库名>         GitHub 仓库名（默认 ldm_AstrBot）
    LDMBOT_UPDATE_CACHE_TTL=<秒>      远端信息缓存秒数（默认 300）
    LDMBOT_GITHUB_TOKEN=<token>       GitHub API Token，提高限流配额
    LDMBOT_CORE_PACKAGE_BASE_URL=<URL>  核心包下载基础 URL

  Provider / 代理:
    LDMBOT_DASHSCOPE_API_KEY=<key>    阿里云百炼 API Key（Embedding/Rerank 回退）
    https_proxy / http_proxy          标准 HTTP 代理（Provider 默认读取）

  第三方平台:
    LDMBOT_COZE_API_KEY=<key>         Coze API 密钥
    LDMBOT_COZE_BOT_ID=<id>           Coze Bot ID
    LDMBOT_DINGTALK_REGISTRATION_BASE_URL=<URL>  钉钉注册基础 URL
    LDMBOT_DINGTALK_REGISTRATION_SOURCE=<来源>    钉钉注册来源标识

  其他:
    LDMBOT_DEMO_MODE=true             演示模式
    LDMBOT_NO_PLUGINS=true            跳过第三方插件加载（仅内置插件，可用于排除插件故障调试）
    LDMBOT_BAY_DATA_DIR=<路径>        Bay 凭据目录
    LDMBOT_DISABLE_METRICS=1          禁用指标上传
    LDMBOT_PLATFORM_STATS_INVALID_COUNT_WARN_LIMIT=<数>  备份导入告警阈值（默认 5）
    LDMBOT_BUILD_DASHBOARD=1          构建时编译 Dashboard 前端

用法示例:
  ./.venv/bin/python main.py                                  正常启动
  ./.venv/bin/python main.py --data-dir /path/to/data         指定 data 目录
  ./.venv/bin/python main.py --webui-dir /path/to/dist        指定 WebUI 目录
  ./.venv/bin/python main.py --rollback                       进入交互式选择回滚备份
  ./.venv/bin/python main.py --rollback 4.26.26               回滚到指定版本备份
  ./.venv/bin/python main.py --restore-backup                  交互式选择并恢复数据备份
  ./.venv/bin/python main.py --restore-backup /path/to/backup.zip  恢复指定数据备份
  ./.venv/bin/python main.py --reset-password                 重置管理面板密码
  LDMBOT_DASHBOARD_PORT=8080 ./.venv/bin/python main.py       指定端口启动
  LDMBOT_NO_BANNER=1 ./.venv/bin/python main.py               跳过横幅动画
""",
    )
    _parser.add_argument(
        "--data-dir",
        type=str,
        help="指定 data 目录路径（等价 LDMBOT_DATA_DIR 环境变量）",
        default=None,
    )
    _parser.add_argument(
        "--webui-dir",
        type=str,
        help="指定 WebUI 静态文件目录路径（默认优先项目根 dashboard/dist）",
        default=None,
    )
    _parser.add_argument(
        "--reset-password",
        "--重置密码",
        action="store_true",
        help="重置管理面板密码（交互式输入，留空用默认 ldm），改完退出需重启",
    )
    _parser.add_argument(
        "--rollback",
        "--回滚",
        nargs="?",
        const="",
        default=None,
        help="回滚到旧版本备份（不带版本号=最近一次备份，如 --rollback 4.26.26）",
    )
    _parser.parse_args()
    sys.exit(0)

import runtime_bootstrap  # noqa: E402
import asyncio
import mimetypes
from pathlib import Path
runtime_bootstrap.initialize_runtime_bootstrap()


def _apply_startup_env_flags(argv: list[str]) -> None:
    """Apply startup flags that must take effect before core imports.

    Args:
        argv: Command-line arguments excluding the executable name.
    """

    if "-h" in argv or "--help" in argv:
        return

    startup_parser = argparse.ArgumentParser(add_help=False)
    startup_parser.add_argument("--reset-password", "--重置密码", action="store_true", dest="reset_password")
    startup_parser.add_argument("--data-dir", type=str, default=None)
    startup_parser.add_argument("--webui-dir", type=str, default=None)
    startup_parser.add_argument("--rollback", "--回滚", nargs="?", const="", default=None)
    startup_parser.add_argument("--restore-backup", "--恢复备份", nargs="?", const="", default=None)
    startup_args, _ = startup_parser.parse_known_args(argv)
    if startup_args.data_dir:
        os.environ["LDMBOT_DATA_DIR"] = startup_args.data_dir
    if startup_args.rollback is not None:
        _do_rollback(startup_args.rollback or None, webui_dir=startup_args.webui_dir)
    if startup_args.reset_password:
        _prompt_and_set_reset_password()


def _do_rollback(version: str | None, webui_dir: str | None = None) -> None:
    """执行启动参数 --rollback：解压备份 zip → 清空 astrbot/dist → 复制回去。

    在本阶段不能 import astrbot 包（会加载重模块），因此用 importlib
    按文件路径加载纯标准库的回滚模块。回滚无论成功或失败都直接退出，
    不继续启动服务：成功时提示重启生效，失败时提示检查后重启。
    """
    try:
        import importlib.util

        模块路径 = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "astrbot",
            "core",
            "utils",
            "update_rollback.py",
        )
        spec = importlib.util.spec_from_file_location("ldmbot_update_rollback", 模块路径)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载回滚模块: {模块路径}")
        回滚模块 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(回滚模块)

        if version is None:
            备份列表 = 回滚模块.list_backups()
            if not 备份列表:
                print(f"{red}回滚失败：没有找到可用的回滚备份。{reset}")
                sys.exit(1)
            print("可回滚的备份包：")
            for index, 备份路径 in enumerate(备份列表, 1):
                print(f"  {index}. {备份路径.name}")
            选择 = input("请输入序号，或直接输入备份文件路径：").strip()
            try:
                目标备份 = 备份列表[int(选择) - 1]
            except (ValueError, IndexError):
                目标备份 = Path(选择).expanduser().resolve()
            if not 目标备份.is_file():
                print(f"{red}回滚失败：备份文件不存在: {目标备份}{reset}")
                sys.exit(1)
            version = str(目标备份)

        回滚成功 = 回滚模块.rollback(
            version=version,
            project_root=os.path.dirname(os.path.abspath(__file__)),
            webui_dir=webui_dir,
        )
    except Exception as exc:
        print(f"{red}回滚失败: {exc}。{reset}")
        sys.exit(1)

    if 回滚成功:
        print(f"{green}回滚完成，请重新启动 ldm 生效。{reset}")
        sys.exit(0)
    # 失败原因已由回滚模块打印（找不到备份 / 指定版本不存在 / 备份损坏等）
    sys.exit(1)


def _prompt_and_set_reset_password() -> None:
    """交互式输入新密码并直接写入配置文件，完成后退出提示重启。

    交互式：提示输入新密码，留空使用默认 "ldm"；
    非交互式：直接使用 "ldm"。
    两种情况都写完配置后退出，不继续启动。
    """
    password = "ldm"
    if sys.stdin and sys.stdin.isatty():
        raw = input('请输入新的管理面板密码（留空使用默认 "ldm"）: ').strip()
        if raw:
            password = raw

    # 直接写入配置文件
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    from astrbot.core.utils.auth_password import (
        hash_dashboard_password,
        hash_md5_dashboard_password,
    )
    import json

    data_path = get_astrbot_data_path()
    config_path = os.path.join(data_path, "cmd_config.json")

    if not os.path.exists(config_path):
        print("配置文件不存在，请先正常启动一次生成配置。")
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

    print(f"管理面板密码已重置为: {password}")
    print("请重新启动 ldm 生效。")
    sys.exit(0)


_apply_startup_env_flags(sys.argv[1:])

from astrbot.core import LogBroker, LogManager, db_helper, logger  # noqa: E402
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
from astrbot.core.dashboard_assets import resolve_dashboard_dist  # noqa: E402
from astrbot.core.utils.runtime_env import is_packaged_desktop_runtime  # noqa: E402

# 日志系统已就绪：若横幅还在播，立刻挂起控制台，避免 import 后续日志打穿动画
if is_startup_banner_running():
    LogManager.pause_console()

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
    """Resolve the WebUI dist directory for startup.

    解析优先级统一委托 resolve_dashboard_dist：
    1. 显式指定的 --webui-dir（存在即用）
    2. 项目根 dashboard/dist（源码内置，优先）
    3. 随包 astrbot/dashboard/dist
    4. data/dist（历史遗留，仅作最后回退）

    Args:
        webui_dir: Optional explicit WebUI directory path from CLI.

    Returns:
        The directory path to serve, or None when no usable WebUI can be found.
    """

    resolved = resolve_dashboard_dist(webui_dir)
    if resolved is None:
        logger.warning(
            "未找到可用的 WebUI 目录（dashboard/dist 与 data/dist 均不可用）；"
            "请构建 dashboard 并部署到项目根 dashboard/dist，或重新安装 ldm（安装包自带 WebUI），"
            "也可用 --webui-dir 手动指定。"
        )
        return None
    logger.info("WebUI 目录: %s", resolved)
    return str(resolved)


async def main_async(webui_dir_arg: str | None) -> None:
    """主异步入口"""
    # 检查仪表板文件（仅用于启动日志与缺失告警）
    webui_dir = await check_dashboard_files(webui_dir_arg)
    if webui_dir is None:
        logger.warning(
            "管理面板文件检查失败，WebUI 功能将不可用。"
            "请检查网络连接或手动指定 --webui-dir 参数。"
        )

    db = db_helper

    core_lifecycle = InitialLoader(db, log_broker)
    # 传原始启动参数而非解析结果：解析结果回传会被 dashboard server
    # 二次解析时误判为「用户显式指定的 --webui-dir」，版本不匹配时
    # 打出错误的「显式指定的 WebUI 目录」警告（实际用户并未指定）。
    # server.py 内部会用 resolve_dashboard_dist 按完整优先级重新解析。
    core_lifecycle.webui_dir = webui_dir_arg
    await core_lifecycle.start()


async def restore_backup_interactive(backup_path: str | None) -> None:
    """启动前交互式恢复数据备份。"""
    from astrbot.core.backup.importer import AstrBotImporter
    from astrbot.core.utils.astrbot_path import get_astrbot_backups_path, get_astrbot_data_path

    if not backup_path:
        backup_dir = Path(get_astrbot_backups_path())
        backups = sorted(backup_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not backups:
            print("备份目录中没有 ZIP 备份文件。")
            sys.exit(1)
        print("可恢复的备份包：")
        for index, path in enumerate(backups, 1):
            print(f"  {index}. {path.name}")
        choice = input("请输入序号，或直接输入备份文件路径：").strip()
        try:
            backup_path = str(backups[int(choice) - 1])
        except (ValueError, IndexError):
            backup_path = choice

    path = Path(backup_path).expanduser().resolve()
    if not path.is_file():
        print(f"备份文件不存在: {path}")
        sys.exit(1)
    importer = AstrBotImporter(
        main_db=db_helper,
        config_path=str(Path(get_astrbot_data_path()) / "cmd_config.json"),
    )
    check = importer.pre_check(str(path))
    if not check.valid:
        print(f"备份检查失败: {check.error}")
        sys.exit(1)
    print(f"备份版本: {check.backup_version}，当前版本: {check.current_version}")
    if input("是否恢复备份中的 WebUI 端口号？[y/N]: ").strip().lower() == "y":
        restore_webui_port = True
    else:
        restore_webui_port = False
    if input("是否恢复备份中的账号密码？[y/N]: ").strip().lower() == "y":
        restore_account_password = True
    else:
        restore_account_password = False
    if input("确认恢复？这将覆盖现有数据 [y/N]: ").strip().lower() != "y":
        print("已取消恢复。")
        sys.exit(0)
    result = await importer.import_all(
        str(path),
        restore_webui_port=restore_webui_port,
        restore_account_password=restore_account_password,
    )
    if not result.success:
        print("恢复失败: " + "; ".join(result.errors))
        sys.exit(1)
    print("恢复完成：已默认保留当前 WebUI 端口号和账号密码，请重新启动 ldm 生效。")
    sys.exit(0)


if __name__ == "__main__":
    # argparse 在前面 --help 拦截处已定义，这里只解析实际启动参数
    _parser = argparse.ArgumentParser(
        prog="python main.py",
        description="LDMBot — 基于 AstrBot 的聊天机器人框架",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _parser.add_argument(
        "--data-dir",
        type=str,
        help="指定 data 目录路径（等价 LDMBOT_DATA_DIR 环境变量）",
        default=None,
    )
    _parser.add_argument(
        "--webui-dir",
        type=str,
        help="指定 WebUI 静态文件目录路径（默认优先项目根 dashboard/dist）",
        default=None,
    )
    _parser.add_argument(
        "--reset-password",
        "--重置密码",
        action="store_true",
        help="重置管理面板密码（交互式输入，留空用默认 ldm），改完退出需重启",
    )
    _parser.add_argument(
        "--rollback",
        "--回滚",
        nargs="?",
        const="",
        default=None,
        help="回滚到旧版本备份（不带版本号=最近一次备份，如 --rollback 4.26.26）",
    )
    _parser.add_argument(
        "--restore-backup",
        "--恢复备份",
        nargs="?",
        const="",
        default=None,
        help="交互式恢复数据备份（不填路径时从备份目录选择）",
    )
    args = _parser.parse_args()

    # 零阻塞：不 join 横幅；挂起控制台日志，动画结束后再冲刷
    arm_startup_banner_console_release()

    check_env()

    # 启动日志代理
    log_broker = LogBroker()
    LogManager.set_queue_handler(logger, log_broker)

    # 只使用一次 asyncio.run()
    if args.restore_backup is not None:
        asyncio.run(restore_backup_interactive(args.restore_backup or None))
    asyncio.run(main_async(args.webui_dir))