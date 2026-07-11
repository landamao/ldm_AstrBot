# scripts

- `install_or_run.sh`：克隆本仓库后，一键安装依赖并启动（优先 uv，失败回退 venv+pip）
- `hatch_build.py`：打包时可选构建 WebUI（`ASTRBOT_BUILD_DASHBOARD=1 uv build`）
