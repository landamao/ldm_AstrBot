"""更新回滚备份模块（纯标准库）。

本模块服务于两处调用：
1. updator.py：更新前备份当前版本到 data/ldmbot_rollback/（zip 形式）；
2. main.py 启动参数 --rollback：在 import astrbot 重模块之前用 importlib
   按文件路径加载本模块执行回滚。

因此本模块只允许 import 标准库，不得依赖任何 astrbot 包。
"""

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

说明文件名 = "说明.txt"
回滚目录名 = "ldmbot_rollback"
备份文件名模板 = "ldmbot_{version}.zip"


def _修复压缩包文件名(zf: zipfile.ZipFile) -> None:
    """就地修补无 UTF-8 标志位条目的文件名（cp437 还原字节 → utf-8/gbk 重解码）。

    本模块不得依赖 astrbot 包，故从 astrbot.core.utils.zip_fix 内联同等逻辑。
    备份 zip 由本模块自己打包（Python zipfile，UTF-8 标志自动正确），此修补
    是兜底：兼容外部工具替换过的备份包。
    """
    for info in zf.infolist():
        if info.flag_bits & 0x800:
            continue
        try:
            raw = info.filename.encode("cp437")
        except UnicodeEncodeError:
            continue
        for encoding in ("utf-8", "gbk"):
            try:
                fixed = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            if fixed != info.filename:
                info.filename = fixed
            break

说明文本 = (
    "本目录存放更新前的旧版本备份（zip 形式），\n"
    "供新版本异常时用启动参数 --rollback 回滚，例如：\n"
    "  cd ~/ldmbot && ./.venv/bin/python main.py --rollback           回滚到最近一次备份\n"
    "  cd ~/ldmbot && ./.venv/bin/python main.py --rollback 4.26.26   回滚到指定版本备份\n"
    "此命令仅供参考，请根据实际情况调整。\n"
    "若新版本正常运行，可安全删除本目录所有文件。\n"
)


def _支持颜色() -> bool:
    """终端是否支持 ANSI 颜色（非 tty / dumb / NO_COLOR 时不输出颜色码）。"""
    try:
        if not sys.stdout.isatty():
            return False
        if os.environ.get("TERM") == "dumb":
            return False
        if os.environ.get("NO_COLOR"):
            return False
        return True
    except Exception:
        return False


_绿 = "\033[32m"
_红 = "\033[31m"
_黄 = "\033[33m"
_重置 = "\033[0m"
if not _支持颜色():
    _绿 = _红 = _黄 = _重置 = ""


def resolve_data_dir(data_dir: str | None = None) -> str:
    """数据目录：显式指定 > LDMBOT_DATA_DIR > LDMBOT_ROOT/data > 源码根/data。

    与 astrbot/core/utils/astrbot_path.py 的 get_astrbot_data_path() 同规则，
    纯标准库实现（本模块可能在加载 astrbot 包之前被调用）。
    默认跟随项目源码自身位置而非启动时的 cwd。
    """
    if data_dir:
        return os.path.realpath(data_dir)
    if env := os.environ.get("LDMBOT_DATA_DIR"):
        return os.path.realpath(env)
    if root := os.environ.get("LDMBOT_ROOT"):
        return os.path.realpath(os.path.join(root, "data"))
    源码根 = _默认项目根()
    if os.path.isfile(os.path.join(源码根, "main.py")):
        return os.path.realpath(os.path.join(源码根, "data"))
    return os.path.realpath(os.path.join(os.getcwd(), "data"))


def get_rollback_dir(data_dir: str | None = None) -> Path:
    """回滚备份目录：data/ldmbot_rollback/。"""
    return Path(data_dir or resolve_data_dir()) / 回滚目录名


def ensure_rollback_dir(data_dir: str | None = None) -> Path:
    """创建回滚备份目录，并写入说明文件（每次覆盖写，保证说明为最新文案）。"""
    rollback_dir = get_rollback_dir(data_dir)
    rollback_dir.mkdir(parents=True, exist_ok=True)
    说明文件 = rollback_dir / 说明文件名
    try:
        说明文件.write_text(说明文本, encoding="utf-8")
    except OSError:
        # 说明文件写失败不影响备份功能
        pass
    return rollback_dir


