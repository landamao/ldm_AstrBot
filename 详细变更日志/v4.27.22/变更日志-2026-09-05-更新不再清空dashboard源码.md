# 修复：「更新ldm」整目录覆盖 dashboard 导致本地 WebUI 源码丢失

- 日期：2026-09-05
- 背景：ldm 更新包的 `dashboard/` 只带 WebUI 构建产物（以真实 v4.27.21 包核对：239 个条目全部位于 `dist/` 下，无 src）。而本地 `dashboard/` 下源码（`src/`）、工程文件（package.json、vite.config.ts 等）与 `dist/` 共存。`_应用源码包内容` 此前把顶层 `dashboard` 当普通目录处理：先 `clear_dir_contents` 清空本地 `dashboard/` 整目录、再拷入包内内容 → 一次「更新ldm」就把本地 WebUI 源码全清掉，只剩 dist。且回滚备份只含 `astrbot/` + 实际生效 dist + `main.py`，救不回 dashboard 源码（本次的本地源码是用户从仓库外构建目录复制回来的）。

## 改动

### `astrbot/core/updator.py`

- `_应用源码包内容`：顶层 `dashboard` 与 `data`/`.venv`/`.git` 等受保护目录同等跳过，本地 `dashboard/` 整目录不再被清空覆盖（日志「已跳过保护项」中会出现 `dashboard`）。
- 包内 `dashboard/` 若出现 `dist` 之外的条目，记 warning 后忽略、不覆盖本地同名文件（当前发版包不含此类条目，属前向防御）。
- dist 的替换仍由 `_应用webui` 完成：只清空 + 覆盖**实际生效的 WebUI 目录**（显式 `--webui-dir`/`LDMBOT_WEBUI_DIR` 优先，其次项目根 `dashboard/dist`），旧行为里 dist 的清理本来就由它负责，现在不再被整目录覆盖抢在前面。
- 三个更新入口一并生效：WebUI「更新ldm」按钮（`update_project`）、上传压缩包应用（`apply_uploaded_package`）、`update()`。

### 类注释同步

- `根目录覆盖示例` 注释移除 `dashboard` 示例，标注「dashboard 例外：只同步 dist」。

## 测试

`tests/test_update_preserve_dashboard_source.py`（pytest），4 用例：

1. 本地 dashboard 源码/工程文件原样保留，`dist` 整体替换为新构建产物且旧残留清除（stale.js 消失）；
2. 设 `LDMBOT_WEBUI_DIR` 时 dist 覆盖到该目录，本地 `dashboard/`（含 dist）完全不动；
3. 包内 `dashboard/` 出现 dist 之外条目时忽略，不覆盖本地同名文件；
4. 本地 dashboard 只有源码没有 dist（历史 bug 清空后的状态）时 dist 正常补齐。

另用真实 v4.27.21 更新包在沙箱目录端到端验证：src/package.json/node_modules 全保留，`dist/assets/version` 正确更新为 v4.27.21。相关既有测试（test_core_restart、test_zip_filename_fix）与 ruff（改动文件范围）通过，顺手修了 updator.py 原有的 import 排序问题（I001）。

## 备注

- 回滚备份不含 dashboard 源码是有意设计（备份/回滚只管 `astrbot/` + 实际生效 dist + `main.py`），本次不改；更新不再毁源码后，这一边界的影响变小。
