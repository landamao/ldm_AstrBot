"""模型引用管理 API 集成测试:路由注册、鉴权、服务装配与请求体解析。"""

import jwt as pyjwt
from fastapi.testclient import TestClient
from test_model_usage_service import (
    _make_core_lifecycle,
    _sample_core_conf,
)

from astrbot.dashboard.api.app import create_dashboard_asgi_app

JWT_SECRET = "test-model-usage-secret"


def _auth_headers():
    token = pyjwt.encode({"username": "admin"}, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _make_app():
    providers = [
        {"id": "openai-main", "model": "gpt-4o", "enable": True},
        {"id": "tts-edge", "model": "edge-tts", "enable": True},
    ]
    core_conf = _sample_core_conf()
    lifecycle, reloaded, scheduler_reloaded = _make_core_lifecycle(
        core_conf, providers
    )
    lifecycle.astrbot_config = core_conf
    lifecycle.log_broker = None
    lifecycle.astrbot_updator = None
    app = create_dashboard_asgi_app(
        core_lifecycle=lifecycle,
        db=SimpleNamespaceDB(),
        jwt_secret=JWT_SECRET,
        static_folder=None,
    )
    return app, lifecycle, reloaded, scheduler_reloaded


class SimpleNamespaceDB:
    """create_dashboard_asgi_app 只存引用不调用，占位即可。"""


def test_未授权访问返回401():
    app, *_ = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/model-usages/scan")
    assert resp.status_code == 401


def test_scan_接口返回提供商与引用列表():
    app, *_ = _make_app()
    client = TestClient(app)
    resp = client.get("/api/v1/model-usages/scan", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    data = body["data"]
    provider_ids = [p["id"] for p in data["providers"]]
    assert "openai-main" in provider_ids
    key_paths = [u["key_path"] for u in data["usages"]]
    assert "provider_settings.default_provider_id" in key_paths
    assert "provider_tts_settings.provider_id" in key_paths


def test_apply_接口批量替换并触发重载():
    app, lifecycle, reloaded, scheduler_reloaded = _make_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/model-usages/apply",
        headers=_auth_headers(),
        json={
            "replacements": [
                {
                    "scope": "core",
                    "config_id": "default",
                    "key_path": "provider_tts_settings.provider_id",
                    "value_type": "single",
                    "new_value": ["openai-main"],
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["data"]["replaced"] == 1
    conf = lifecycle.astrbot_config_mgr.confs["default"]
    assert conf["provider_tts_settings"]["provider_id"] == "openai-main"
    assert "default" in scheduler_reloaded
    assert reloaded == []


def test_apply_请求体缺失replacements时报错():
    app, *_ = _make_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/model-usages/apply",
        headers=_auth_headers(),
        json={},
    )
    # ValueError 由全局异常处理转成 400 + 中文报错
    assert resp.status_code == 400
