"""非 ASCII(中文)API Key 透传:必须以 UTF-8 bytes 进 HTTP 头而非炸 ascii 编码。"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
from astrbot.core.provider.sources.openai_image_generation_source import (
    ProviderOpenAIImageGeneration,
)
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

中文密钥 = "中文密钥测试"


def _起本地服务器(头名: str, 响应体: bytes):
    """起一个记录指定请求头的本地 HTTP 服务器,返回 (端口, 收到的头容器)。"""

    received = {}

    class 处理器(BaseHTTPRequestHandler):
        def do_GET(self):
            received.setdefault("headers", []).append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(响应体)))
            self.end_headers()
            self.wfile.write(响应体)

        def log_message(self, *args):
            pass

    服务器 = HTTPServer(("127.0.0.1", 0), 处理器)
    端口 = 服务器.server_address[1]
    threading.Thread(target=服务器.serve_forever, daemon=True).start()
    return 端口, received


def _解码收到的头(值: str) -> str:
    """http.server 按 latin-1 解码线上字节,反转回去拿 UTF-8 原文。"""
    return value if (value := 值) is None else value.encode("latin-1").decode("utf-8")


def test_中文密钥经openai源以UTF8上线并支持切换():
    端口, received = _起本地服务器("Authorization", b'{"object": "list", "data": []}')
    配置 = {
        "id": "test-cn",
        "type": "openai_chat_completion",
        "model": "gpt-4o",
        "key": [中文密钥],
        "api_base": f"http://127.0.0.1:{端口}/v1",
        "timeout": 10,
        "enable": True,
        "provider_type": "chat_completion",
    }
    提供商 = ProviderOpenAIOfficial(配置, {})
    try:
        asyncio.run(提供商.get_models())
        assert _解码收到的头(received["headers"][0]["Authorization"]) == f"Bearer {中文密钥}"

        提供商.set_key("ascii-key-2")
        asyncio.run(提供商.get_models())
        assert received["headers"][1]["Authorization"] == "Bearer ascii-key-2"

        提供商.set_key("新中文密钥")
        asyncio.run(提供商.get_models())
        assert _解码收到的头(received["headers"][2]["Authorization"]) == "Bearer 新中文密钥"
    finally:
        asyncio.run(提供商.terminate())


def test_中文密钥经anthropic源以UTF8上线并支持切换():
    端口, received = _起本地服务器("X-Api-Key", b'{"data": []}')
    配置 = {
        "id": "test-cn",
        "type": "anthropic_chat_completion",
        "model": "claude-3-5-sonnet",
        "key": [中文密钥],
        "api_base": f"http://127.0.0.1:{端口}",
        "timeout": 10,
        "enable": True,
        "provider_type": "chat_completion",
    }
    提供商 = ProviderAnthropic(配置, {})
    try:
        asyncio.run(提供商.get_models())
        assert _解码收到的头(received["headers"][0]["X-Api-Key"]) == 中文密钥

        提供商.set_key("ascii-key")
        asyncio.run(提供商.get_models())
        assert received["headers"][1]["X-Api-Key"] == "ascii-key"

        提供商.set_key("新中文密钥")
        asyncio.run(提供商.get_models())
        assert _解码收到的头(received["headers"][2]["X-Api-Key"]) == "新中文密钥"
    finally:
        asyncio.run(提供商.terminate())


def test_中文密钥经gemini源以UTF8上线并支持切换():
    响应体 = json.dumps(
        {
            "models": [
                {"name": "models/gemini-2.5-flash", "supportedActions": ["generateContent"]}
            ]
        }
    ).encode()
    端口, received = _起本地服务器("x-goog-api-key", 响应体)
    配置 = {
        "id": "test-cn",
        "type": "gemini_chat_completion",
        "model": "gemini-2.5-flash",
        "key": [中文密钥],
        "api_base": f"http://127.0.0.1:{端口}",
        "timeout": 10,
        "enable": True,
        "provider_type": "chat_completion",
    }
    提供商 = ProviderGoogleGenAI(配置, {})
    asyncio.run(提供商.get_models())
    assert _解码收到的头(received["headers"][0]["x-goog-api-key"]) == 中文密钥

    提供商.set_key("ascii-key")
    asyncio.run(提供商.get_models())
    assert received["headers"][1]["x-goog-api-key"] == "ascii-key"

    提供商.set_key("新中文密钥")
    asyncio.run(提供商.get_models())
    assert _解码收到的头(received["headers"][2]["x-goog-api-key"]) == "新中文密钥"


def test_中文密钥经生图源以UTF8上线():
    端口, received = _起本地服务器("Authorization", b'{"data": [{"id": "img-model"}]}')
    配置 = {
        "id": "test-img",
        "type": "openai_image_generation",
        "model": "dall-e-3",
        "key": [中文密钥],
        "api_base": f"http://127.0.0.1:{端口}",
        "timeout": 10,
        "enable": True,
        "provider_type": "image_generation",
    }
    提供商 = ProviderOpenAIImageGeneration(配置, {})
    try:
        asyncio.run(提供商.initialize())
        models = asyncio.run(提供商.get_models())
        assert models == ["img-model"]
        assert _解码收到的头(received["headers"][0]["Authorization"]) == f"Bearer {中文密钥}"
    finally:
        asyncio.run(提供商.terminate())


def test_get_wire_key_ascii原样非ascii转bytes():
    提供商 = ProviderOpenAIOfficial(
        {"id": "t", "type": "openai_chat_completion", "key": ["placeholder"], "enable": True},
        {},
    )
    assert 提供商.get_wire_key("sk-abc123") == "sk-abc123"
    assert 提供商.get_wire_key("") == ""
    assert 提供商.get_wire_key(None) is None
    assert 提供商.get_wire_key("中文密钥") == "中文密钥".encode()
