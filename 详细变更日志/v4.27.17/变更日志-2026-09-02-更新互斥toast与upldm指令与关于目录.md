# 变更日志：更新互斥报错提示 + 删除单独更新面板 + /upldm 指令 + About 目录完善（2026-09-02）

## 背景

本轮包含四块改动：更新互斥的报错提示方式、删除无意义的「单独更新管理面板」功能、新增聊天侧 `/upldm` 一键更新指令、关于弹窗目录显示完善。

## 一、更新互斥改为报错 + 前端 toast

### 问题

更新进行中再触发更新请求时，后端返回 `status="warning"` + 已运行任务 id，前端静默转跟踪既有任务进度，用户无任何感知，容易误以为点了没反应反复点击。

### 根因

前端 toast 报错的链路没有接上：`api/updates.py` 的 `_service_error` 会把 `UpdateServiceError` 的中文 message 吞成 `"An internal error has occurred."`，前端拿不到可展示文案。

### 改动

- 后端 `astrbot/dashboard/services/update_service.py`：
  - 核心更新互斥占用时改为 `raise UpdateServiceError("已有更新任务正在进行中")`（不再返回 warning + 任务 id）
  - 上传压缩包应用互斥文案同步精简为 `"已有核心更新任务正在进行中"`
- 后端 `astrbot/dashboard/api/updates.py`：`_service_error` 将 `str(exc)` 原样透传（HTTP 200 + `status=error`），不再吞成英文内部错误文案——与「设置→WebUI 端口号不合法」报错同一链路
- 前端 `VerticalHeader.vue`：删除 `status === 'warning'` 静默跟踪分支；`status === 'error'` 时停掉进度/重启轮询 + `toastStore.add({ message, color: 'error' })` 弹红色粘滞 toast（带 X 可关）

## 二、删除「单独更新管理面板到最新版本」功能

### 决策

ldm 的 WebUI 随源码树走（核心更新时自动从同一更新包同步），不像官方那样运行时才下载面板，单独更新面板功能无意义，整体删干净。

### 删除范围

- 后端：
  - `astrbot/dashboard/api/updates.py`：`POST /api/v1/updates/dashboard` + legacy `POST /api/update/dashboard` 两条路由
  - `update_service.py`：`update_dashboard` 方法、`check_update` 的 `update_type == "dashboard"` 分支、构造函数 `download_dashboard_func`/`extract_dashboard_func` 死参数、`call_download_dashboard`/`call_extract_dashboard` 包装函数
  - `astrbot/dashboard/api/app.py`：UpdateService 注入参数与 import 清理
  - `astrbot/core/updator.py`：`apply_webui_only_from_package` 方法
  - `astrbot/core/utils/io.py`：`download_dashboard` 兼容入口（`extract_dashboard` 保留，上传包解压仍用）
  - `builtin_commands/main.py` + `commands/admin.py`：`/dashboard_update` 聊天指令
- 前端：
  - `VerticalHeader.vue`：「高级设置」折叠区块（唯一内容即面板更新 banner，toggle 一并删）、`updateDashboard` 函数、`dashboardHasNewVersion`/`dashboardCurrentVersion`/`updatingDashboardLoading`/`showAdvancedUpdateSettings` 状态、顶栏「WebUI 有新版本！」小字、更新按钮 chip 相关条件、`.dashboard-update-banner`/`.advanced-settings-toggle` CSS
  - `api/v1.ts`：`updatesApi.dashboard`
  - 生成 SDK：`updateDashboard` 函数 + `UpdateDashboard*` 类型
  - i18n `zh-CN/core/header.json`：`dashboardUpdate.*`、`version.dashboardHasNewVersion`

### 保留

`io.py::extract_dashboard`（上传包解压）、`updator.py::_应用webui`/`_定位包内webui_dist`（核心更新链路）、`statsApi.version()` 的 `dashboard_version` 字段（About 弹窗/版本一致性弹窗仍用）。

