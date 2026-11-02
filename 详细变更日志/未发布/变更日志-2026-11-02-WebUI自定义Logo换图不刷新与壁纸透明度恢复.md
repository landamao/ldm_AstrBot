# 变更日志 — 2026-11-02 — WebUI 自定义 Logo 换图不刷新修复 + 壁纸透明度滑块恢复（默认 80%）

## 背景

用户反馈两个问题：

1. **自定义 Logo 的图片链接换了图，WebUI 里仍是旧图**：在设置页把 Logo 地址清空再重新填同一个链接保存，顶栏/登录页/ChatUI 头像都不变，还是原来那张图；
2. **壁纸透明度调节没有了**：要求把「壁纸透明度」滑块加回来，默认 80%。

## 根因

### 1. Logo 换图不刷新 = 浏览器按完整 URL 缓存图片

`<img src>` / `background-image` 都走 HTTP 缓存，缓存键是**完整 URL**。用户用的是固定外链（如 `https://cdn.example.com/logo.png`），远端把图片内容换掉后 URL 没变，浏览器就一直命中旧缓存条目 → 永远显示旧图。

**清除 Logo 再重新设置为什么也没用**：清除只是把本地记录删掉，重新设置写回的仍是同一个 URL 字符串，缓存键一模一样 → 照样命中旧图。

**为什么壁纸看起来没这个问题**：壁纸通常是「本站上传」——每次上传都生成新的 uuid 文件名（`/api/v1/ui-preferences/wallpaper/files/<uuid>.jpg`），文件名变了缓存键就变了，必然拿到新图；外链壁纸其实有同样的隐患（这次一并处理了）。

### 2. 壁纸透明度滑块

`opacity` 字段在 v4.27.34（2026-09-19）随「壁纸透明度移除与板块透明度重设计」被整体删除：前端 `normalizeWallpaper` 不再读取/下发 `opacity`，`wallpaperApplier.js` 不再写 `--wallpaper-opacity`，`wallpaper.scss` 的壁纸层去掉 opacity，Settings.vue 删除滑块与状态，后端 `normalize_wallpaper` 同步移除。

## 改动

### 1. 破缓存地址生成（`dashboard/src/utils/uiPreferenceSync.js`）

新增 `buildLogoSrc(url, version)` / `buildWallpaperSrc(url, version)`，核心是 `buildImageSrc`：

- `data:` 图片自带内容 → 原样返回；
- 本站上传文件（uuid 文件名 + `immutable` 缓存头）→ 只附 `?v=<version>`（内容变则文件名变，本地文件不重复下载）；
- 普通外链 → 附 `?v=<version>&t=<页面会话令牌>`，**页面每次加载令牌都会变**，远端换图后刷新页面即可看到新图；
- **自带普通 query 的外链照常追加**（用 `&` 续接）：典型如 QQ 头像直链 `https://q1.qlogo.cn/g?b=qq&nk=xxx&s=0&type=1`——首版实现把「带 query 一律放行」当成了签名链接处理，导致这类地址仍命中旧缓存（用户实测反馈后修正）；
- **只有真正的签名链接原样返回**：URL 查询参数里含 `X-Amz-Signature` / `X-Amz-Credential` / `OSSAccessKeyId` / `Signature` / `Expires` 等签名参数名时，追加参数会导致签名校验失败，此时不动地址；
- URL 带 `#fragment` 时，破缓存参数加在 fragment 之前。

### 2. Logo/壁纸版本号（version）

- `normalizeLogo` / `normalize_wallpaper` 支持 `version`（毫秒时间戳）并透传；
- 设置页每次保存 Logo/壁纸时写入 `version: Date.now()`：地址没变但图换了时，展示地址带上新版本号 → 浏览器必然重新拉取；
- 后端 `normalize_logo` / `normalize_wallpaper` 同步支持并持久化 `version`（`_normalize_version` 只接受正整数）。

### 3. 展示点统一走破缓存地址

- `useCustomLogo`（顶栏/登录页/ChatUI 头像/插件页等全部 Logo 展示点）改用 `buildLogoSrc`；
- `faviconSync.js`（浏览器标签页图标）同步改用 `buildLogoSrc`；
- 设置页 Logo 预览：输入防抖 400ms + 每次刷新版本号，预览不再被旧缓存挡住。

### 4. 壁纸透明度恢复（默认 80%）