def clear_dir_contents(target: Path) -> None:
    """清空目录内容，保留目录节点本身（软链接安全）。

    - 条目是软链接 → unlink 软链接本身（不跟目标）
    - 条目是真实目录 → rmtree
    - 条目是文件 → unlink

    目标本身若是软链接目录，则清空其指向的内容并保留软链接；
    目标不存在或不是可遍历目录（如断链软链接）时，删除并重建为真实目录。
    """
    target = Path(target)
    try:
        entries = list(target.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        # 目标不存在或不可遍历（如断链软链接 / 目标是文件）：删除后重建真实目录
        try:
            if target.is_symlink() or target.exists():
                target.unlink(missing_ok=True)
        except OSError:
            pass
        target.mkdir(parents=True, exist_ok=True)
        return

    for entry in entries:
        try:
            if entry.is_symlink():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except FileNotFoundError:
            pass


def _resolve_webui_dir(webui_dir: str | None) -> str | None:
    """解析显式指定的 WebUI 目录：--webui-dir/LDMBOT_WEBUI_DIR 指定且存在 → 用之；否则 None。"""
    if webui_dir and os.path.isdir(webui_dir):
        return webui_dir
    env_dir = os.environ.get("LDMBOT_WEBUI_DIR")
    if env_dir and os.path.isdir(env_dir):
        return env_dir
    return None


def _实际生效webui目录(webui_dir: str | None, data_dir: str | None = None) -> Path:
    """解析实际生效的 WebUI 目录（与运行时 resolve_dashboard_dist 同一优先级）。

    纯标准库实现（本模块不得 import astrbot 包）：
    1. 显式指定（--webui-dir/LDMBOT_WEBUI_DIR）且存在 → 用之
    2. 项目根 dashboard/dist 存在 → 用之（WebUI 随源码树走）
    3. data/dist（历史遗留回退）
    """
    显式 = _resolve_webui_dir(webui_dir)
    if 显式:
        return Path(显式)
    项目dist = Path(_默认项目根()) / "dashboard" / "dist"
    if 项目dist.is_dir():
        return 项目dist
    return Path(resolve_data_dir(data_dir)) / "dist"


def _zip_tree(zf: zipfile.ZipFile, src: Path, 根归档: str) -> None:
    """把 src 目录内容写入 zip，归档路径以 根归档 开头（如 astrbot/、dist/）。

    软链接按其指向内容写入（备份自包含）；指向目录时递归写入其内容。
    """
    for entry in sorted(src.iterdir(), key=lambda p: p.name):
        归档名 = f"{根归档}/{entry.name}"
        if entry.is_symlink():
            目标 = entry.resolve()
            if 目标.is_dir():
                zf.writestr(归档名 + "/", "")
                _zip_tree(zf, 目标, 归档名)
            else:
                try:
                    zf.write(entry, 归档名)
                except OSError as exc:
                    raise RuntimeError(f"备份失败: 无法读取软链接目标 {entry}: {exc}") from exc
        elif entry.is_dir():
            zf.writestr(归档名 + "/", "")
            _zip_tree(zf, entry, 归档名)
        else:
            zf.write(entry, 归档名)


def backup_current_version(
    project_root: str,
    version: str,
    webui_dir: str | None = None,
    data_dir: str | None = None,
) -> Path:
    """更新前备份当前版本到 data/ldmbot_rollback/ldmbot_<版本>.zip。

    包内含三样：astrbot/ 目录 + dist/ 目录 + 根目录 main.py
    （dist = 实际生效 WebUI 目录：显式 --webui-dir/LDMBOT_WEBUI_DIR →
    项目根 dashboard/dist → 历史遗留 data/dist）。
    多个版本可同时保留，重名则覆盖。备份失败抛出异常，由调用方中断更新
    （防止旧版本丢失后无法回滚）。
    """
    project_root = Path(project_root)
    源码目录 = project_root / "astrbot"
    if not 源码目录.is_dir():
        raise RuntimeError(f"备份失败: 项目源码目录不存在: {源码目录}")

    # dist：与运行时同一优先级解析实际生效目录
    dist目录: Path | None = None
    实际dist = _实际生效webui目录(webui_dir, data_dir)
    if 实际dist.is_dir():
        dist目录 = 实际dist

    rollback_dir = ensure_rollback_dir(data_dir)
    zip_path = rollback_dir / 备份文件名模板.format(version=version)

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            _zip_tree(zf, 源码目录, "astrbot")
            if dist目录 is not None:
                _zip_tree(zf, dist目录, "dist")
            # 根目录 main.py 也备份（入口脚本，回滚时一并恢复）
            main_py = project_root / "main.py"
            if main_py.is_file():
                zf.write(main_py, "main.py")
    except OSError as exc:
        raise RuntimeError(f"备份失败: 写入备份文件出错: {exc}") from exc

    return zip_path


def list_backups(data_dir: str | None = None) -> list[Path]:
    """列出回滚备份 zip（按修改时间倒序，最新在前）。"""
    rollback_dir = get_rollback_dir(data_dir)
    if not rollback_dir.is_dir():
        return []
    return sorted(
        (p for p in rollback_dir.glob("ldmbot_*.zip") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def find_backup(version: str | None, data_dir: str | None = None) -> Path | None:
    """查找回滚备份：指定版本 → ldmbot_<版本>.zip；未指定 → 最近一次备份。"""
    rollback_dir = get_rollback_dir(data_dir)
    if version:
        target = rollback_dir / 备份文件名模板.format(version=version)
        return target if target.is_file() else None
    backups = list_backups(data_dir)
    return backups[0] if backups else None


def _默认项目根() -> str:
    """默认项目根：本模块位于 <根>/astrbot/core/utils/ 下。"""
    return str(Path(__file__).resolve().parents[3])


def rollback(
    version: str | None = None,
    project_root: str | None = None,
    webui_dir: str | None = None,
    data_dir: str | None = None,
) -> bool:
    """回滚到指定版本备份（默认最近一次备份）。

    流程：解压备份 zip 到临时目录 → 清空当前 astrbot/、dist/ 内容
    （保留目录节点，软链接安全）→ 把包内内容复制回去（复制而非重命名）→
    根目录 main.py 备份中有则一并恢复 → 清理临时目录 → 继续正常启动
    （用旧版代码跑起来）。
    备份目录保留不删，用户确认新版没问题后手动删。
    找不到备份 / 指定版本不存在 → 打印中文提示并返回 False，继续正常启动。
    """
    project_root = Path(project_root or _默认项目根())
    rollback_dir = get_rollback_dir(data_dir)
    目标zip = find_backup(version, data_dir)

    if 目标zip is None:
        if version:
            print(
                f"{_红}回滚失败: 找不到版本 {version} 的回滚备份"
                f"（{rollback_dir} 下无 ldmbot_{version}.zip）。{_重置}"
            )
        else:
            print(
                f"{_红}回滚失败: 未找到任何回滚备份"
                f"（{rollback_dir} 目录为空，请先更新产生备份）。{_重置}"
            )
        return False

    备份版本 = 目标zip.stem.removeprefix("ldmbot_")
    print(f"{_绿}开始回滚: 使用备份 {目标zip.name}（版本 {备份版本}）...{_重置}")

    with tempfile.TemporaryDirectory(prefix="ldmbot-rollback-") as tmp:
        tmp_root = Path(tmp)
        try:
            with zipfile.ZipFile(目标zip, "r") as zf:
                corrupt = zf.testzip()
                if corrupt:
                    print(
                        f"{_红}回滚失败: 备份文件损坏（{目标zip.name}: {corrupt}）。{_重置}"
                    )
                    return False
                _修复压缩包文件名(zf)
                zf.extractall(tmp_root)
        except zipfile.BadZipFile as exc:
            print(f"{_红}回滚失败: 备份文件不是有效 zip（{目标zip.name}）: {exc}。{_重置}")
            return False

        包astrbot = tmp_root / "astrbot"
        if not 包astrbot.is_dir():
            print(f"{_红}回滚失败: 备份 {目标zip.name} 中缺少 astrbot/ 目录，内容不完整。{_重置}")
            return False

        # 1. 恢复 astrbot/（清空内容保留目录 → 复制回去）
        当前astrbot = project_root / "astrbot"
        if not 当前astrbot.is_dir():
            print(f"{_红}回滚失败: 当前项目目录不存在 astrbot/: {当前astrbot}。{_重置}")
            return False
        clear_dir_contents(当前astrbot)
        shutil.copytree(包astrbot, 当前astrbot, dirs_exist_ok=True)

        # 2. 恢复根目录 main.py（备份中有才恢复；无则保持现状并提示）
        包main = tmp_root / "main.py"
        if 包main.is_file():
            当前main = project_root / "main.py"
            shutil.copy2(包main, 当前main)
        else:
            print(f"{_黄}备份中无 main.py（旧备份），入口脚本保持现状。{_重置}")

        # 3. 恢复 dist/（备份中有才恢复；无则保持现状并提示）
        包dist = tmp_root / "dist"
        if 包dist.is_dir():
            当前dist = _实际生效webui目录(webui_dir, data_dir)
            当前dist.parent.mkdir(parents=True, exist_ok=True)
            clear_dir_contents(当前dist)
            shutil.copytree(包dist, 当前dist, dirs_exist_ok=True)
        else:
            print(f"{_黄}备份中无 dist/（备份时 WebUI 目录不存在），WebUI 保持现状。{_重置}")

    print(f"{_绿}回滚成功: 已恢复到版本 {备份版本}。{_重置}")
    return True