## 三、新增 `/upldm` 聊天指令（管理员）

### 功能

聊天侧一键更新 ldmbot 并自动重启，免开 WebUI。

### 链路

1. 发送「正在更新ldmbot...」
2. `AstrBotUpdator.check_update(force_refresh=True)` 检查（Release-only）；无更新 → 发「当前已是最新版本 vX.Y.Z，无需更新。」结束
3. 走 ldm 镜像源（后端硬编码 `normalize_ldm_mirror("ldm_mirror")`，与 WebUI 更新默认下载源一致）下载最新 Release 整包
4. `apply_update_package` 应用（内部含更新前自动备份 + WebUI 同步）
5. 全局 `pip_installer.install(requirements.txt)` 更新依赖（失败仅记日志不阻断）
6. 发送「更新成功 v旧 → v新，正在重启」
7. `_write_restart_record(event)` 写重启记录（供启动报告插件使用），再 `core_lifecycle.restart()` 重启

### 关键实现点

- **版本号显示错误修复**：`VERSION` 是进程启动时 import 进内存的常量，更新包覆盖磁盘后不变——曾出现「v4.27.15 → v4.27.15」。修复：直接读运行目录 `astrbot/__init__.py` 源文件，正则解析 `__version__`（主路径 `Path(__file__).parents[4]` 推算项目根，兜底 `astrbot.__file__`，再兜底 Release tag）。**注意 `importlib.import_module` 会命中 sys.modules 缓存返回旧值，此路不通**（首版修复即踩此坑）
- **下载源说明**：WebUI 更新弹窗的下载源选择存浏览器 localStorage，服务端感知不到；聊天指令侧固定走后端硬编码的 ldm 镜像源
- **重启链路**：重启记录写入逻辑从 `restart()` 提取为共用方法 `_write_restart_record()`，`/restart` 与 `/upldm` 共用；重启走 `core_lifecycle.restart()`（防 pipeline 自取消死锁的 shield 链路，端口正常释放）
- **PipInstaller 陷阱**：构造需要 `pip_install_arg` 参数，直接复用全局现成实例 `from astrbot.core import pip_installer`（与 WebUI 更新链路同配置）；`VERSION` 在 `astrbot.core.config.default` 不在 `astrbot.core`

## 四、关于 ldm 弹窗目录显示完善

- 「关于 ldm」弹窗 + `/about` 指令目录显示新增「插件目录」「插件数据目录」（`get_astrbot_plugin_path()` / `get_astrbot_plugin_data_path()`）
- 目录顺序：WebUI目录 → 数据目录 → 插件目录 → 插件数据目录 → 备份目录 → 版本回滚目录（数据目录紧跟 WebUI目录，其后都是 data/ 子目录）
- i18n 文案「WebUI 版本」「WebUI 目录」去掉中间空格（「WebUI版本」「WebUI目录」），与「/about」指令文案对齐

## 效果

- 更新互斥触发时用户能看到明确的红色 toast 提示「已有更新任务正在进行中」
- WebUI 更新入口收敛为核心更新一条链路，面板随核心更新自动同步
- 聊天侧管理员可 `/upldm` 一键更新+重启，消息反馈完整（更新中/无更新/成功版本对比/重启），启动报告插件照常发报告
- 关于弹窗目录信息更全（插件目录、插件数据目录），顺序符合「后面的都是 data/ 子目录」的认知

## 验证

- 全部改动文件 `py_compile` 通过
- 冒烟测试：互斥拒绝文案 3 场景、API 层 message 透传、`get_about_info` 新字段与目录顺序、`up_ldm` 无更新分支消息内容、重启记录真实写盘，全部通过
- `bash ~/构建部署.sh` 构建部署同步成功（vue-tsc 类型检查通过）

## 手动同步

前端已构建部署并同步运行目录，后端已同步源码。需要手动重启 ldm 后生效。
