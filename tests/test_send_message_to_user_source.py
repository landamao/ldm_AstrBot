"""send_message_to_user 的 path/url 来源归一化验证。

背景：`url` 字段原描述只有一句 "URL for ..."，模型经常把本地路径塞进 `url`，
`Comp.Image.fromURL()` 只认 http(s)，于是抛 `not a valid url` 并被包成
`error: failed to build messages[0] component: ...` —— 错误信息没告诉模型
应该改用 `path`，模型会原样重试，表现为「一直传错参数」。

改造后 `path` / `url` 语义等价，都接受：
本地绝对路径、会话工作区相对路径、沙箱路径、`file://` URI、`http(s)://` URL，
另外 `url` 还接受 `base64://` 与 `data:...;base64,`。

运行：python tests/test_send_message_to_user_source.py
"""

import asyncio
import atexit
import base64
import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.tools import message_tools as mt  # noqa: E402

TOOL = mt.SendMessageToUserTool()

_TMP_DIR = Path(tempfile.mkdtemp(dir=mt.get_astrbot_temp_path()))
atexit.register(shutil.rmtree, _TMP_DIR, ignore_errors=True)


class _FakeEvent:
    unified_msg_origin = "test:FriendMessage:1"
    role = "admin"
    _has_send_oper = False

    def __init__(self) -> None:
        self._extras = {}

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value


class _FakePluginContext:
    _db = None

    def __init__(self, sent: list) -> None:
        self.sent = sent

    def get_config(self, umo=None):
        return {
            "provider_settings": {
                "computer_use_runtime": "local",
                "computer_use_require_admin": False,
            }
        }

    async def send_message(self, session, chain) -> None:
        self.sent.append((str(session), chain))


def _make_context(sent: list | None = None):
    sent = [] if sent is None else sent
    return SimpleNamespace(
        context=SimpleNamespace(
            event=_FakeEvent(),
            context=_FakePluginContext(sent),
        )
    )


def _make_image_file() -> Path:
    """在机器人临时目录里造一个假图片，测试结束统一清理。"""
    image_path = _TMP_DIR / f"sample_{uuid.uuid4().hex[:6]}.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return image_path


def test_schema_declares_supported_forms():
    """schema 里必须把 file:// 与本地路径写清楚，并给出 type 枚举。"""
    import jsonschema

    jsonschema.Draft202012Validator.check_schema(TOOL.parameters)
    item = TOOL.parameters["properties"]["messages"]["items"]
    props = item["properties"]

    assert props["type"]["enum"] == [
        "plain",
        "image",
        "record",
        "video",
        "file",
        "mention_user",
    ]
    assert "file://" in props["url"]["description"]
    assert "http://" in props["url"]["description"]
    assert "local path" in props["url"]["description"]
    assert "file://" in props["path"]["description"]
    assert item["required"] == ["type"]


def test_classify_media_source_table():
    """各类取值归一结果。"""
    local = r"C:\Users\demo\a.png"
    cases = {
        local: ("path", local),
        "  " + local + "  ": ("path", local),
        '"' + local + '"': ("path", local),
        "https://example.com/a.png": ("url", "https://example.com/a.png"),
        "http://example.com/a.png": ("url", "http://example.com/a.png"),
        "base64://aGVsbG8=": ("base64", "aGVsbG8="),
        "data:image/png;base64,iVBORw0KGgo=": ("base64", "iVBORw0KGgo="),
        "": ("", ""),
        None: ("", ""),
    }
    for raw, expected in cases.items():
        assert mt._classify_media_source(raw) == expected, raw


def test_classify_file_uri_becomes_local_path():
    """file:// URI（含 Windows 盘符、大小写、引号）都解码成本地路径。"""
    uri = Path(r"C:\Users\demo\a b.png").as_uri()
    kind, value = mt._classify_media_source(uri)
    assert kind == "path"
    assert value == r"C:\Users\demo\a b.png"

    kind, value = mt._classify_media_source("FILE:///C:/Users/demo/a.png")
    assert kind == "path"
    assert value.lower().startswith("c:")


def test_url_accepts_file_uri():
    """url 传 file:// 时，构造出的组件与 path 传本地路径完全一致。"""
    image = _make_image_file()
    ctx = _make_context()

    async def build(msg):
        return await TOOL._build_media_component(ctx, msg, "image")

    from_uri = asyncio.run(build({"type": "image", "url": image.as_uri()}))
    from_path = asyncio.run(build({"type": "image", "path": str(image)}))

    assert from_uri.type == from_path.type
    assert from_uri.path == str(image)
    assert from_uri.file == from_path.file


