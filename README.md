# 🚀 **ldm** · 个人魔改版 AstrBot

> **长期自用 · 高度定制 · 拒绝官方覆盖**  
> 基于官方 [AstrBot](https://github.com/AstrBotDevs/AstrBot) v4.26.5 二次修改  
> **对照基线**：本地 `/home/ldm/AstrBot` ↔ 官方 `/home/ldm/__tmp/AstrBot`

---

## 📥 一键安装（推荐）

在终端执行以下命令，自动下载并运行安装脚本：

```bash
curl -fsSL -o ldm_AstrBot_install.sh https://github.com/landamao/ldm_AstrBot/releases/latest/download/ldm_AstrBot_install.sh && chmod +x ldm_AstrBot_install.sh && ./ldm_AstrBot_install.sh
```

脚本会自动解压项目、配置依赖并启动。首次运行后再次执行可**直接启动**或**重建**。

> 如需**手动安装**（分步控制），请跳转到文档末尾的 [📄 手动安装教程](#-手动安装教程)。

---


## ✨ 突出特性一览

- ⚙️ **旧版 `/help` 回归** – 列表式指令预览，隐藏冗余命令  
- 🔇 **`/llm` 会话级开关** – 精准控制群聊/私聊/全局 LLM 开关  
- 📦 **`/plugin` 完全体** – 列表/启用/禁用/安装 + **重启/更新插件**（演示模式禁用）  
- 🛡️ **自动更新彻底封锁** – 核心 / WebUI / pip 全部 no‑op，固守当前版本  
- 🎨 **自定义 WebUI 面板** – 品牌 Logo + 侧栏重排 + 模型链拖拽 + 固定默认密码  
- 🌐 **全链路中文** – 启动日志、帮助文本、错误提示、仪表盘 UI 全面中文化  
- 🔄 **Agent 消息不发回** – 默认不再把确认串回灌 LLM，避免循环  
- 🧩 **兼容流式降级** – 对异常非流式响应自动切换流式并聚合，零影响正常上游  
- 🖥️ **高饱和控制台** – 后端 ANSI + 前端 CSS 双重亮色，一目了然  
- 🔌 **端口友好** – TTY 下端口被占可交互更换，立即生效  
- 💾 **备份恢复** – 可恢复不同版本的备份数据。

---

## 🔍 核心魔改详解

### ⚙️ 1. 旧版 `/help` 体验

- 从指令注册表**动态生成列表**（改名/禁用后仍准确）  
- 隐藏集合：`set` / `unset` / `websearch`（与官方不同）  
- 品牌头：`ldm v{VERSION}(WebUI: …)`  
- 分区「内置指令:」 + 中文空列表提示  

```
ldm v4.26.5(WebUI: v4.26.5)
内置指令:
/help - 查看帮助
/llm - 开关会话 LLM
...
```

---

### 🔇 2. 重写 `/llm` – 会话级 LLM 开关

**文件**：`commands/llm.py` + `_conf_schema.json` + `main.py` 注册拦截

- `/llm` – 切换当前会话（群/私聊）  
- `/llm <id> -q` – 开关指定群  
- `/llm <id> -s` – 开关指定私聊  
- `/llm list [-s|-a]` – 列出已关闭会话  
- `/llm all [on|off]` – 全局禁用/启用  
- `/llm help` – 中文帮助  

**真正拦截**：通过 `@filter.on_llm_request()` 拒绝被关闭会话的 LLM 请求，日志明确记录。

---

### 📦 3. 增强 `/plugin` – 完整插件管理（含 restart/update）

**文件**：`commands/plugin.py` + `main.py` 注册组

| 子命令 | 功能 | 增强点 |
|--------|------|--------|
| `ls` | 已加载插件列表（含启用状态） | 提示 `restart`/`update` 用法 |
| `on <插件名>` | 启用插件（ADMIN） | |
| `off <插件名>` | 禁用插件（ADMIN） | |
| `get <仓库地址>` | 安装插件（ADMIN） | |
| `help <插件名>` | 插件作者/版本 + 注册指令列表 | |
| `restart <插件名>` | **重启插件**（ADMIN） | 调用 `PluginManager.reload()`，演示模式拒绝 |
| `update <插件名>` | **更新插件**（ADMIN） | 调用 `PluginManager.update_plugin()`，演示模式拒绝 |

> 注意：此处的更新/重启是**已安装插件**，与核心/WebUI 自动更新无关。

---

### 🛡️ 4. 自动更新封锁（全链路）

| 入口 | 行为 |
|------|------|
| 启动检查 WebUI | **不再** `download_dashboard` / 覆盖 `data/dist`；有 `index.html` 则警告并使用现有面板 |
| 下载/解压函数 | `download_dashboard` / `extract_dashboard` 仅打警告，不联网不写盘 |
| 核心更新器 | `check_update` / `get_releases` / `update` / `download_update_package` / `apply_update_package` **全部 no‑op** |
| 仪表盘更新服务 | `update_project` blocked；`call_download_dashboard` / `call_pip_install` no‑op |
| 管理员指令 | `/dashboard_update` 拒绝自动下载，提示手动构建 |

---

### 🎨 5. WebUI 定制 & 登录策略

#### 🏷️ 品牌替换
- 页面标题 / meta / 图标 / Logo / 顶栏 / 聊天侧栏 → **ldm**  
- i18n 值中的 `AstrBot` → `ldm`（key 不变）

#### 🔐 登录与密码（简化）
| 项目 | 官方 | ldm |
|------|------|-----|
| 首登 Setup | 可能要求 | **永远 False** |
| 强制改密 | 登录后弹窗 | **清除 flags，不弹** |
| 密码复杂度 | 长度+大小写+数字 | **仅非空** |
| 随机初始密码 | 强随机 | **固定默认密码** |
| 默认用户名 | `astrbot` | `ldm` |

#### 🧩 侧栏与配置 UI
- `sidebarItem.ts`：**插件、控制台、模型、配置**前移，其余放入「更多」  
- 模型配置：主模型 + 回退模型链**可拖拽**（`ChatModelChainSelector.vue`）  

#### 🎨 控制台高饱和配色
- **后端 ANSI**：DEBUG=亮青，INFO=亮绿，WARNING=亮黄，ERROR=亮红  
- **前端 CSS**：对应 `#39C5BB` / `#00FFFF` / `#FFFF00` / `#FF0000` / `#00FF00` / 白色  
（官方为低饱和灰蓝/暗黄/暗红）

#### 📦 部署约定（重要！）
```bash
cd ~/AstrBot/dashboard
pnpm build
cp -r dist/* ../data/dist/
echo -n "v4.26.5" > ../data/dist/assets/version   # 务必保留 version 文件
```
> **服务路径**：`data/dist/`（不是 `dashboard/dist/`）  
> **修改后需手动重启**（本项目禁用自动重启）

---

### 🔄 6. Agent 发消息不再二次唤醒

**文件**：`message_tools.py`  
- 工具 `SendMessageToUserTool` 新增参数 `receive_result`（默认 `False`）  
- `False` → 返回 `None` → **Agent 循环结束**，不回灌确认串  
- `True` → 返回 `Message sent to session …`，继续对话（用于需要反馈的场景）  

配套单元测试已同步调整。

---

### 🧩 7. 模型提供商强制流式兼容

**文件**：`openai_source.py`  
- 上游有时忽略 `stream=False` 返回流式，或返回非法类型（如空响应 / 字符串 SSE）  
- 本地策略：  
  - 可配置 `force_stream_on_query` 强制开启流式  
  - 否则先尝试非流式，检测到异常则**自动降级**为流式并聚合为 `ChatCompletion`  
- 对正常上游零影响  
- 请求前打印中文模型日志  

OpenAI 兼容继承类（Groq / xAI / Zhipu 等）自动受益；Gemini / Anthropic 路径独立增加模型日志。

---

### 🔌 8. 端口占用交互

**文件**：`dashboard/server.py`  
- TTY 下端口被占用时：询问是否换端口 → 校验可用 → 写入 `dashboard.port` → 提示重启并 `sys.exit(0)`  
（不自动拉起新进程，遵守「禁止自动重启」约定）

---

## 📂 文件差异速查

### ✅ 仅本地存在（新增/替换）

```
astrbot/builtin_stars/builtin_commands/commands/
├── llm.py              # 会话级 LLM 开关
├── plugin.py           # 完整插件管理（含 restart/update）
├── persona.py          # /persona
├── t2i.py              # /t2i
├── tts.py              # /tts
└── alter_cmd.py        # /alter_cmd
astrbot/builtin_stars/builtin_commands/_conf_schema.json   # 中文键
dashboard/src/components/shared/ChatModelChainSelector.vue  # 模型链拖拽
dashboard/src/assets/images/ldm-logo.svg                   # 品牌图标
README_LDM.md                                             # 本文档
```

### ❌ 仅官方存在（本地删除/未使用）

```
astrbot/builtin_stars/builtin_commands/commands/name.py    # /name 被替换为管理指令集强化
```

> 其他修改以 **品牌中文化**、**防更新**、**兼容性补丁** 为主，散见于各核心模块（CLI、核心生命周期、仪表盘服务、前端等）。

---

## 🔧 其他改动区域（简要）

- **CLI**：`cmd_run` / `cmd_conf` / `cmd_init` / `cmd_plug` 等 – 品牌显示及中文提示  
- **核心**：`config/astrbot_config.py`、`cron/manager.py`、`db/migration`、`persona_mgr`、`platform/*`、`provider/*`、`star/*`、`knowledge_base` 等 – 日志中文化及配置兼容  
- **仪表盘后端**：`plugin_service.py`、`stat_service.py` 等 – 与前端品牌联动  
- **前端组件**：`ExtensionCard.vue`、`SessionManagementPage.vue`、TOTP 对话框等 – 登录流程简化  

完整差异可执行（排除无关目录）：
```bash
diff -rq \
  --exclude='.git' --exclude='.venv' --exclude='node_modules' \
  --exclude='__pycache__' --exclude='dist' --exclude='data' \
  /home/ldm/__tmp/AstrBot /home/ldm/AstrBot
```

---

## 🚀 快速启动（开发/已有源码）

如果你已通过其他方式获取源码（如克隆仓库或手动解压），可直接进入项目目录：

```bash
cd ldmbot          # 或你的项目目录
uv sync            # 若使用 uv
uv run main.py
```

若使用传统 `pip`：
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## 📌 自用约定

1. **🔒 不自动更新** – 核心、WebUI、pip 全部锁死  
2. **📁 WebUI 只认 `data/dist`** – 构建后必须部署并保留 `assets/version`  
3. **📦 迁移** – 整目录复制（排除 `.venv` / `node_modules` / 缓存 / 日志）  
4. **🖊️ 显示名可改，逻辑标识慎改** – 不擅改 `AstrBotConfig`、事件名、配置键、i18n key  
5. **🔄 重启需手动** – 助手/脚本不自动 `restart`  
6. **🧪 合入上游时** – 在干净官方树重放补丁点，再 `diff` 校验  

---

## 📜 许可 & 文档维护

- 上游遵循 **AGPL v3**（见 `LICENSE` / `EULA.md`）  
- 本说明仅描述**个人魔改行为**，非官方分支  
- **对照目录**：本地 `/home/ldm/AstrBot` ↔ 官方 `/home/ldm/__tmp/AstrBot`  
- 刷新差异时排除：`.git`、`.venv`、`node_modules`、`__pycache__`、`dist`、`data`  
- 若本文与代码不一致，以 `~/AstrBot` 源码和 `data/dist` 实际部署为准  

---

## 📄 手动安装教程

> 以下步骤基于 `ldm_AstrBot_install.sh` 脚本的逻辑，适合希望完全掌控每个环节的用户。

### 1. 下载安装脚本
```bash
wget https://github.com/landamao/ldm_AstrBot/releases/latest/download/ldm_AstrBot_install.sh
# 或 curl
curl -LO https://github.com/landamao/ldm_AstrBot/releases/latest/download/ldm_AstrBot_install.sh
```

### 2. 赋予执行权限
```bash
chmod +x ldm_AstrBot_install.sh
```

### 3. 运行脚本（自动解压 + 环境配置）
```bash
./ldm_AstrBot_install.sh
```

脚本会自动执行以下操作：
- 解压内嵌的 `ldmbot.zip` 到当前目录
- 检测本地代理（端口 `7890` 或 `7897`），询问是否启用
- 检查 `uv` 包管理器，若缺失则自动安装
- 优先使用 `uv sync` 安装依赖并启动
- 若 `uv` 失败，回退到 `pip`：自动创建 Python 3.12 虚拟环境，安装 `requirements.txt` 并启动

### 4. （可选）手动解压部署与启动
若只想解压不自动启动，可将脚本后缀改为 `.zip` 后解压：
```bash
cp ldm_AstrBot_install.sh ldmbot.zip
unzip ldmbot.zip -d ldmbot
cd ldmbot
```
- **部署**
使用uv（推荐）
  ```bash
  uv sync  # 同步依赖
  ```
  ```bash
  uv run main.py  # 启动 （后续启动）
  ```
- **使用 pip**：
  ```bash
  python3.12 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  python main.py
  ```

### 5. 后续维护
- 再次执行同一安装脚本，会检测到已存在 `ldmbot` 目录，提供 **直接启动** / **删除重建** / **重命名重建** 选项。
- 如需更新，建议备份数据后删除旧目录再运行脚本（或重命名旧目录）。

---

**🌟 享受你的 ldm 之旅！**