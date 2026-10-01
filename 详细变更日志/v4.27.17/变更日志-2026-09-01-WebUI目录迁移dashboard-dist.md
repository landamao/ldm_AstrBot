# [2026-09-01] WebUI 目录迁移：部署/运行第一优先改为项目根 dashboard/dist

> 本文件为独立变更日志，记录 2026-09-01 WebUI 目录解析架构调整：
> WebUI 本质是源码的一部分，随源码树走，不再以 data/dist 为第一优先。
> 解决「启动参数指定不同 data 目录导致 WebUI 直接用不了」的结构性问题。

---

## 问题现象

- WebUI 部署目标与运行时服务目录写死为 `data/dist`，而 data 目录位置由
  `LDMBOT_DATA_DIR` / `--data-dir` 启动参数决定——换了 data 目录启动，
  WebUI 就直接用不了；
- 目录解析逻辑分散在 4 处（main.py / server.py / io.py / dashboard_assets.py），
  各自维护一套「data/dist 优先 → 随包 → 回退」的顺序，行为不一致；
- 架构上 data/ 是运行时数据目录，把 WebUI 产物（源码的一部分）放进去
  本身就违背「源码与数据分离」。

## 根因

WebUI 被当作「运行时数据」对待，而不是「源码的一部分」。
所有解析/部署/回退逻辑都围绕 data/dist 构建。

## 修复内容

### 1. 唯一权威解析器（新增 `get_project_dist_path` + 重写 `resolve_dashboard_dist`）

`astrbot/core/dashboard_assets.py`，解析优先级：

1. 显式 `--webui-dir` / `LDMBOT_WEBUI_DIR`（存在即用）
2. **项目根 `dashboard/dist`**（源码内置，版本不匹配仍用但警告）
3. 随包 `astrbot/dashboard/dist`（wheel 安装场景）
4. `data/dist`（**降为历史遗留回退**，仅旧部署兜底）

main.py `check_dashboard_files()` / `astrbot/dashboard/server.py` 构造 /
`io.py:get_dashboard_version()` 全部删掉各自本地解析逻辑，统一委托到该解析器。
全无可用 dist 时返回 None（不再返回不存在的 data/dist 路径假装能用）。

> **2026-09-02 补修——二次解析语义冲突（实踩）**：main.py 曾把
> `check_dashboard_files()` 的**解析结果**赋给 `core_lifecycle.webui_dir`，
> initial_loader 传给 AstrBotDashboard 后，server.py 再次调
> `resolve_dashboard_dist(webui_dir)`——解析结果非 None 被误判为
> 「用户显式指定的 --webui-dir」，版本不匹配时打出错误的
> 「显式指定的 WebUI 目录…」警告（用户实际从未指定）。
> 修复：`main.py` 只回传**原始启动参数** `webui_dir_arg`，解析一律由
> server.py 的 `resolve_dashboard_dist` 按完整优先级做。

### 2. 更新器部署目标迁移

`astrbot/core/updator.py:_应用webui()`：WebUI 覆盖目标由 `data/dist`
改为项目根 `dashboard/dist`（显式 --webui-dir 仍最优先）。
data 整目录依旧受保护、绝不触碰——data 里的内容可能是用户自建的，
不清理、不回退、不误删。

### 3. 回滚备份/恢复对齐

`astrbot/core/utils/update_rollback.py`（纯标准库，不得 import astrbot 包）：
新增 `_实际生效webui目录()`，按「显式 → dashboard/dist → data/dist」同一优先级
解析备份与恢复目标，与运行时行为一致。

### 4. 发版/打包链路（此前已落地，本次实测确认）

- `同步源码.sh` 两段式：排除 `/data` 与 `/dashboard` 整目录后，
  第二段单独把 `~/AstrBot/dashboard/dist/` 补进中间树；
- zip 内 WebUI 位于 `ldmbot/dashboard/dist/`（实测 239 文件、无 data/dist、
  无 dashboard 源码）；GitHub 仓库同步预演确认旧 data/dist 移除、新路径进 git；
- `verify_release.py` / `1.sh` / `ldmbot_install.sh` 均按 dashboard/dist 校验与安装；
- `.gitignore` 最小化，`git check-ignore` 确认 dashboard/dist 全部可进 git。

### 5. 日志文案

版本不匹配/缺失的警告统一中文，建议为「重新构建 dashboard 并部署到
dashboard/dist，或重新安装 ldm（安装包自带匹配版本的 WebUI）」；
不引用任何个人环境脚本路径。

## 修改文件清单

1. `astrbot/core/dashboard_assets.py` — 新增 `get_project_dist_path()`；重写
   `resolve_dashboard_dist()`；`get_dashboard_version()` 委托解析器
2. `main.py` — `check_dashboard_files()` 委托解析器；删死导入；--help 文案
3. `astrbot/dashboard/server.py` — 构造函数解析段委托解析器；清理死导入
4. `astrbot/core/utils/io.py` — `get_dashboard_version()` 委托解析器
5. `astrbot/core/updator.py` — `_应用webui()` 目标改 dashboard/dist；类与函数
   docstring 同步
6. `astrbot/core/utils/update_rollback.py` — 备份/恢复同优先级解析
7. `astrbot/dashboard/services/update_service.py` — docstring 文案
8. `astrbot/cli/utils/basic.py` — bundled 判断含项目根 dist；提示文案

纯后端改动，无需重新构建前端。

## 验证

- 8 个文件 py_compile 全过；
- 8 场景功能测试全过：dashboard/dist 优先于 data/dist / 无 dashboard/dist 回退
  data/dist / 版本不匹配仍优先+警告 / --webui-dir 绝对优先 / 全无→None /
  回滚模块三优先级与运行时一致；
- 实机解析：`get_project_dist_path()` = `/home/ldm/ldmbot_code/dashboard/dist`，
  回滚模块解析结果一致；
- `pytest tests/`：51 通过；3 个失败均为既有问题（chat_service 的
  `except BaseException` 为停止/打断功能既有断言失败、event_stopped_notify
  缺 pytest-asyncio），与本次改动文件无交集；
- 发版链路实测：同步源码.sh 中间树含 dashboard/dist 239 文件、无 data/ 目录；
  测试 zip 内 `dashboard/dist/` 239 条、`data/dist/` 0 条、`dashboard/src/` 0 条；
  verify_release.py 版本/污染/入口检查全过；rsync 预演 github 仓
  删旧 data/dist 239 文件、增 dashboard/dist 239 文件；git check-ignore
  确认 dist 全部可提交。

## 部署 / 测试注意

- 源码由用户手动同步到运行目录后重启 ldm 生效；
- 升级影响面：
  - 全新安装：WebUI 直接落 dashboard/dist，运行时直接命中；
  - 老部署（WebUI 在 data/dist）：运行时按第 4 级历史遗留回退继续可用并打
    日志提醒；同步源码后 dashboard/dist 到位即自动切换，无需手工迁移；
  - data/dist 一律**不清理**——data 里的内容可能是用户自建的，更新器不碰；
- 回归验证点：
  1. 默认启动：日志出现「WebUI 目录: …/dashboard/dist」；
  2. `--webui-dir` 指定目录：仍以指定目录为准；
  3. 换 `--data-dir` 启动：WebUI 不再失效（这是本次修复的核心场景）；
  4. 面板「更新面板」：WebUI 覆盖到 dashboard/dist；
  5. 更新前备份 zip 含 dist/（实际生效目录），--rollback 可正常恢复。

## 回滚

还原上述 8 个文件到改动前备份即可（发版打包流程的改动前备份 zip）。
