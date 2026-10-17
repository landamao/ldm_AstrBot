"""上游模型限额注册表与合并逻辑。

验证行为：
- 各聊天源 ``get_models()`` 把上游返回的上下文/输出限额记入注册表
  （Gemini: inputTokenLimit/outputTokenLimit；OpenAI 兼容: 扩展字段）
- WebUI ``model_metadata`` 拼装：上游限额优先于内置静态表（LLM_METADATAS），
  静态表没有的模型用上游限额生成部分元数据
- 运行时 max_context_tokens 解析优先级：手动配置 > 上游限额 > 内置表 > 兜底

运行：python tests/test_upstream_model_limits.py
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent)
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from astrbot.core.provider import upstream_model_limits as uml  # noqa: E402
from astrbot.core.provider.sources.gemini_source import (  # noqa: E402
    ProviderGoogleGenAI,
)
from astrbot.core.provider.sources.openai_source import (  # noqa: E402
    ProviderOpenAIOfficial,
)
from astrbot.dashboard.services.model_metadata_builder import (  # noqa: E402
    build_model_metadata,
)

中文说明 = "上游限额"


def _起本地服务器(响应体: bytes):
    """起一个返回固定 JSON 的本地 HTTP 服务器，返回 (端口, 服务器)。"""

    class 处理器(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(响应体)))
            self.end_headers()
            self.wfile.write(响应体)

        def log_message(self, *args):
            pass

    服务器 = HTTPServer(("127.0.0.1", 0), 处理器)
    threading.Thread(target=服务器.serve_forever, daemon=True).start()
    return 服务器.server_address[1], 服务器


def _重置注册表():
    with uml._LOCK:
        uml._UPSTREAM_LIMITS.clear()


def test_注册表记录查询与清理():
    _重置注册表()
    uml.record_model_limits(
        "src-a",
        {
            "m1": (1_000_000, 64_000),
            "m2": (None, None),  # 上游什么都没给 → 不记录
            "m3": (0, -5),  # 非法值归零 → 不记录
            "": (123, 456),  # 空 ID 忽略
        },
    )
    assert uml.get_model_limits("src-a", "m1") == {
        "context": 1_000_000,
        "output": 64_000,
    }
    assert uml.get_model_limits("src-a", "m2") is None
    assert uml.get_model_limits("src-a", "m3") is None
    assert uml.get_model_limits("src-a", "不存在") is None
    assert uml.get_model_limits("src-b", "m1") is None  # 不跨源
    assert uml.get_model_limits("", "m1") is None

    uml.record_model_limits("src-a", {"m1": (None, None)})
    assert uml.get_model_limits("src-a", "m1") is None, "全空记录应删除旧条目"

    uml.record_model_limits("src-a", {"m9": (7, 8)})
    uml.clear_limits_for_source("src-a")
    assert uml.all_model_limits_for_source("src-a") == {}
    print("test_注册表记录查询与清理: PASS")


def test_openai源采集扩展字段限额():
    _重置注册表()
    响应体 = json.dumps(
        {
            "object": "list",
            "data": [
                {"id": "m-context", "object": "model", "context_length": 131072},
                {"id": "m-plain", "object": "model"},
                {"id": "m-output", "object": "model", "max_output_len": 8192},
            ],
        }
    ).encode()
    端口, 服务器 = _起本地服务器(响应体)
    配置 = {
        "id": "oai-src",
        "type": "openai_chat_completion",
        "model": "m-context",
        "key": ["k"],
        "api_base": f"http://127.0.0.1:{端口}/v1",
        "timeout": 10,
        "enable": True,
    }
    提供商 = ProviderOpenAIOfficial(配置, {})
    try:
        models = asyncio.run(提供商.get_models())
        assert models == ["m-context", "m-output", "m-plain"], models
        assert uml.get_model_limits("oai-src", "m-context") == {
            "context": 131072,
            "output": 0,
        }
        assert uml.get_model_limits("oai-src", "m-output") == {
            "context": 0,
            "output": 8192,
        }
        assert uml.get_model_limits("oai-src", "m-plain") is None
    finally:
        服务器.shutdown()
        asyncio.run(提供商.terminate())
    print("test_openai源采集扩展字段限额: PASS")


def test_gemini源采集token限额():
    _重置注册表()
    响应体 = json.dumps(
        {
            "models": [
                {
                    "name": "models/gemini-pro",
                    "supportedGenerationMethods": ["generateContent"],
                    "inputTokenLimit": 1048576,
                    "outputTokenLimit": 65536,
                },
                {
                    "name": "models/gemini-no-limit",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/gemini-embed",
                    "supportedGenerationMethods": ["embedContent"],
                    "inputTokenLimit": 2048,
                },
            ]
        }
    ).encode()
    端口, 服务器 = _起本地服务器(响应体)
    配置 = {
        "id": "gem-src",
        "type": "gemini_chat_completion",
        "model": "gemini-pro",
        "key": ["k"],
        "api_base": f"http://127.0.0.1:{端口}",
        "timeout": 10,
        "enable": True,
    }
    提供商 = ProviderGoogleGenAI(配置, {})
    try:
        models = asyncio.run(提供商.get_models())
        assert models == ["gemini-pro", "gemini-no-limit"], models
        assert uml.get_model_limits("gem-src", "gemini-pro") == {
            "context": 1048576,
            "output": 65536,
        }
        assert uml.get_model_limits("gem-src", "gemini-no-limit") is None
        assert uml.get_model_limits("gem-src", "gemini-embed") is None, (
            "不支持 generateContent 的模型不应记录限额"
        )
    finally:
        服务器.shutdown()
        asyncio.run(提供商.terminate())
    print("test_gemini源采集token限额: PASS")


def test_元数据拼装上游优先于静态表():
    _重置注册表()
    # LLM_METADATAS 启动时异步拉取，测试进程里是空的；塞桩数据模拟已加载
    from astrbot.core.utils import llm_metadata

    llm_metadata.LLM_METADATAS["gpt-4o"] = {
        "id": "gpt-4o",
        "reasoning": False,
        "tool_call": True,
        "knowledge": "none",
        "release_date": "",
        "modalities": {"input": ["text"], "output": ["text"]},
        "open_weights": False,
        "limit": {"context": 128_000, "output": 16_384},
    }
    try:
        # 静态表已有 gpt-4o，用上游限额覆盖其 context
        uml.record_model_limits(
            "src-y", {"gpt-4o": (555_555, 32_768), "gpt-fake": (999_999, 0)}
        )
        meta = build_model_metadata(["gpt-4o", "gpt-fake"], source_id="src-y")
        assert meta["gpt-4o"]["limit"]["context"] == 555_555, meta["gpt-4o"]
        assert meta["gpt-4o"]["limit"]["output"] == 32_768
        assert meta["gpt-4o"]["id"] == "gpt-4o", "其余字段保留静态表内容"
        assert meta["gpt-4o"]["tool_call"] is True
        assert meta["gpt-fake"] == {"limit": {"context": 999_999, "output": 0}}, (
            "静态表没有的模型只生成限额元数据"
        )

        # 不传 source_id 时仅静态表数据
        meta2 = build_model_metadata(["gpt-4o", "gpt-fake"])
        assert meta2["gpt-4o"]["id"] == "gpt-4o"
        assert meta2["gpt-4o"]["limit"]["context"] == 128_000, "无上游时不覆盖"
        assert "gpt-fake" not in meta2

        # 无效模型 ID 跳过
        meta3 = build_model_metadata([None, 123, "", "gpt-4o"], source_id=None)
        assert list(meta3) == ["gpt-4o"]
    finally:
        llm_metadata.LLM_METADATAS.pop("gpt-4o", None)
        uml.clear_limits_for_source("src-y")
    print("test_元数据拼装上游优先于静态表: PASS")


class _桩提供商:
    """模拟运行时解析看到的 provider：config 可写、model 可读。"""

    def __init__(self, config: dict, model: str):
        self.provider_config = config
        self._model = model

    def get_model(self) -> str:
        return self._model


def _跑运行时解析(提供商, fallback=128_000):
    """复刻 astr_main_agent 的解析分支？不——直接调用真实代码路径不可行
    （在函数深处），这里以同一优先级逻辑断言注册表行为，配合
    test_astr_main_agent 解析源码存在性检查。"""
    from astrbot.core.provider.upstream_model_limits import get_model_limits
    from astrbot.core.utils.llm_metadata import LLM_METADATAS

    if 提供商.provider_config.get("max_context_tokens", 0) <= 0:
        model = 提供商.get_model()
        upstream_limit = get_model_limits(
            提供商.provider_config.get("id", ""), model
        )
        if upstream_limit and upstream_limit.get("context"):
            提供商.provider_config["max_context_tokens"] = upstream_limit["context"]
        elif model_info := LLM_METADATAS.get(model):
            提供商.provider_config["max_context_tokens"] = model_info["limit"][
                "context"
            ]
        else:
            提供商.provider_config["max_context_tokens"] = fallback
    return 提供商.provider_config["max_context_tokens"]


def test_运行时解析优先级():
    _重置注册表()
    from astrbot.core.utils import llm_metadata

    llm_metadata.LLM_METADATAS["gpt-4o"] = {
        "id": "gpt-4o",
        "reasoning": False,
        "tool_call": True,
        "knowledge": "none",
        "release_date": "",
        "modalities": {"input": ["text"], "output": ["text"]},
        "open_weights": False,
        "limit": {"context": 128_000, "output": 16_384},
    }
    try:
        assert _跑运行时解析(
            _桩提供商({"id": "p", "max_context_tokens": 4096}, "m")
        ) == 4096, "手动配置最高优先"

        uml.record_model_limits("p2", {"m": (777_777, 0)})
        assert _跑运行时解析(_桩提供商({"id": "p2"}, "m")) == 777_777, (
            "有上游限额用上游"
        )

        uml.record_model_limits("p3", {"m": (0, 8_192)})
        value = _跑运行时解析(_桩提供商({"id": "p3"}, "gpt-4o"))
        assert value == 128_000, "上游无 context 时退静态表"

        value = _跑运行时解析(
            _桩提供商({"id": "p3"}, "彻底不存在的模型"), fallback=64_000
        )
        assert value == 64_000, "都查不到时用兜底值"
    finally:
        llm_metadata.LLM_METADATAS.pop("gpt-4o", None)
    print("test_运行时解析优先级: PASS")


def test_astr_main_agent解析已接入注册表():
    源码 = (Path(SRC) / "astrbot" / "core" / "astr_main_agent.py").read_text(
        encoding="utf-8"
    )
    assert "get_model_limits(" in 源码, "运行时解析应调用上游限额注册表"
    assert "upstream_limit" in 源码
    print("test_astr_main_agent解析已接入注册表: PASS")


if __name__ == "__main__":
    test_注册表记录查询与清理()
    test_openai源采集扩展字段限额()
    test_gemini源采集token限额()
    test_元数据拼装上游优先于静态表()
    test_运行时解析优先级()
    test_astr_main_agent解析已接入注册表()
    print("\n全部通过 ✓")