- `uiPreferenceSync.js`：新增 `WALLPAPER_OPACITY_DEFAULT = 80`（范围 0-100，100=完全不透明），`normalizeWallpaper` 读取 `opacity`（旧结构同义，沿用），等于默认值时省略存储；
- `wallpaperApplier.js`：写入 `--wallpaper-opacity`（0-1）；
- `wallpaper.scss`：壁纸层 `::before` 加 `opacity: var(--wallpaper-opacity, 0.8)`；
- Settings.vue：新增「壁纸透明度」滑块（0-100，默认 80），预览区把壁纸拆成独立图层 `.wallpaper-preview__bg`，按不透明度显示，与真实实现一致；
- 后端 `normalize_wallpaper`：新增 `WALLPAPER_OPACITY_MIN/MAX/DEFAULT` 与 `opacity` 规范化。

### 5. 顺带修掉的连带问题

- `logoHasChanges` / `wallpaperHasChanges` 改为忽略 `version` 字段比较（版本号每次保存都刷新，参与比较会让「未保存改动」提示永远亮着）。

## 修改文件清单

| 文件 | 改动 |
| --- | --- |
| `dashboard/src/utils/uiPreferenceSync.js` | 破缓存地址生成（buildLogoSrc/buildWallpaperSrc）、Logo/壁纸 version 透传、壁纸 opacity 字段与默认值 |
| `dashboard/src/utils/wallpaperApplier.js` | 写 `--wallpaper-opacity`；壁纸地址走 buildWallpaperSrc |
| `dashboard/src/utils/faviconSync.js` | favicon 地址走 buildLogoSrc |
| `dashboard/src/composables/useCustomLogo.ts` | 全部 Logo 展示点走 buildLogoSrc |
| `dashboard/src/scss/wallpaper.scss` | 壁纸层应用 `--wallpaper-opacity` |
| `dashboard/src/views/Settings.vue` | 壁纸透明度滑块 + 预览图层、Logo 预览破缓存、保存时写 version、改动比较忽略 version |
| `dashboard/src/i18n/locales/zh-CN/features/settings.json` | `wallpaper.opacityLabel/opacityHint`、logo.urlHint 补充换图说明 |
| `dashboard/tests/uiPreferenceSync.test.mjs` | 新增：壁纸 opacity 钳制/默认值省略、Logo version 透传、破缓存地址四类规则（10 例） |
| `dashboard/tests/aliasStubLoader.mjs`、`dashboard/tests/stubs/apiHttpStub.mjs` | 测试用「@/」别名与网络层打桩 |
| `astrbot/dashboard/services/dashboard_preference_service.py` | `normalize_wallpaper` 支持 opacity/version；`normalize_logo` 支持 version |

## 验证

- 前端单测：`node --test tests/uiPreferenceSync.test.mjs` → 11 passed（含 QQ 头像直链追加破缓存参数、OSS/S3 预签名链接保持原样两类用例）；
- 后端规范化（打桩 astrbot 依赖直跑）：无壁纸全默认返回 None；`opacity=50/100` 保留、999 钳到 100；旧结构 `{url, opacity:30}` → 竖屏壁纸 + opacity 30；`version` 透传；Logo 非法协议/reset 返回 None；
- `pnpm build`（含 `vue-tsc --noEmit` 类型检查）通过，产物 `dist/assets/index-*.css` 内确认含 `opacity: var(--wallpaper-opacity, .8)`，壁纸应用层产物含 `--wallpaper-opacity` 写入逻辑；
- QQ 头像直链实测：`https://q1.qlogo.cn/g?b=qq&nk=663309071&s=0&type=1&v=<ts>&t=<token>` 返回 HTTP 200 / image/jpeg / 108KB，内容为换图后的新头像（追加参数不影响 CDN 取图，且能绕开浏览器旧缓存）。

## 部署 / 测试注意

- 后端改动（`normalize_wallpaper` / `normalize_logo` 支持 version 与 opacity）需重启 ldm 生效；前端刷新（Ctrl+F5）即可；
- 用户侧验证：设置页把 Logo 换成同一个外链再保存一次 → 顶栏/登录页/ChatUI 头像立刻换成新图；壁纸透明度滑块默认显示 80%，拖动后保存即时生效；
- 存量配置里的旧 `opacity` 字段此前被丢弃，本次恢复读取并沿用「壁纸不透明度」语义（100=完全不透明），不会与新的默认 80 冲突；
- 签名类外链（URL 自带 query）不追加任何参数，避免破坏签名；此类链接若要换图，请换新签名 URL 或改用本站上传。
