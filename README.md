# ldm · AstrBot 个人魔改版

基于官方 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的二次修改，面向**长期自用、可定制、不被官方更新覆盖**的场景。

品牌与日志显示为 **ldm**。当前主力代码目录：`~/AstrBot`。

---

## 和官方 AstrBot 比，改了什么？

### 一图对比

| | 官方 AstrBot | 本魔改（ldm） |
|--|--------------|---------------|
| **品牌** | AstrBot | **ldm**（WebUI / 日志 / CLI / `/help`） |
| **`/help`** | 官方帮助样式 | **保留旧版 AstrBot 风格**：实时列出已启用内置指令 |
| **`/llm`** | 简单开关或无完整会话管理 | **重写**：群/私聊/全局开关 + list + help + 请求拦截 |
| **语言** | 启动/日志偏英文 | **启动主路径与界面中文化** |
| **WebUI** | 可自动下载官方面板 | **自定义面板 + 禁止自动覆盖** |
| **自动更新** | 可更新核心 / WebUI / pip | **默认全禁用** |
| **登录** | 随机强密码 + 强制改密 | 自用凭据 + **去掉强制改密** |
| **Agent 发消息** | 发送后常再次唤醒 LLM | **默认发送即结束**，少一句废话 |
| **模型兼容** | 标准 OpenAI 路径 | **强制流式兼容**（部分上游只认 stream） |
| **配置 UI** | 默认模型 / 回退模型分开 | **可拖拽对话模型链** |

---

## 突出特性

### 1. 保留旧版 AstrBot 的 `/help` 体验

官方新版本帮助输出会随上游变化；本魔改**刻意保留旧版 AstrBot 的 help 风格**：

- 实时从指令注册表生成列表（改名/禁用后仍准确）
- 只展示已启用的内置顶级指令
- 输出品牌改为 `ldm v{版本}(WebUI: …)`
- 结构清晰：版本行 →「内置指令:」→ 指令清单

```text
ldm v4.26.5(WebUI: v4.26.5)
内置指令:
/help - 查看帮助
/llm - 开关会话 LLM
/provider - ...
...
```

### 2. 重写 `/llm`：会话级 LLM 开关

官方 `/llm` 能力有限。本版按「关闭 LLM」类插件思路**整指令重写**：

```text
/llm              → 切换当前会话（群聊/私聊）
/llm <id> -q      → 开关指定群
/llm <id> -s      → 开关指定私聊
/llm list [-s|-a] → 查看已关闭会话
/llm all [on|off] → 全局禁用/启用
/llm help         → 中文帮助
```

- 配置持久化（关闭的群 / 私聊 / 全局关闭）
- 通过 `on_llm_request` **真正拦截**被关闭会话的 LLM 请求
- 日志会提示：`全局 LLM 已关闭` / `群 xxx 的 LLM 功能已关闭`

### 3. 中文化

相对官方偏英文的启动与提示：

- 启动主路径日志中文：`ldm 版本`、`正在加载…`、`ldm 启动完成`、`WebUI 已就绪`
- 控制台 / WebUI 文案中文化
- 日志里打印所用模型：`正在请求 LLM，使用模型: xxx（提供商: yyy）`
- 内置指令说明、`/llm help` 等用户可见文案中文
- 逻辑标识（类名、API、配置键）保持英文，避免破坏兼容

### 4. WebUI 深度定制（相对官方最大差异之一）

**品牌**
- 顶栏 / 登录页 / Chat 侧栏显示 **ldm**
- 自定义 favicon / logo
- 页面标题：`ldm - 仪表盘`

**防覆盖（官方会自动下官方面板，这里关掉）**
- 禁用启动时自动下载 WebUI
- 禁用更新流程覆盖 `data/dist`
- 禁用 `/dashboard_update` 拉官方包
- 禁用 WebUI 触发的 pip 更新
- 禁用核心源码自动更新

