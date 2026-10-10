# [2026-10-10] LDMBOT_DISABLE_UPDATE：一键禁用主程序更新与回滚

> 本文件为独立变更日志，记录禁更环境变量的判定设计、全部拦截点与有意不拦的边界。

---

## 动机

部署在由外部流程（git pull + systemd）统一管理代码的机器上时，误触更新（WebUI 按钮、`/upldm` 指令、`--rollback`）会把外部流程维护的代码状态覆盖成更新包内容，造成两套来源互相打架。需要一个明确的总开关：环境变量置位后本次启动拒绝一切主程序更新操作，**无强制继续途径**（要更新就移除变量重启，意图明确）。

## 判定设计（astrbot/core/utils/update_guard.py）

- 真值集合 `("true", "1", "t")`，读取时 strip + lower；不设置或其余任何值 = 更新照常。
- 实时读进程环境变量。外部无法在运行中修改别的进程环境，效果上等同于启动时固化。
- 提示文案集中为 `禁用提示` 常量，各拦截点直接引用，保证口径一致。
- **插件更新不在管辖范围**——插件走独立的安装/更新链路，与本开关无关。

## 拦截点清单（8 处）

| 入口 | 位置 | 方式 |
| --- | --- | --- |
| 源码/WebUI 更新下载 | `updator.py` 更新入口 | `raise Exception(禁用提示)` |
| 应用更新包（含上传包） | `updator.py apply_update_package` | `raise RuntimeError(禁用提示)` |
| WebUI 更新请求 | `update_service.update_project` | `UpdateServiceError(禁用提示)` |
| WebUI 回滚 | `update_service.rollback_to_version` | 同上 |
| WebUI 上传更新包 / 应用压缩包 | `update_service` 上传与应用 | 同上 |
| 服务初始化 | `UpdateService.__init__` | 打一条 info 日志声明已禁用 |
| `/upldm` 指令 | `admin.py` | `event.send` 禁用提示后 return |
| 命令行回滚 | `main.py _do_rollback`、`update_rollback.rollback` | 打印中文提示后退出/返回 False |

### 两处内联判定（同步约束）

`main.py _do_rollback` 与 `update_rollback.rollback` **不能 import astrbot 包**（前者在启动极早期、加载重模块之前执行；后者是纯标准库模块，被 main.py 按文件路径加载）。这两处内联了同一判定（`os.environ.get(...).strip().lower() in ("true", "1", "t")`），代码注释双向标明与 update_guard.py 保持同步——**后续改真值集合必须三处一起改**。

## 有意不拦的边界

- **清理回滚备份不拦**：删除备份不更改源码，禁更期间清理旧备份释放空间是合理操作。
- **立即备份（backup_current_state）不拦**：只读打包、不改源码也不重启（详见回滚备份篇），禁更时仍是可用的安全网。
- 回滚被拒时备份目录原样保留，不做任何删除。

## 文档同步（3 处）

`.env.example`、`astrbot/utils/env_file.py` 的 `_ENV_EXAMPLE_SECTIONS`（每次启动重写 .env.example 的模板）、`main.py --help` 的环境变量段落，三处同步加入 `LDMBOT_DISABLE_UPDATE=1` 条目。

## 测试

`tests/test_update_disabled_env.py`：真值/假值/未设置三分支 × 更新与回滚入口拦截断言。

## 回归点

- 新增更新类入口时**必须**补禁更拦截，否则开关出现旁路。
- 真值集合三处同步：update_guard.py、main.py、update_rollback.py。
- 判定改逻辑时优先改 update_guard.py 并同步两处内联，别引入第四处判定。

## 修改文件清单

1. `astrbot/core/utils/update_guard.py` — 新增，统一判定与文案
2. `astrbot/core/updator.py` — 下载更新、应用更新包两入口拦截
3. `astrbot/dashboard/services/update_service.py` — WebUI 更新/回滚/上传/应用拦截 + 初始化日志
4. `astrbot/builtin_stars/builtin_commands/commands/admin.py` — `/upldm` 拦截
5. `main.py` — `--rollback` 拦截（内联）
6. `astrbot/core/utils/update_rollback.py` — `rollback()` 拦截（内联）
7. `.env.example`、`astrbot/utils/env_file.py`、`main.py --help` — 文档
8. `tests/test_update_disabled_env.py` — 新增专项测试
