# 发版打包

生成 GitHub Release 的 5 个附件：

- `ldmbot_<version>.zip` / `ldmbot.zip`
- `ldmbot_install_embedded_<version>.sh` / `ldmbot_install_embedded.sh`
- `ldmbot_install.sh`

## 前置步骤

1. 修改 `pyproject.toml`、`astrbot/__init__.py`、`uv.lock` 与 `dashboard/dist/version`、`dashboard/dist/assets/version` 中的版本号。
2. 新增 `changelogs/v<version>.md` 与 `详细变更日志/v<version>/` 下的明细。
3. 如果 WebUI 源码有改动，先在 `ldmbot-dashboard` 仓库执行 `pnpm build`，产物会直接写入本仓库的 `dashboard/dist/`。
4. 提交本次发版改动（打包脚本按 `git ls-files` 收集文件，未提交的新文件也能收进 zip，但建议先提交）。

## 打包

```bash
python scripts/release/package_release.py
```

默认输出到与本仓库同级的 `ldmbot资源/`。可用 `--out` 指定其他目录：

```bash
python scripts/release/package_release.py --out "D:/tmp/release"
```

脚本会校验版本一致性、zip 完整性、中文文件名 UTF-8 标志，并重新生成内嵌安装脚本；同时输出 `latest.zip` 供镜像服务器使用。

默认不预生成 `dashboard/dist` 下的 `.gz` 静态缓存（服务端首次访问会自动生成）。如需减小服务端首次访问开销，可加 `--gzip-assets`。

## 上传 Release

```bash
gh release create v<version> \
  --title "v<version>" \
  --notes-file "../ldmbot资源/release_note_v<version>.md" \
  "../ldmbot资源/ldmbot.zip" \
  "../ldmbot资源/ldmbot_<version>.zip" \
  "../ldmbot资源/ldmbot_install.sh" \
  "../ldmbot资源/ldmbot_install_embedded.sh" \
  "../ldmbot资源/ldmbot_install_embedded_<version>.sh"
```