**交互与配置**
- 去掉首次登录强制改密 / 自动弹改密框
- 侧栏顺序按自用频率调整（插件、控制台、模型、配置更靠前）
- 控制台日志高饱和配色（后端 ANSI + 前端 CSS 同步）
- 统一「对话模型链」：主模型 + 回退模型拖拽排序

**部署约定（和官方不一样，务必记住）**

```bash
cd ~/AstrBot/dashboard
pnpm build
cp -r dist/* ../data/dist/
# 必须保留 version，否则官方逻辑可能判定异常
echo -n "v4.26.5" > ../data/dist/assets/version
```

实际服务目录是 **`data/dist/`**，不是 `dashboard/dist/`。

### 5. Agent：发完消息默认不再二次唤醒

官方工具发送消息后，常把结果回灌 LLM，容易多一句。  
本版 `SendMessageToUser`：

- 默认 `receive_result=false` → 返回 `None` → **Agent 循环结束**
- 需要继续对话时再显式 `receive_result=true`

### 6. 模型提供商兼容

针对部分上游「`stream=false` 空响应 / 强行返回 stream」：

- OpenAI 兼容层自动聚合 stream
- 可按提供商开启 `force_stream_on_query`

### 7. 内置指令集更贴近旧版/自用习惯

相对官方当前指令集，本版额外保留/强化例如：

- `/llm`（重写）
- `/plugin` 插件管理组
- `/persona`、`/t2i`、`/tts`、`/alter_cmd` 等
- `/help` 旧版列表风格

---

## Linux 一键安装

下载 Release 自解压安装脚本并执行（内含完整源码 + WebUI 静态资源）：

```bash
curl -fsSL -o ldm_AstrBot_install.sh \
  https://github.com/landamao/ldm_AstrBot/releases/latest/download/ldm_AstrBot_install.sh \
  && chmod +x ldm_AstrBot_install.sh \
  && ./ldm_AstrBot_install.sh
```

非交互（默认同意提示 / 已存在目录时默认删除重建）：

```bash
curl -fsSL -o ldm_AstrBot_install.sh \
  https://github.com/landamao/ldm_AstrBot/releases/latest/download/ldm_AstrBot_install.sh \
  && chmod +x ldm_AstrBot_install.sh \
  && LDM_ASTRBOT_YES=1 ./ldm_AstrBot_install.sh
```

说明：
- 安装脚本为「脚本头 + 内嵌 zip」，**必须先下载到本地再执行**（不要用 `curl | bash`）
- 解压目录：`./ldm_AstrBot`
- 也可将 `.sh` 后缀改成 `.zip` 后手动解压安装
- Release 页：https://github.com/landamao/ldm_AstrBot/releases

## 快速启动（已有源码）

```bash
# 克隆仓库
git clone https://github.com/landamao/ldm_AstrBot.git
cd ldm_AstrBot

# 方式 A：仓库内脚本
bash scripts/install_or_run.sh

# 方式 B：uv（推荐）
uv sync
uv run main.py
```

修改后端或 WebUI 后请**手动重启**（本项目约定不自动重启服务）。

---

## 自用约定

1. **不自动更新覆盖** — 核心、WebUI、pip 默认都锁死  
2. **WebUI 只认 `data/dist`** — 构建后必须部署并保留 `assets/version`  
3. **迁移** — 优先整目录复制（排除 `.venv` / `node_modules` / 缓存 / 日志）  
4. **显示名可改，逻辑标识慎改** — 例如不乱改 `AstrBotConfig`、事件名、配置键  

---

## 许可

- 上游：https://github.com/AstrBotDevs/AstrBot  
- 许可证：遵循上游 AGPL v3 等条款（见 `LICENSE` / `EULA.md`）  
- 本说明描述的是**个人魔改行为**，不是官方分支

---

## 说明

本文档对标 **AstrBot 官方上游行为**，列出本机魔改点。  
若与代码不一致，以 `~/AstrBot` 源码和 `data/dist` 实际部署为准。
