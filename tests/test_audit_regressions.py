import ast
import asyncio
from pathlib import Path


源码根目录 = Path(__file__).parents[1] / "astrbot"


def _读取语法树(相对路径: str) -> ast.Module:
    路径 = 源码根目录 / 相对路径
    return ast.parse(路径.read_text(encoding="utf-8-sig"), filename=str(路径))


def test_gemini_计时模块已导入():
    语法树 = _读取语法树("core/provider/sources/gemini_source.py")
    已导入 = any(
        isinstance(节点, ast.Import)
        and any(别名.name == "time" for 别名 in 节点.names)
        for 节点 in 语法树.body
    )
    assert 已导入, "Gemini 请求路径使用 time.perf_counter()，必须导入 time"


def test_webchat不会吞掉取消异常():
    语法树 = _读取语法树("dashboard/services/chat_service.py")
    捕获基类异常 = [
        节点
        for 节点 in ast.walk(语法树)
        if isinstance(节点, ast.ExceptHandler)
        and isinstance(节点.type, ast.Name)
        and 节点.type.id == "BaseException"
    ]
    assert not 捕获基类异常, "WebChat 流式处理不能吞掉 CancelledError"


def test_微信输入状态finally不返回():
    语法树 = _读取语法树("core/platform/sources/weixin_oc/weixin_oc_adapter.py")
    函数 = next(
        节点
        for 节点 in ast.walk(语法树)
        if isinstance(节点, ast.AsyncFunctionDef)
        and 节点.name == "_delayed_cancel_typing"
    )
    finally返回 = []
    for 节点 in ast.walk(函数):
        if isinstance(节点, ast.Try):
            finally模块 = ast.Module(body=节点.finalbody, type_ignores=[])
            finally返回.extend(
                子节点 for 子节点 in ast.walk(finally模块) if isinstance(子节点, ast.Return)
            )
    assert not finally返回, "finally 中 return 会覆盖 CancelledError"


def test_wecom监听任务回调不会误删新任务():
    源码 = (
        源码根目录 / "core/platform/sources/wecom_ai_bot/wecomai_queue_mgr.py"
    ).read_text(encoding="utf-8-sig")
    assert "lambda _: self._listener_tasks.pop(session_id, None)" not in 源码


def test_公众号重复消息会等待已有future且仅所有者清理():
    源码 = (
        源码根目录
        / "core/platform/sources/weixin_official_account/weixin_offacc_adapter.py"
    ).read_text(encoding="utf-8-sig")
    assert "owns_future = future is None" in 源码
    assert "result = await asyncio.wait_for(" in 源码
    assert "if owns_future and self.wexin_event_workers.get(msg_id) is future:" in 源码


def test_webui固定默认策略保持不变():
    默认配置 = (
        源码根目录 / "core/config/default.py"
    ).read_text(encoding="utf-8-sig")
    密码实现 = (
        源码根目录 / "core/utils/auth_password.py"
    ).read_text(encoding="utf-8-sig")
    assert 'DEFAULT_DASHBOARD_PASSWORD = "ldm"' in 密码实现
    assert '"username": "ldm"' in 默认配置
    assert '"host": "0.0.0.0"' in 默认配置
    assert '"password_change_required": False' in 默认配置


def test_mcp配置返回会脱敏():
    import importlib.util

    路径 = 源码根目录 / "dashboard/services/tools_service.py"
    规格 = importlib.util.spec_from_file_location("tools_service_audit", 路径)
    assert 规格 and 规格.loader
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    配置 = {
        "headers": {"Authorization": "Bearer secret", "X-Name": "ok"},
        "env": {"API_KEY": "secret", "MODE": "prod"},
    }
    结果 = 模块._脱敏_mcp配置(配置)
    assert 结果["headers"]["Authorization"] == "******"
    assert 结果["headers"]["X-Name"] == "ok"
    assert 结果["env"]["API_KEY"] == "******"
    assert 结果["env"]["MODE"] == "prod"


def test_插件热重载任务已纳入管理器():
    源码 = (源码根目录 / "core/star/star_manager.py").read_text(encoding="utf-8-sig")
    assert "self._watcher_task" in 源码
    assert "async def shutdown" in 源码


def test_代理测试拒绝私网地址():
    语法树 = _读取语法树("dashboard/services/stat_service.py")
    函数名 = {
        节点.name
        for 节点 in ast.walk(语法树)
        if isinstance(节点, ast.AsyncFunctionDef)
    }
    assert "_validate_public_http_url" in 函数名


def test_代理地址校验拒绝回环地址(monkeypatch):
    import importlib.util

    路径 = 源码根目录 / "dashboard/services/stat_service.py"
    规格 = importlib.util.spec_from_file_location("stat_service_audit", 路径)
    assert 规格 and 规格.loader
    模块 = importlib.util.module_from_spec(规格)
    规格.loader.exec_module(模块)
    monkeypatch.setattr(
        模块.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 80))],
    )

    async def _校验():
        try:
            await 模块.StatService._validate_public_http_url("http://example.com")
        except 模块.StatServiceError:
            return
        raise AssertionError("必须拒绝回环地址")

    asyncio.run(_校验())