def test_url_accepts_bare_local_path():
    """url 传本地路径不再抛 not a valid url。"""
    image = _make_image_file()
    ctx = _make_context()

    comp = asyncio.run(
        TOOL._build_media_component(ctx, {"type": "image", "url": str(image)}, "image")
    )
    assert comp.path == str(image)


def test_path_accepts_http_url():
    """反向也成立：path 里放了 http 地址，按 URL 处理。"""
    ctx = _make_context()
    comp = asyncio.run(
        TOOL._build_media_component(
            ctx, {"type": "image", "path": "https://example.com/a.png"}, "image"
        )
    )
    assert comp.file == "https://example.com/a.png"


def test_file_type_with_local_source_uses_file_field():
    """file 类型传本地路径必须落到 file=，不能静默变成下载失败的 url=。"""
    image = _make_image_file()
    ctx = _make_context()

    comp = asyncio.run(
        TOOL._build_media_component(ctx, {"type": "file", "url": str(image)}, "file")
    )
    assert comp.file_ == str(image)
    assert not comp.url
    assert comp.name == image.name


def test_file_type_uses_text_as_display_name():
    image = _make_image_file()
    ctx = _make_context()

    comp = asyncio.run(
        TOOL._build_media_component(
            ctx,
            {"type": "file", "url": image.as_uri(), "text": "报告.png"},
            "file",
        )
    )
    assert comp.name == "报告.png"
    assert comp.file_ == str(image)


def test_base64_payload_becomes_component():
    """base64:// 与 data URI 都能直接构造，无 padding 也能解。"""
    ctx = _make_context()
    payload = base64.b64encode(b"hello").decode().rstrip("=")

    image = asyncio.run(
        TOOL._build_media_component(
            ctx, {"type": "image", "url": f"base64://{payload}"}, "image"
        )
    )
    assert image.file == f"base64://{payload}"

    file_comp = asyncio.run(
        TOOL._build_media_component(
            ctx,
            {
                "type": "file",
                "url": f"data:text/plain;base64,{payload}",
                "text": "a.txt",
            },
            "file",
        )
    )
    assert Path(file_comp.file_).read_bytes() == b"hello"
    assert file_comp.name == "a.txt"


def test_missing_source_error_mentions_path():
    """没给来源时的报错要能自解释，不能只说 not a valid url。"""
    ctx = _make_context()
    try:
        asyncio.run(
            TOOL._build_media_component(ctx, {"type": "image", "text": "说明"}, "image")
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert "path" in message
    assert "file://" in message


def test_call_routes_aliases_and_reports_unsupported():
    """call() 端到端：类型别名可用，未知类型报出支持列表。"""
    sent: list = []
    ctx = _make_context(sent)
    image = _make_image_file()

    result = asyncio.run(
        TOOL.call(
            ctx,
            messages=[
                {"type": "text", "content": "看图"},
                {"type": "img", "url": image.as_uri()},
                {"type": "at", "user_id": "42"},
            ],
        )
    )
    assert result is None
    assert len(sent) == 1
    chain = sent[0][1].chain
    assert [getattr(c.type, "value", c.type) for c in chain] == [
        "Plain",
        "Image",
        "At",
    ]
    assert chain[1].path == str(image)
    assert chain[2].qq == "42"

    error = asyncio.run(TOOL.call(ctx, messages=[{"type": "sticker", "url": "x"}]))
    assert "unsupported message type 'sticker'" in error
    assert "file" in error


def test_messages_shape_tolerance():
    """messages 传 JSON 字符串或单个对象也能处理。"""
    sent: list = []
    ctx = _make_context(sent)
    image = _make_image_file()

    asyncio.run(TOOL.call(ctx, messages=json.dumps([{"type": "plain", "text": "hi"}])))
    assert [c.text for c in sent[0][1].chain] == ["hi"]

    sent.clear()
    asyncio.run(TOOL.call(ctx, messages={"type": "image", "url": str(image)}))
    assert sent[0][1].chain[0].path == str(image)

    error = asyncio.run(TOOL.call(ctx, messages="{not json"))
    assert "JSON array" in error


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")
