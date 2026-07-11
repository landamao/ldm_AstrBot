# ldm_AstrBot

基于开源项目 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的二次修改与优化版本（亦称 ldmbot / LDMBOT）。

本仓库用于公开分发源码与安装脚本，方便自行部署与二次开发。

## 与上游的关系

- 上游项目：AstrBot（AGPL-3.0）
- 本仓库：在 AstrBot 基础上的个人/社区优化发行版
- 许可证：继承上游 **AGPL-3.0-or-later**（见 `LICENSE`）
- 使用 / 分发 / 修改网络服务时，请遵守 AGPL 义务（含对应源码提供）

## 功能概览

与 AstrBot 主体能力一致，包括但不限于：

- 多平台接入（QQ / 微信生态 / 飞书 / 钉钉 / Telegram / Discord / Slack 等）
- LLM 对话、Agent、MCP、插件市场、知识库、人格、WebUI
- 内置管理面板静态资源（`data/dist`），克隆后可直接启动

## 环境要求

- Python 3.12+
- 推荐安装 [uv](https://docs.astral.sh/uv/)
- Linux / macOS / Windows（WSL 更佳）

## 快速开始

### 方式一：克隆源码 + 安装脚本

```bash
git clone https://github.com/landamao/ldm_AstrBot.git
cd ldm_AstrBot
bash scripts/install_or_run.sh
```

### 方式二：uv（推荐）

```bash
git clone https://github.com/landamao/ldm_AstrBot.git
cd ldm_AstrBot
uv sync
uv run main.py
```

### 方式三：venv + pip

```bash
git clone https://github.com/landamao/ldm_AstrBot.git
cd ldm_AstrBot
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
pip install -e .
python main.py
```

默认会启动 API / WebUI（常见端口以实际日志为准，AstrBot 默认可为 `6185`）。

重置面板密码示例：

```bash
python main.py --reset-password
```

## 代理说明

国内环境若拉取依赖困难，可先设置代理，例如：

```bash
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
```

`scripts/install_or_run.sh` 会自动探测本机 `7890` / `7897` 代理端口并询问是否启用。

## 目录结构（简要）

```
ldm_AstrBot/
├── main.py                 # 入口
├── runtime_bootstrap.py
├── pyproject.toml
├── requirements.txt
├── LICENSE                 # AGPL-3.0
├── astrbot/                # 核心代码
├── data/
│   ├── dist/               # 预置 WebUI 静态资源
│   └── t2i_templates/      # 文生图模板
└── scripts/
    ├── install_or_run.sh   # 一键安装/启动
    └── hatch_build.py
```

运行后会在数据目录生成配置、插件、数据库等（默认位于项目 `data/` 或用户目录下的数据路径，以版本逻辑为准）。**请勿把含密钥的 `data/config`、`.env` 提交到 Git。**

## 配置与安全

- 首次启动后请立刻修改 Dashboard 密码
- API Key / Token / Cookie 只放在本地配置或环境变量中
- 开源仓库不包含任何个人密钥与运行时数据库

## 致谢

- 上游：[AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot)
- 以及 AstrBot 社区插件与贡献者

## 免责声明

本项目按「原样」提供，作者不对使用本软件造成的任何损失负责。请遵守各即时通讯平台与模型服务商的服务条款与当地法律法规。

## 许可证

AGPL-3.0-or-later — 详见 [LICENSE](./LICENSE)。
