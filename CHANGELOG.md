# 更新日志（CHANGELOG）

本文档记录 ldmbot（AstrBot 魔改版）的重要变更。  
格式大致遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，正文保持中文。

---

<details>
<summary><strong>[4.26.38] — 2026-08-19</strong> — 登录页 NapCat 风格重做、自定义壁纸、更新器支持 --webui-dir</summary>

基于 ldm v4.26.37 的版本。本次将登录页全新重做为 NapCat 风格（粉紫渐变光斑、玻璃拟态卡片、3D 倾斜交互），登录按钮固定粉色渐变不再受旧主题色影响；设置页新增「自定义壁纸」（图片地址/本地上传/透明度调节）；修复更新器忽略 `--webui-dir` 启动参数导致更新 WebUI 不生效的问题。

### 新增

- **登录页 NapCat 风格重做**（`/auth/login` 整页重写，参考 NapCat WebUI 登录页并升级）
  - 背景：粉紫渐变 + 3 个动态光斑（缓慢漂移动画，NapCat 为静态光斑）
  - 右上角：新增 GitHub 图标（链接 landamao/ldm_AstrBot 仓库）+ 主题切换按钮（玻璃质感）
  - 登录卡片：玻璃拟态（半透明 + backdrop-blur 磨砂 + 粉色描边 + 大圆角 28px）
  - 3D 交互：卡片跟随鼠标 3D 倾斜，0.12s 快速过渡即时跟手，移出 0.12s 回弹归位
  - 入场动画：卡片淡入 + 上浮 + 弹性缩放（spring 曲线）
  - logo：头像放大 + 粉色投影（无浮动动画）
  - 标题：「ldm」深色 +「WebUI」粉紫渐变高亮（仿 NapCat 两段式配色）
  - 输入框：大圆角 16px + 聚焦粉晕
  - 登录按钮：通栏胶囊造型（圆角 999px）+ 粉紫渐变 + 悬浮发光 + 点击按压回弹
  - 暗色模式完整适配：深紫红渐变背景 + 暗色光斑 + 深色玻璃卡片 + 白色标题
- **WebUI 自定义壁纸**（设置 → 外观 新增设置项）
  - 壁纸图片地址（支持 http/https 或 data: 图片，地址无效/加载失败有提示）
  - 上传图片：选择本地图片上传到服务器（data/wallpapers/），自动填入地址；支持 png/jpg/jpeg/webp/gif/bmp，最大 10MB；换壁纸/清除时自动删除旧上传文件
  - 壁纸透明度滑块（10%-100%，默认 100%，数值越小壁纸越淡）
  - 界面板块透明度滑块（0%-90%，默认 0%，数值越大侧边栏/顶栏/内容卡片越透明，壁纸越明显）
  - 实时预览卡片：模拟侧边栏 + 顶栏 + 内容卡片骨架，拖动滑块即时联动
  - 「清除壁纸」按钮恢复默认纯色背景
  - 壁纸设置与主题色同一套机制：保存到服务端 preferences（`dashboard_wallpaper`，跨浏览器共享）+ localStorage 缓存；点右下角保存按钮才应用全局，未保存只在预览卡内看效果
  - 壁纸只作用于登录后的主界面（wallpaper-mode），登录页保持 NapCat 渐变风格不受影响
  - 上传文件访问接口 `GET /api/v1/ui-preferences/wallpaper/files/{uuid}.ext`（需登录，uuid 文件名防枚举、防路径穿越、长缓存）

### 体验优化

- **平台日志页「面板透明度」独立调节**
  - 日志终端背景不再跟随全局板块透明度，平台日志页头新增「面板透明度」按钮（v-menu 弹滑块，0-90%）
  - 终端区背景用独立 CSS 变量 `--console-alpha`（`rgba(surface, var(--console-alpha, var(--panel-alpha, 1)))`），未设置时回退全局；滑块写入 localStorage `console_panel_alpha`
  - 浅色主题下日志终端底色固定深色 `rgba(30,30,30,alpha)`（=#1e1e1e），深浅主题一致可读，壁纸仍从半透明深色底透出
- **移除登录页版本显示与版本不一致提示**
  - 删除登录页底部版本显示（WebUI/ldm/ldm(Code) 版本信息行）、版本不一致提示按钮 + 版本状态弹窗（含相关 i18n 引用）、`publicApi.versions()` 调用及 `PublicVersionData` 类型引用

### 修复

- **登录按钮背景固定粉色渐变**（`#FF7FAC → #F06292`），不再使用主题变量（`--v-theme-primary/secondary`）
  - 原因：按钮原用主题变量，若浏览器/服务端保存过旧主题色（换肤前紫色系），按钮会显示成紫色而非粉色；现在无论主题色如何覆盖，登录按钮始终是粉色
- **更新器支持 `--webui-dir`**（修复「传 --webui-dir 启动后，更新 WebUI 仍覆盖默认 data/dist」）
  - 根因：`updator.py` 的 `_应用webui()` 目标目录写死为 `data/dist`，完全不看启动参数；而启动时 `main.py:check_dashboard_files()` 与 `server.py` 都优先用 `--webui-dir` 服务 → 运行时服务和更新目标是两个地方，更新完 WebUI 不生效
  - 新增 `_resolve_webui_dir()` 解析当前进程的 `--webui-dir`（来源优先级：命令行参数 → `ASTRBOT_WEBUI_DIR` 环境变量，与重启参数保留 `_build_frozen_reboot_args` 同一套来源）；`_应用webui()` 应用更新前先解析：指定且目录存在 → 覆盖到该目录；指定但不存在 → 警告并回退默认 data/dist；未指定 → 默认（行为不变）
  - version 兜底逻辑（新包缺 assets/version 时回写旧 version）对自定义目录同样生效；完整更新（`apply_update_package`）与仅更新 WebUI（`apply_webui_only_from_package` / 面板「更新面板」）两条路径都覆盖
  - 重启后 `_build_frozen_reboot_args` 会继续把 `--webui-dir` 带给新进程，更新目标不会丢
- **修复「点外观后设置页按钮点不了」**：预览提示层（wallpaper-preview__empty）绝对定位逃逸到 .v-main 盖住全屏，已移入预览舞台内部并给容器加 position: relative
- **修复「设置了壁纸但背景还是白色」**：根因是 `scss/wallpaper.scss` 在 `.v-application.wallpaper-mode` 上声明了 `--wallpaper-image: none` 等三个默认值——CSS 自定义属性一旦在元素自身声明就会阻断从 html 继承，而壁纸变量写在 `document.documentElement`，导致 `::before` 永远解析到 `none`。已删除这三行默认值（`::before`/板块规则里 `var(..., 默认值)` 本身有兜底）
- **板块透明度覆盖范围扩展**（对照 astrbot_plugin_palette 策略）
  - 侧边栏：`.v-navigation-drawer` + `.leftSidebar` 双保险，内部 `.v-list` 透明、激活项 `rgba(surface, alpha*0.72)`
  - 日志终端：`.console-displayer-wrapper / .console-term / #console-wrapper / [style*="background-color: #1e1e1e"]` 系列（ConsoleDisplayer 硬编码 #1e1e1e 背景显式覆盖）+ 全屏 backdrop
  - 模型提供商页：provider-workbench / __sidebar / __main / provider-config-* / provider-sources-* / provider-source-item / provider-empty-state / provider-chat-panel / provider-drawer-* 全部覆盖
  - 其他页面级：config-* 系列、stats-page 卡片、trace-page 卡片、settings/session-management/knowledge-base/kb-detail/persona-manager 的 .v-card
  - 边框弱化：v-card--variant-outlined / v-field--variant-outlined / v-table 边框固定 `rgba(on-surface, 0.06)`（接近隐形）；分隔线 `rgba(on-surface, alpha*0.18)`；provider 激活/悬停项 `rgba(primary, alpha*0.14)`
  - Chat 页保持不纳入（ChatUI 自有背景体系）
- **修复壁纸模式黑色线框**（插件页每张插件卡一框黑线不好看）
  - 原因：壁纸 CSS 把 outlined 卡片/输入框/表格边框覆盖为 `rgba(on-surface, 板块透明度 * 0.24)`——即使板块透明度 100% 也比 Vuetify 默认边框（0.12）深一倍，且随透明度滑块联动
  - 修法：边框改为固定淡色 `rgba(on-surface, 0.06)`，不再跟随板块透明度变化（用户反馈 0.12 仍偏重后调低）；分隔线/激活项高亮等其他规则不受影响

### 说明

- 更新后请**手动重启**一次服务（后端 Python 改动需重启才加载，壁纸上传接口为新增路由，重启后上传才可用；前端改动需同步源码后强刷浏览器 Ctrl+F5）
- 登录页暗色主题类名是 `v-theme--PurpleThemeDark`（Vuetify 3.7 按主题名生成），不是 `v-theme--dark`

</details>

<details>
<summary><strong>[4.26.37] — 2026-08-18</strong> — NapCat 风格 q弹粉粉主题；系统设置手动保存；移除多语言仅保留中文；打断提示标签修复</summary>

基于 ldm v4.26.36 的版本。本次将 WebUI 整体改为 NapCat 风格「q弹粉粉」主题（双粉配色、全局 Q 弹动效、新增退出登录入口）；「设置」页由「改即存」改为手动保存；移除英文/俄文语言包仅保留简体中文；修复打断回复提示标签与实际行为不一致、及打断时误污染上一条完整回复的问题。

### 跟随官方更新

- 本次无跟随官方上游更新内容；全部为 ldmbot 本地改动。

### 新增

- **WebUI 整体改为 NapCat 风格「q弹粉粉」主题**
  - 亮色主色 樱花粉 `#FF7FAC`、辅色 玫瑰粉 `#F06292`（双粉配色，无蓝色）；暗色主色 `#f31260`、辅色 浅粉 `#F48FB1`
  - 全局背景改为 纯色（亮色 `#ffffff` / 暗色 `#1E121C`），页面内容容器不透明底色；不使用渐变光斑（避免粉色审美疲劳）
  - 圆角 8px → 12px，卡片 hover 轻浮 + 粉色阴影，按钮 hover 微缩放（Q 弹手感）
  - 滚动条改为浅粉（跟随主题主色的粉色半透明，如 rgba(255,127,172,…)）；字体栈加入 Quicksand / Nunito（圆润），中文仍走系统字体兜底；选中文字浅粉底
- **全局点击按压动效（NapCat Q 弹）**
  - 按钮/图标/标签/chip 按下 0.06s 缩到 0.92、卡片 0.98、列表项 0.96、开关/复选框 0.88，松开走弹性曲线回弹；弹窗/抽屉内同样生效；文本输入框不跳动；触屏设备保留反馈
- **右上角 ⋯ 菜单新增「退出登录」**
  - 红色 mdi-logout 图标，点击清除会话跳转登录页
- **数据统计页卡片 Q 弹动效**
  - 统计卡片 hover 轻浮 + 粉色阴影、点击按压回弹

### 体验优化

- **「设置」页改为手动保存**
  - 系统配置（基础运行/代理与依赖源/WebUI 安全/日志/缓存/文本转图像）与外观主题色由「改即存」改为点击右下角保存按钮才保存，避免边改边写、误触即存，与「配置文件设置」页交互一致
  - 右下角新增浮动保存按钮，无改动时置灰防误点；顶部新增常驻提示条「设置有未保存的修改。请点击右下角保存按钮以生效。」，切到任意设置分页不丢提示
  - 主题色选色不再即时改页面，点保存成功后才应用并持久化；「恢复默认」只改控件值，随保存按钮一起提交并应用
  - 侧边栏 v-list 隐藏滚动条由「仅折叠态」改为始终应用（滚动能力保留、滚动条不可见）
- **移除多语言仅保留简体中文**
  - 删除英文（en-US）、俄文（ru-RU）两套语言包（合计约 524KB）与语言切换功能，WebUI 只保留简体中文，以后改文案只需改 zh-CN 一份
  - 主 bundle 4087KB → 3741KB（gzip 1131 → 1041KB）
  - 后端配置元数据 i18n 机制保留（ConfigMetadataI18n），插件自带 i18n 数据仍兼容（只取 zh-CN 部分）；老用户 localStorage 残留 `astrbot-locale` 不再读取，无需清理
- **登录页卡片圆角加大、侧边栏 Q 弹动效**
  - 登录页卡片圆角加大到 20px，登录页背景为不透明容器底色（containerBg），不使用渐变光斑
  - 侧边栏菜单项 hover 弹性轻移（translateX 4px + scale 1.02）+ 粉色阴影 + 图标放大（scale 1.16，不旋转）；折叠 rail 态位移减弱防溢出
  - 插件页 Q 弹策略：列表页每张插件卡片单独 Q 弹，卡片内部按钮/开关/chip 保持静态；插件详情页完全不 Q 弹

### 修复

- **打断回复「写入上下文的打断提示文案」提示与实际行为不符**
  - WebUI 提示称「写入 &lt;system_reminder&gt; 的提示正文；无需手写标签」，但实际落库路径（真截断时写入对话历史）此前是纯文本直接拼接，没有 &lt;system_reminder&gt; 标签，仅等待旧任务超时的兜底路径会自动包标签
  - 现在落库路径与超时兜底路径保持一致：写入历史时自动包裹 &lt;system_reminder&gt;…&lt;/system_reminder&gt;（配置文案已含标签则不重复包裹），两条 hint 文案同步修正为与实际行为一致
- **打断时未发出任何内容，误污染上一条完整回复**
  - 场景：上一条回复已完整发出，新请求生成中被打断（流式还没产出任何内容，已发出长度为 0、任务被中止），旧逻辑会把打断提示追加到上一条已完整发出的 assistant 消息上，造成「明明回复完了，历史里却被注入打断提示」
  - 修复：`_apply_interrupt_to_messages` 开头增加保护——已发出内容为空且任务被中止时直接返回历史原样，不裁剪、不写打断提示（本次请求对用户不可见，历史不应留痕），也不凭空追加「打断提示」assistant 消息

### 说明

- 更新后请**手动重启**一次服务（后端 Python 改动需重启才加载；前端改动需同步源码后强刷浏览器）
- 默认主题色已改，但服务端偏好里存过旧自定义色（蓝灰/黄）会覆盖新默认值，请到「设置 → 外观」点一次「恢复默认」回到樱花粉主题
- 此前已写入的脏历史（完整回复被误挂打断提示）不会自动清除，可删除对应会话或忽略，新对话不再出现
- 若面板显示异常，可用功能菜单中的「强制刷新 WebUI 面板」或浏览器 Ctrl+F5
- 建议验证：外观默认主题为双粉配色、全局 Q 弹动效；设置页右下角出现保存按钮、顶部有未保存提示条；右上角 ⋯ 菜单有「退出登录」；打断回复时写入历史的打断提示自动包裹 &lt;system_reminder&gt; 标签、未发出内容时不污染上一条回复

</details>

<details>
<summary><strong>[4.26.36] — 2026-08-17</strong> — 插件详情指令别名显示；顶栏强制刷新按钮调整；本地文档渲染入口</summary>

基于 ldm v4.26.35 的体验优化版本。本次为 WebUI 插件详情页补全指令别名展示（含截断内容悬停/点击查看），移除顶栏「强制刷新」按钮防误触（功能菜单入口保留），自动强制刷新前增加绿色提示；左下角「官方文档」改为本地渲染项目 README，并新增「更新日志」入口。

### 新增

- **插件详情「插件行为」指令别名显示**
  - WebUI 插件详情页 → 插件行为 → 指令区域：指令行新增独立「别名」列，显示该指令注册的别名（v-chip 小标签，与指令管理页格式一致）；
  - 指令组、子指令、多级指令的别名都会显示（别名挂在叶子指令上）；
  - 同一指令被多个 handler 注册时，别名取并集（去重保序）；
  - 后端 `astrbot/dashboard/services/plugin_service.py`：`_build_command_filter_component` / `_build_command_group_component` / `_build_command_group_child` 通过新增 `_attach_aliases()` 把过滤器上的别名集合（排序列表）挂到命令组件上，空别名不加字段；`_merge_command_component` 合并同名指令时别名取并集。
  - 前端 `dashboard/src/views/extension/PluginDetailPage.vue`：指令表格从两列改为三列（指令名 | 别名 | 描述），无别名显示「-」。

- **截断内容悬停/点击查看完整信息**
  - 指令名、别名、其他行为名称（监听器/钩子/页面等）被省略号截断时，鼠标滑过（桌面）或点击（手机，`open-on-click`）可查看完整内容；
  - 别名悬停/点击显示全部别名（以「 / 」分隔）。

### 体验优化

- **顶栏强制刷新按钮移除（防误触）**
  - 删除 WebUI 顶栏右侧的「强制刷新」图标按钮（mdi-refresh），防止误触导致整页强制重载；
  - 功能菜单（⋯ 菜单）里的「强制刷新 WebUI 面板」入口**保留**，需要时仍可手动强制刷新；
  - 更新后自动全量重载逻辑不受影响（`forceRefreshWebUI` 仍用于更新完成后的自动刷新）。

- **自动强制刷新前弹绿色提示**
  - 面板/内核更新完成后自动强制刷新时，刷新前先弹出绿色（success）提示「面板更新完成，正在强制刷新页面…」等，延迟约 0.8s 再整页刷新，避免用户觉得页面「莫名其妙自己刷新」；
  - `forceRefreshWebUI` 增加可选 `message` / `delay` 参数；手动点「强制刷新面板」保持立即刷新不加提示。

- **左下角「官方文档」改为本地渲染，新增「更新日志」入口**
  - 侧边栏左下角「官方文档」按钮不再外链 GitHub README，改为在弹窗内渲染**项目根目录的 README.md**（markdown 渲染样式与插件文档一致，含代码高亮 / 表格 / 复制按钮）；
  - 新增「更新日志」按钮（mdi-history），弹窗内渲染项目根目录的 **CHANGELOG.md**；
  - 后端新增 `GET /api/v1/project-docs/{readme|changelog}` 接口（`astrbot/dashboard/api/project_docs.py`），读取源码树根目录对应文件，已登录用户即可访问；
  - 前端复用 `ReadmeDialog` 组件，新增 `project-readme` / `project-changelog` 两种模式；`VerticalSidebar.vue` 底部按钮接入。

- **插件详情指令表格列宽适配手机端**
  - 桌面端：指令名 200px、别名 140px；
  - 手机端（≤700px）：指令名 110px、别名 90px，保证描述列有足够宽度正常换行，不再出现「说明一个字一行」。

### 说明

- 更新后请**手动重启**一次服务（后端 Python 改动需重启才加载；前端改动需同步源码后强刷浏览器）
- 若面板显示异常，可用功能菜单中的「强制刷新 WebUI 面板」或浏览器 Ctrl+F5
- 建议验证：插件详情 → 插件行为 → 指令区域，指令行应显示别名列；顶栏右侧无强制刷新图标按钮，功能菜单里仍有「强制刷新 WebUI 面板」；左下角「官方文档」弹窗渲染 README.md、「更新日志」弹窗渲染 CHANGELOG.md；更新完成后自动刷新前先弹绿色提示

</details>

<details>
<summary><strong>[4.26.35] — 2026-08-17</strong> — 会话级插件禁用全面生效（事件钩子 / 装饰结果 / LLM 工具）</summary>

基于 ldm v4.26.34 的版本。本次修复 WebUI「会话管理 → 自定义规则 → 插件配置 → 禁用的插件」原本只拦截指令/消息处理器、事件钩子与 LLM 工具全部绕过的问题——现在三套派发链路全部按会话禁用规则过滤，与界面承诺的「整体禁用」语义一致。

### 修复

- **会话级插件禁用全面生效**
  - 指令/消息处理器链路原本就生效（`filter_handlers_by_session`），本次补齐其余两条绕过路径：
  - **事件钩子**（`on_llm_request` / `on_llm_response` / `on_agent_begin` / `on_agent_done` / `on_using_llm_tool` / `on_llm_tool_respond` / `on_after_message_sent` / `on_plugin_error` 等，统一派发点 `call_event_hook`）：被会话规则禁用的插件，其事件钩子不再触发；
  - **发送前装饰钩子**（`on_decorating_result`，`result_decorate/stage.py` 手写循环派发）：同样按会话禁用过滤；
  - **插件 LLM 工具**（`@llm_tool`，请求构建时注入）：被会话禁用插件的工具从工具集中剔除，不注入给模型（MCP 工具、无插件归属的保留工具不受影响）。
  - 系统级事件（启动完成 / 插件加载 / 插件卸载）按自身循环派发、无会话概念，不受影响。
- **查库缓存**：新增 `SessionPluginManager.get_session_disabled_plugins()` 助手，带事件级缓存（`event._extras`），避免一条消息请求周期内钩子高频派发（agent begin/done、llm request/response、tool start/end、after_message_sent、decorating_result、plugin_error 等十余次）反复查库。

### 说明

- 更新后请**手动重启**一次服务（后端 Python 改动需重启才加载）
- 建议验证：WebUI「会话管理 → 自定义规则 → 插件配置」禁用某插件后，在该会话中确认：① 插件指令不再响应；② 插件的事件钩子不再触发（如 on_llm_request 拦截）；③ 插件的 LLM 工具不再出现在模型工具列表中；其他会话不受影响

</details>

<details>
<summary><strong>[4.26.34] — 2026-08-16</strong> — /flow 指令会话级流式输出；模型提供商独立代理配置（三态：独立代理/全局代理/直连）</summary>

基于 ldm v4.26.33 的版本。本次新增会话级流式输出指令 /flow，可按会话独立控制流式输出（默认跟随全局配置）；同时为模型提供商补齐与平台代理同构的独立代理配置，统一「独立代理 > 全局代理 > 直连」三态。

### 新增

- **/flow 指令（会话级流式输出控制）**
  - 新增系统指令 `/flow`，单独设置「当前会话」是否启用流式输出：
    - `/flow on`：当前会话强制开启流式输出；
    - `/flow off`：当前会话强制关闭流式输出；
    - `/flow unset`：取消设置，恢复跟随全局配置（WebUI 系统设置 → 模型提供商 → 流式输出）；
    - `/flow`（不带参数）：切换当前会话的流式输出开关（已设置则取反；未设置则按当前实际生效状态取反设为显式覆盖）；
    - `/flow help`：显示帮助。
  - 按群聊/私聊/WebChat 会话各自独立存储，互不影响。
  - 覆盖优先级：事件 extra（插件）> 会话覆盖（/flow）> 全局配置，未设置即跟随配置。
  - 主 Agent 路径覆盖全部平台（QQ/Telegram/WebChat 均走 internal/third_party 两个子阶段），subagent 工具内部 LLM 同步会话覆盖。
- **模型提供商独立代理配置**
  - 每个模型提供商配置中新增「使用全局代理」开关与「独立代理地址」输入框，三态解析（优先级从高到低）：
    1. 独立代理地址：仅该提供商使用的 HTTP/HTTPS 代理；
    2. 使用全局代理开关：开启后复用「系统设置 → 网络」中的全局 HTTP 代理（默认开启，保持旧版「自动读环境变量代理」的行为）；
    3. 都不配置：直连。
  - 统一入口：`AbstractProvider.get_proxy()`（`astrbot/core/provider/provider.py`）三态解析，LLM / TTS / STT / Embedding / Rerank 全部适配器统一接入（15 个文件）。
  - 全部 60 个提供商配置模板补齐 `use_global_proxy` + `proxy` 字段；老配置加载时自动补默认值，行为与旧版一致。

### 体验优化

- **代理配置项条件显示**
  - 「使用全局代理」开关显示在「独立代理地址」上方；
  - 开启「使用全局代理」时自动隐藏「独立代理地址」输入框（condition 条件显示），关闭后恢复。

### 说明

- 更新后请**手动重启**一次服务（后端 Python 改动需重启才加载）
- 若面板显示异常，可用顶栏「强制刷新面板」或浏览器 Ctrl+F5
- 建议验证：聊天中分别测试 `/flow on`、`/flow off`、`/flow unset`、`/flow`（无参切换）；WebUI 提供商编辑面板查看「使用全局代理」开关与条件显示的「独立代理地址」输入框

</details>

<details>
<summary><strong>[4.26.33] — 2026-08-09</strong> — 消息平台独立代理（三态：独立代理/全局代理/直连）；品牌文案统一 ldmbot</summary>

基于 ldm v4.26.32 的体验优化版本。本次为每个消息平台新增独立代理配置：可单独指定代理地址，也可一键复用全局代理，都不配置时平台直连（不再被环境变量代理劫持）。同时将配置默认值与提示文案中的品牌统一为 ldmbot。

### 新增

- **消息平台独立代理配置**
  - 每个平台配置中新增「使用全局代理」开关与「独立代理地址」输入框，三态解析（优先级从高到低）：
    1. 独立代理地址：仅该平台使用的 HTTP/HTTPS 代理，填一个地址同时覆盖 http 与 https 流量；
    2. 使用全局代理开关：开启后复用「系统设置 → 网络」中的全局 HTTP 代理；
    3. 都不配置：该平台直连，不受任何代理影响（修正了此前「留空自动走全局代理」的不准确行为）。
  - 统一入口：`Platform.get_proxy()`（`astrbot/core/platform/platform.py`）解析实际代理地址；适配器把结果显式传给网络客户端，未配置时传 `None` 强制直连。
  - 支持的平台（8 个，出站连接真正生效）：
    - **Telegram**：PTB `ApplicationBuilder().proxy()` 同时配置主请求与 getUpdates 轮询请求；直连时用 `HTTPXRequest(trust_env=False)` 禁掉 httpx 环境变量代理；
    - **Discord**：原 `discord_proxy` 字段删除，统一改用 `proxy`，传给 pycord `discord.Bot(proxy=)`；
    - **Satori**：aiohttp session 与 websockets 连接均注入；
    - **Line**：aiohttp session 注入；
    - **Misskey**：aiohttp session 与 websockets 连接均注入；
    - **Mattermost**：aiohttp session 与 websocket 均注入；
    - **Slack**：`AsyncWebClient` 与 Socket Mode 客户端均注入；
    - **KOOK**：aiohttp session 与 websockets 连接均注入。
  - 不支持的平台（OneBot v11、QQ 官方、钉钉、飞书、企业微信、个人微信等）：模板不显示代理字段，避免「填了不生效」的误导。

### 体验优化

- **代理配置项条件显示**
  - 「使用全局代理」开关显示在「独立代理地址」上方；
  - 开启「使用全局代理」时自动隐藏「独立代理地址」输入框（condition 条件显示），关闭后恢复。
- **Telegram Token 提示文案更新**
  - 原「如果你的网络环境为中国大陆，请在 `其他配置` 处设置代理或更改 api_base」已过时，改为「如果你的网络无法直接访问 Telegram，请在下方代理设置中配置「独立代理地址」或开启「使用全局代理」」。

### 文案统一

- **配置默认值与提示文案品牌统一为 ldmbot**
  - `start_message` 默认值：`Hello, I'm AstrBot!` → `Hello, I'm ldmbot!`；
  - 后端 `astrbot/core/config/default.py` 全部用户可见 hint/description 中的 AstrBot（25 处）与残留 ldm（3 处）统一为 ldmbot；
  - 前端三语 `config-metadata.json`（zh-CN/en-US/ru-RU）hint/description 品牌统一（zh 29 处、en 28 处、ru 17 处）；
  - 保留不动：`admins_id`/`username` 功能性默认值、docs.astrbot.app 文档 URL、Dify 变量名（astrbot_wf_output 等）、日志路径、组件名与 i18n key 等代码层引用。

### 说明

- 更新后请手动同步源码（`bash ~/同步源码.sh`）并重启服务。
- 已有 Telegram 平台配置中的 `start_message` 不会被模板合并覆盖，如需新默认值请在该平台配置中手动修改或删除重建。
- Discord 用户注意：`discord_proxy` 字段已移除，统一使用新代理配置（独立代理地址 / 使用全局代理）。

</details>

<details>
<summary><strong>[4.26.32] — 2026-08-09</strong> — WebUI 打开自动强刷；失败插件可直接编辑配置</summary>

基于 ldm v4.26.31 的体验优化版本。本次部署新版 WebUI 后打开面板会自动检测版本变化并强制刷新，不再需要手动 Ctrl+F5；插件加载失败时可在失败列表直接「编辑配置」修正后重新加载，并优化了重载失败与保存配置日志窗口的交互。

### 新增

- **WebUI 打开时自动检测版本并强制刷新**
  - 部署新版面板后，浏览器可能仍缓存旧页面，需要手动 Ctrl+F5 才能看到新版。现在打开 WebUI 时会自动比对当前版本与本地记录，版本不一致即自动执行强制刷新（清理缓存 + 重新拉取资源），无需手动操作。
  - 带防循环保护：刷新后记录新版本，同版本不再重复刷新；开发模式（pnpm dev）不生效，不影响本地开发。

- **插件加载失败可直接编辑配置**
  - 插件加载失败（依赖变化、配置写错等原因）时，失败列表新增「编辑配置」按钮（有配置项 schema 的插件可用），可直接修正配置后保存，保存后自动重新加载插件；加载成功即进入正常列表，仍失败则留在失败列表并显示最新错误。
  - 重载失败后列表立即刷新：正常插件重载失败会马上从正常列表移入失败表格，不再出现「旧插件还在 / 失败插件不出现」的假象。

### 体验优化

- **保存配置日志窗口交互优化**
  - 保存插件配置弹出的日志窗口可点击外部直接关闭；插件重载报错时窗口保留日志、不再 2 秒自动关闭，便于查看错误原因。

### 说明

- 更新后请手动同步源码并重启服务。
- 若面板显示异常，可用顶栏「强制刷新面板」或浏览器 Ctrl+F5。

</details>

<details>
<summary><strong>[4.26.31] — 2026-08-06</strong> — StarTools 新增共享字体实例接口、统一字体文件名</summary>

基于 ldm v4.26.30 的体验优化版本。本次为 StarTools 新增共享字体实例接口，插件可跨插件复用同一字体对象，避免各自加载字体实例导致的内存浪费；同时统一字体文件名为 `font.ttf`。

### 新增

- **StarTools.get_font(size) 共享字体实例接口**
  - 新增 `StarTools.get_font(size=14)` 类方法，委托 `t2i/local_strategy.py` 的 `FontManager.get_font()`，按 size 缓存字体对象，跨插件复用同一个实例。
  - 字体查找顺序：`data/font.ttf`（自定义）→ 系统字体（跨平台 CJK 回退链：微软雅黑 / 思源黑体 / 苹方 / Arial / DejaVu）→ PIL 默认字体。
  - 插件侧从各自 `ImageFont.truetype(...)` 改为 `StarTools.get_font(24)` 即可，同 size 全局共享同一对象，减少内存占用。
  - 使用 `from __future__ import annotations` + `TYPE_CHECKING` 延迟 PIL 导入，避免运行时注解求值 NameError。

### 体验优化

- **统一字体文件名为 font.ttf**
  - `StarTools.get_font_path()` 原返回 `data/ldm.ttf`，与 `FontManager` 使用的 `data/font.ttf` 及配置提示文案不一致，现统一为 `font.ttf`。

### 说明

- 更新后请**手动重启**一次服务
- 若插件中曾自行 `ImageFont.truetype` 加载字体，建议迁移到 `StarTools.get_font(size)` 以复用共享实例
- 若曾使用 `StarTools.get_font_path()` 获取字体路径，请将字体文件从 `ldm.ttf` 重命名为 `font.ttf`

</details>

<details>
<summary><strong>[4.26.30] — 2026-08-05</strong> — 跨提供商切换显示名修复、WebChat 重试可配置化、models.dev 镜像加速</summary>

基于 ldm v4.26.29 的体验优化与修复版本。本次修复跨提供商自动切换时提供商名展示带模型名的问题，将 WebChat 标题生成的重试次数提升为可配置项，并将 models.dev 元数据源切换至国内镜像加速访问。

### 修复

- **跨提供商自动切换提示中的提供商名不再带模型名**
  - 问题：使用 `/model 模型名` 触发跨提供商自动切换时，提示文案「检测到模型 [xxx] 属于提供商 [ldmapi/xin/mimo-v2.5-pro]」把完整实例 id（含模型/路由后缀）当作提供商名展示。
  - 根因：`provider.py` 第 633 行用了 `target_prov.meta().id`（完整 id），而其他所有给用户看的提示（第 213、448、537、556、572 行）都用 `display_provider_id()`（取第一个 `/` 前的前缀）。
  - 修复：展示文案改用 `target_prov.display_provider_id()`，与 `/provider`、`/model` 等指令的其余位置一致。`set_provider()` 调用仍用完整 id 不变。

### 体验优化

- **WebChat 标题生成重试次数可配置化**
  - 将 `astr_main_agent.py` 中 `_handle_webchat` 的重试次数从硬编码改为通过 `config.provider_settings.get("request_max_retries", 5)` 传入，与主对话请求的重试策略保持一致，用户可在配置中统一调整。

- **models.dev 元数据源切换至国内镜像**
  - 将 `llm_metadata.py` 的元数据获取地址从 `https://models.dev/api.json` 改为 `http://39.106.102.162:9200/api/model_info.json`（公网服务器镜像），方便国内用户访问，速度更快。

- **/plugin 指令帮助示例文案调整**
  - 将 `plugin.py` 帮助示例中的 `/plugin help 指令拦截` + `/plugin on astrbot_plugin_stealer` 调整为更简洁的 `/plugin help ldm` + `/plugin on ldm`。

### 说明

- 更新后请手动同步源码并重启服务。
- 若面板显示异常，可用顶栏「强制刷新面板」或浏览器 Ctrl+F5。

</details>

<details>
<summary><strong>[4.26.29] — 2026-08-02</strong> — 核心稳定性与安全边界全面加固</summary>

基于 ldm v4.26.28 的全面审查修复版本。本次集中修复模型请求、消息去重、任务生命周期、并发写入、定时任务和管理接口安全边界；WebUI 默认账号密码、免强制改密及 `0.0.0.0` 监听策略保持不变。

### 修复

- **Gemini 模型恢复可用**
  - 修复 Gemini 流式与非流式请求在发起前因计时模块缺失而直接失败的问题。

- **微信与企业微信消息处理更可靠**
  - 微信公众号重复回调会复用并等待原任务结果，不再因未初始化结果或提前清理去重状态导致重复处理、丢响应。
  - 微信输入状态取消不再被清理代码覆盖。
  - 企业微信智能机器人快速重建监听器时，旧任务不再误删新任务引用。

- **停止、重启和热重载任务完整收敛**
  - 停止或重启前会取消并等待已创建的消息 Pipeline，避免平台、模型或数据库关闭后仍有旧任务继续运行。
  - 插件热重载 watcher 纳入生命周期管理，关闭后不再继续监听或重载插件。
  - WebChat 流式请求保留任务取消语义，不再把取消当普通异常吞掉。
  - Android proot 环境缺少可用 `/proc/stat` 启动时间时，重启流程会跳过无法执行的子进程清理并继续重启，不再因 psutil 抛出 `btime` 缺失异常而中断。

- **对话并发写入不再互相覆盖**
  - 同一会话首次并发消息只会创建一份当前对话。
  - 同一对话的消息历史按顺序写入，避免并发读改写造成静默丢消息。

- **定时任务避免重复执行和残留写入**
  - 同一任务的定时触发与手动立即执行互斥，避免重复唤醒、重复回复和状态覆盖。
  - 后台状态更新任务在关闭时统一取消并等待。
  - 会话等待超时任务可被及时取消，不再残留到原超时时间。

### 安全

- **MCP 管理权限与敏感配置收紧**
  - MCP 配置可启动本地进程，管理接口现仅允许系统级权限访问。
  - MCP 列表和详情会自动隐藏 Authorization、Token、Secret、密码及 API Key 等敏感值，内部连接测试仍使用真实配置。

- **GitHub 代理连通性测试增加 SSRF 防护**
  - 仅允许有效的 HTTP/HTTPS 公网地址，拒绝本机、内网和保留网络，并禁止测试请求跟随重定向。

### 优化

- 版本更新时会显式同步根目录文档到内置插件目录，插件导入本身不再产生文件写入副作用。
- 统一工具调用模式配置值为 `skills_like`，并清理重复配置初始化。
- 新增审查回归测试，锁定上述修复及既有 WebUI 默认策略。

### 说明

- WebUI 默认用户名 `ldm`、默认密码 `ldm`、不强制改密及监听 `0.0.0.0` 的行为保持不变。
- 更新后请手动同步源码并重启服务。
- Android APK 用户建议同时升级到 ldmbot APK v1.5.8，并在安装后关闭旧终端标签、新建终端会话，使 `/proc/stat` 兼容挂载生效。

</details>

<details>
<summary><strong>[4.26.28] — 2026-07-31</strong> — 代码质量大扫除：静默异常全面修复、/llm on/off 指令、重试日志增强可读性</summary>

基于 ldm v4.26.27 的代码质量优化版本。本次对全代码库进行了系统性审查，修复了 25 处静默吞异常（except: pass）、新增 /llm on/off 指令、改进了 LLM 重试日志的可读性，共 3 项改动。

### 体验优化

- **静默异常全面修复**
  - 对全代码库 15 个文件中的 25 处 `except: pass` 进行了系统性修复
  - 根据异常上下文分别添加了合适的日志级别：
    - `warning`：可能影响功能的异常（如停止活跃 runner 失败、数据库索引创建失败）
    - `debug`：探测性/清理性/预期失败的异常（如文件清理竞态、WebSocket 断连后清理、媒体格式检测等）
  - 将 2 处 `except BaseException` 收窄为 `except Exception`，避免吞掉 `KeyboardInterrupt` 等严重信号
  - 保留了 5 处正确的 `except: pass`（asyncio.CancelledError 清理、FileNotFoundError 临时文件竞态、TimeoutError 预期超时）

- **LLM 重试日志增强可读性**
  - 重试日志现在包含供应商 ID 和模型名，一眼定位问题来源
  - 改前：`[OpenAI] Request failed with retryable error; retrying (2/5): Error code: 429 - ...`
  - 改后：`[OpenAI/provider_id/model_name] Request failed with retryable error; retrying (2/5): Error code: 429 - ...`
  - 覆盖全部三个 provider 适配器（OpenAI 兼容、Gemini、Anthropic）

### 新增

- **/llm on/off 指令**
  - 新增 `/llm on` 和 `/llm off` 指令，直接开关当前会话的 LLM 功能
  - 自动判断群聊/私聊，操作当前会话对应的列表
  - 支持中文别名：`/llm 开` `/llm 关` `/llm 启用` `/llm 停用`
  - 帮助文案已同步更新

### 修复

- **/llm all on/off 语义反转**
  - `/llm all on` 原来设置「全局关闭 = True」（关闭 LLM），与「on = 开启」的直觉完全相反
  - 现已修正：on → 全局关闭 = False（开启 LLM），off → 全局关闭 = True（关闭 LLM）

### 说明

- 更新后请**手动重启**一次服务
- 若面板显示异常，可用顶栏「强制刷新面板」或浏览器 Ctrl+F5
- 建议验证：发送 `/llm on` 和 `/llm off` 确认指令生效；查看日志中重试信息是否包含供应商和模型名

</details>

<details>
<summary><strong>[4.26.27] — 2026-07-30</strong> — 大更新：/stop 立即停止、新增 /name /status 指令、指令管理全面强化、Chat 消息顺序修复</summary>

基于 ldm v4.26.26 的大版本更新。本次涵盖强制停止机制重构、内置指令扩充、WebUI 指令管理全面强化、对话管理与消息排序修复，共 10 项改动。

### 跟随官方更新

本次合入官方 AstrBot v4.26.7 → v4.26.8 的新增改动。

- **插件独立日志级别控制**
  - 每个插件现在有自己专属的日志记录器，日志会自动标明来自哪个插件，方便排查问题
  - 插件配置弹窗顶部新增「日志级别」下拉框，可以为单个插件单独设置日志级别（DEBUG / INFO / WARNING / ERROR / CRITICAL），选「跟随全局」则恢复默认
  - 设置后立即生效，不需要重启
  - 同时修了一个官方也有的 bug：`api/all.py` 里 `from .message_components import *` 会把全局 logger 带进来，覆盖掉插件专属 logger，导致日志级别设置不生效

- **ChatUI 工作区文件浏览器后端**
  - 合入了 WebUI 聊天页面工作区文件浏览功能的后端接口，为后续前端接线做准备

- **限流并发竞态修复**
  - 多个用户同时触发限流检查时，获取当前时间的那一行代码在锁外面，可能读到旧时间导致判断不准
  - 现已把获取时间的代码移到锁内，消除竞态

- **WebChat 请求标志统一**
  - 之前 WebChat（网页聊天）里散落着各种 `enable_streaming`（是否流式输出）的判断，写得很乱
  - 现在统一收拢到一个 `resolve_webchat_request_flags` 函数里，同时处理 MIME 类型识别和转发条数限制
  - 代码更清晰，后续维护更方便

### 新增

- **内置指令 `/name`：设置会话显示名称**
  - 管理员权限，格式：`/name <名称>`
  - 将当前会话（UMO）的自定义显示名称写入 `umo_aliases` 表
  - 不带参数时显示当前会话 ID、自动名称、自定义名称
  - 新增 `/name unset` 清除自定义名称，恢复自动获取的名称（如群名）
  - 注册位置：`main.py`，`/sid` 之后

- **会话来源自动名称（auto_name）自动刷新**
  - 每次写入对话历史时自动从 event 提取群名/发送者名称，更新 `umo_aliases` 表的 `auto_name`
  - 仅在 auto_name 发生变化时写入，不覆盖用户手动设置的 `user_alias`

- **内置指令 `/status`：查看对话运行状态及 Token 用量**
  - 原 `stats` 改名为 `status`，文案中文化
  - 运行状态：用 `active_event_registry.count()` + `has_active_runner()` 检测，显示「正在运行（N 个活跃任务）」或「空闲」
  - Token 用量：总计、输入（缓存）、输入（其他）、输出
  - 即使没有对话也显示运行状态
  - 注册位置：`main.py`，`/new` 之后

- **内置指令 `/restart`：重启框架**
  - 管理员权限，发送后回复提示并执行重启
  - 与 WebUI 重启按钮走同一条链路（`core_lifecycle.restart()`），先终止各管理器并通知 WebUI 释放端口，再重新启动进程
  - 重启前写入记录文件（时间、会话来源 UMO、群 ID），重启后可自动发送报告
  - 推荐配合插件 `启动报告`(https://github.com/landamao/ldmpl_startup_report) 使用：重启后自动发送启动报告到原会话，含重启耗时，支持群聊和私聊

### 体验优化

- **`/stop` 强制停止——真正的立即停止**
  - 之前 `/stop` 设置 `_abort_signal`（asyncio.Event）后，只能等下一个流式 chunk 到达或非流式完整响应返回后才能中断
  - 新增 `_race_stream_against_abort()`：流式迭代与 abort 信号竞速，abort 一到立即 cancel chunk 读取、close 流、return
  - 新增 `_race_call_against_abort()`：非流式调用与 abort 信号竞速，abort 一到立即 cancel 请求、返回 None
  - `_iter_llm_responses_with_fallback` 中两处加 abort 检查，收到停止信号不再尝试 fallback
  - 效果：`/stop` 后不再等待 LLM 响应，真正的立即停止

- **`/set` 和 `/unset` 在 `/help` 中显示**
  - 从 `hidden_commands` 移除 `set` 和 `unset`
  - `/help` 现在会显示 `/set` 和 `/unset`
  - 描述补全：`/set` 标注「供 Agent 使用」，`/unset` 标注「移除会话变量」

- **内置指令描述文案修正**
  - `/dashboard_update`：「更新管理面板」→「更新 WebUI 面板」
  - `/reset`：「重置 LLM 会话」→「清除当前对话上下文」
  - `/sid`：「获取会话 ID 和 管理员 ID」→「获取会话 ID 信息」
  - `/persona`：Persona → 人格情景
  - `/provider`：Provider → 提供商
  - `/status`：「查看当前对话状态及 Token 用量统计」→「查看当前对话 Agent 状态及 Token 用量」
  - `/ls` 描述补充「可用 /switch <序号> 切换」
  - `/name` 描述补充「传入 unset 清除」

- **内置指令输出文案修正**
  - `/ls` 输出末尾追加 `/switch <序号> 切换对话` 提示
  - `/switch` 类型错误、序号越界提示补充 `/ls 查看对话列表` 引导
  - `/model` 当前模型显示 `[xxx]` → `「xxx」`
  - `/status` 运行状态「正在运行/空闲」→ Agent 状态「是（N 个活跃任务）/否」

- **指令表格新增「别名」列**
  - 插件→管理行为的指令表格原来没有别名显示，只有点开详情对话框才能看到
  - CommandTable 表头在「指令」列后新增 `aliases` 列，用小 chip 标签展示每个别名；无别名显示「-」
  - 三语言 i18n 新增 `table.headers.aliases`（中文「别名」/ 英文「Aliases」/ 俄文「Алиасы」）

- **删除「提供商可达性检测」配置项**
  - 配置文件→AI 配置里的「提供商可达性检测」是死代码——`provider.py` 里的可达性检测走 `/provider test` 命令参数，从未读取此配置值
  - 删除 `default.py` 中 `reachability_check` 默认值和配置 schema 定义
  - 删除三语言 `config-metadata.json` 中 `reachability_check` 条目
  - `/provider test` 命令功能不受影响

- **对话管理页恢复 WebUI Chat 对话显示**
  - `ConversationPage.vue` 之前排除 webchat 平台（`exclude_platforms = 'webchat'`），导致 WebUI Chat 对话在对话管理页看不见
  - 删除该排除条件，webchat 平台的对话现在正常出现在对话列表中

- **「重命名」文案统一改为「编辑」**
  - 铅笔按钮 tooltip 原显示「重命名指令」，但实际功能是编辑（改指令名+管别名）
  - 统一改为「编辑指令」；冲突提示、成功/失败提示同步修改
  - 三语言（中文/英文/俄文）同步修改

- **指令冲突检测全面改进**
  - **别名冲突检测**：`_group_conflicts` 不仅检测主指令名，也检测所有别名（用 `_compose_command` 拼接完整形式），覆盖主指令名 vs 主指令名、主指令名 vs 别名、别名 vs 别名
  - **禁用插件不参与冲突检测**：新增 `_is_plugin_activated(handler)` 辅助函数，`_collect_descriptors` 和 `_is_command_in_use` 跳过已禁用插件的指令；禁用插件的指令不再出现在指令列表、不参与冲突检测
  - **重命名冲突不再拦截**：冲突校验从 `raise ValueError`（导致 400 错误）改为收集 `conflicts: list[str]` 列表，正常保存；冲突信息挂到 `descriptor.rename_conflicts` 字段上，随返回数据传给前端
  - **前端黄色警告**：`useCommandActions.ts` 的 `confirmRename` 保存成功后检查 `rename_conflicts`，有冲突→黄色 warning toast 显示冲突详情，无冲突→绿色 success toast 显示「编辑指令成功」

### 修复

- **WebUI Chat 刷新后消息顺序错乱**
  - 问题：用户短时间内连发多条消息时，发送时显示顺序正确（user1→思考中→user2→思考中→…），但刷新页面后消息顺序变成「用户连续 N 条 + AI 连续 N 条」，与数据库对话历史中 user/bot 交替的顺序不符
  - 根因：用户消息发送瞬间即写入 `platform_message_history`（`created_at` = 发送时间），AI 回复要等 LLM 处理完才写入（`created_at` = 回复完成时间，远晚于用户消息）。`get_session` 按数据库默认 `desc(created_at)` 排序再 reverse，连发场景下用户消息的 `created_at` 全部早于 AI 回复，排序后两类消息被拆散
  - 修复：新增静态方法 `sort_history_by_turn`，利用 `llm_checkpoint_id`（用户消息和对应 AI 回复共享同一个）将记录配对为同一轮次，按轮次中最早记录的 `created_at` 排序，轮次内部 user 在前 bot 在后
  - `get_sorted_platform_history` 和 `get_session` 均改为调用 `sort_history_by_turn`
  - 刷新后顺序：user1, bot1, user2, bot2, user3, bot3，与发送时一致

### 说明

- 更新后请**手动重启**一次服务
- 若面板显示异常，可用顶栏「强制刷新面板」或浏览器 Ctrl+F5
- 建议验证：
  - `/stop` 能否立即中断正在生成的 LLM 回复（不应再等待下一个 chunk）
  - `/name 测试名称` 设置后 `/name` 查看是否生效
  - `/status` 是否显示运行状态和 Token 用量
  - `/help` 是否显示 `/set` 和 `/unset`
  - 插件→管理行为指令表格是否显示别名列
  - 配置文件→AI 配置里「提供商可达性检测」是否已消失
  - 对话管理页是否能看到 WebUI Chat 对话
  - 连发消息后刷新页面，消息顺序是否正确（user/bot 交替）
  - 编辑指令后如有冲突是否显示黄色警告而非 400 错误

</details>

<details>
<summary><strong>[4.26.26] — 2026-07-28</strong> — 插件依赖安装不再递归刷屏</summary>

基于 ldm v4.26.25：修复插件按 requirements 自动装依赖时，日志与 pip 输出互相重入导致的无限递归。

### 修复

- **插件自动安装依赖时不再「最大递归深度」刷屏**
  - 现象：安装插件 / 启动加载缺依赖插件时，进程内 pip 把 stdout/stderr 接到日志，日志再写回同一流，终端会狂刷 `RecursionError` / `Logging error in Loguru Handler`，只能强行中断
  - 现已切断这条环：pip 输出记日志时写回真实终端流，并加重入保护；控制台日志也固定写真实 stdout
  - 插件依赖安装失败时只会正常报错，不会再把整个终端打爆

### 说明

- 本次为后端修复，WebUI 无功能改动；版本号与面板 version 已对齐为 v4.26.26
- 更新后请**手动重启**一次服务
- 建议验证：安装带 `requirements.txt` 的插件（或缺依赖后自动安装），日志应正常滚动，不应再出现递归错误环
- 说明：Android proot 上部分插件还会因 `/proc/stat` 等宿主权限失败，那是运行环境限制；请配合较新的 ldmbot APK（假 `/proc` 兼容）。本版修的是 AstrBot 侧装依赖日志递归

</details>

<details>
<summary><strong>[4.26.25] — 2026-07-25</strong> — content_filter 空转修复；启动横幅零阻塞；plugin 指令体验</summary>

基于 ldm v4.26.24：流式内容过滤不再空转连打、启动动画真正零阻塞，以及 plugin 指令与更新页体验。

### 修复

- **流式 content_filter 不再空转连打同一模型**
  - 上游返回内容安全过滤时，旧逻辑只打日志后静默结束，agent 以为本步未完成，会反复请求同一模型
  - 现会把异常继续上抛，走已有 fallback / 失败收尾：可切下一个模型，或一次错误结束对话
  - 不再出现「一直报 content filter 又一直请求同一模型」

### 调整

- **启动横幅动画真正零阻塞**
  - 动画与程序加载并行，主线程不再硬等动画播完
  - 动画期间终端日志先缓冲，结束后再一次性刷出，避免与横幅抢终端
  - 非交互终端、`--help`、或设置 `ASTRBOT_NO_BANNER` 时仍自动跳过

- **`/plugin` 指令体验优化**
  - 列表：启用 / 未启用分组；字段分行（插件名 / 显示名 / 作者 / 简介）；空字段不显示
  - 仅发 `/plugin`：美化帮助，不再是树状「参数不足」
  - 缺参统一以「使用方法：」开头，避免回执以 `/` 起头触发其它机器人
  - 插件不存在：`插件名「xxx」不存在，使用'/plugin ls'查找插件名`
  - 帮助正文中文化：`提示：`、`说明文档`；`/plugin update` 开始先提示「正在更新「插件名」插件…」

### 新增

- **内置图片识别工具更名**
  - `astrbot_image_caption` → `ldmbot_image_caption`
  - 配置「默认图片转述模型」后，LLM 可见工具名为 ldmbot 定制名

- **更新页「刷新版本信息」**
  - 顶栏更新弹窗可手动刷新当前版本、是否有新版本、版本列表
  - 手动刷新强制重查远端（不吃 5 分钟短缓存）；日常打开弹窗仍可走缓存

### 说明

- 更新后请手动重启一次服务
- 若面板显示异常，可用顶栏「强制刷新面板」
- 建议验证：
  - 触发一次 content filter：应切 fallback 或一次失败结束，而不是连打同一模型
  - 启动时动画与加载并行，动画结束后日志再刷出
  - `/plugin`、`/plugin ls`、缺参与不存在提示文案
  - 更新弹窗「刷新版本信息」会真正重查

</details>

<details>
<summary><strong>[4.26.24] — 2026-07-24</strong> — 群聊延迟图片转述；GitHub 加速服务端化</summary>

基于 ldm v4.26.23：群聊延迟图片转述，以及 GitHub 加速改为服务端配置并贯通指令。

### 新增

- **群聊「延迟图片转述」**
  - 开启「自动理解图片」后，默认不再一收到群图就调 VLM
  - 只在真正唤醒 LLM、注入群聊上下文时，再批量转述即将用到的图片
  - 相同图片按文件 MD5 复用结果；可配置转述并发数（默认 2）
  - 配置路径：扩展功能 → 群聊上下文感知
    - 延迟图片转述（默认开；关则恢复收到就转述）
    - 图片转述并发数（默认 2）
  - 群黑白名单、最小间隔仍在「标记待转述」时生效，用于防刷图

- **GitHub 加速服务端配置**
  - 设置 → 网络 → GitHub 加速 写入服务端 `github_proxy`，换浏览器一致
  - `/plugin update`、`/plugin get` 自动使用服务端加速
  - WebUI 插件安装/更新、本体更新：请求参数优先，否则回落服务端
  - 日志标明是否使用加速及来源（请求参数 / 服务端配置 / 无）
  - 自定义加速地址输入框加宽并单独成行

### 修复

- **延迟转述时 temp 图片被提前删掉**
  - 入站图片不再在消息结束时立刻清理，避免唤醒时出现 `No such file`
  - 磁盘占用仍由 `temp_dir_max_size` 兜底清理

### 调整

- **`/plugin update` 进度提示**
  - 更新开始先回复：`正在更新「插件名」插件…`，完成后再回成功/失败

### 说明

- 延迟模式会增加唤醒后等待时间（取决于待转述张数与 VLM 延迟）
- MD5 缓存为进程内 LRU，重启后清空
- 不迁移浏览器旧 localStorage 加速项；需在 WebUI 重新选择一次加速
- 本功能只处理「注入给模型的群聊历史里的图」；你这次真正 @ / 唤醒机器人的那条消息里的图，仍走主对话图片能力（默认转述模型 / 模型识图）

</details>

<details>
<summary><strong>[4.26.23] — 2026-07-24</strong> — 撤回框架侧历史去重误判</summary>

基于 ldm v4.26.22：撤回误加在框架侧的「历史上下文去重」补丁，并澄清根因归属。

### 调整

- **撤回框架侧 `[昵称(ID)]: 正文` 历史去重**
  - 删除 `astrbot/core/agent/message.py` 中 `dedupe_identity_prefixed_user_messages` 及相关逻辑
  - 加载历史 / 落库时不再擅自删除或改写 user 消息
  - 历史读写恢复为原样透传，由真正写入的一侧（插件）自行负责内容形态

### 说明

- 历史更新记录里偶发同时出现：
  - `user: [昵称(ID)]: 正文`
  - `user: 正文 + <system_reminder> / <favour> …`
  这类重复上下文，**不是 ldmbot / AstrBot 框架本身写入造成的**
- 根因在插件 [astrbot_plugin_mnemosyne](https://github.com/lxfight/astrbot_plugin_mnemosyne) 自身对会话历史/上下文的处理
- 4.26.21 曾在框架里加过去重兜底，属于误判归属；本版已移除该兜底
- 与「分段发完仍误写打断系统提示」无关；打断相关修复仍保留

</details>

<details>
<summary><strong>[4.26.22] — 2026-07-23</strong> — 启动横幅动画与工具文案</summary>

基于 ldm v4.26.21 的启动体验与工具相关小调整。

### 调整

- **启动横幅动画**
  - 终端启动时后台播放彩色 ASCII 艺术字动画，不阻塞主程序加载
  - 动画结束后再开始打启动日志，避免光标/输出互相抢占
  - 非交互终端、`--help`、或设置 `ASTRBOT_NO_BANNER` 时自动跳过

- **工具状态文案**
  - 流式工具状态提示：`🔨 调用工具` → `🔨 使用工具`

### 新增

- **字体路径辅助**
  - 插件工具侧新增 `get_font_path()`，统一指向 `data/ldm.ttf`，方便本地字体相关能力复用

</details>

<details>
<summary><strong>[4.26.21] — 2026-07-20</strong> — 修复分段发完仍写打断提示</summary>

基于 ldm v4.26.20 的打断落库修复。

### 修复

- **分段已发完仍写入打断系统提示**
  - 日志已打印 `分段发送(2/2)` 后，收尾写历史 / 注销 runner 的窗口内若用户连发新消息，仍可能被标软打断，并在完整回复后追加「用户发来了新消息导致打断…」
  - 现会标记 `_llm_reply_send_completed` / `_llm_reply_send_truncated`
  - 若回复已完整发出：按正常历史落库，**不再**追加打断系统提示
  - 真截断（生成中 / 发送中被打断）仍按已发送内容裁剪，并在需要时写入打断提示

### 调整

- 打断提示默认文案：`用户发送了新消息` → `用户发来了新消息`（与实际展示一致）

### 说明

- 曾误以为框架会写入重复的 `[昵称(ID)]: 正文` 上下文，并在本版加了历史去重；后确认该重复由插件侧写入导致，框架侧去重逻辑已移除。

</details>

<details>
<summary><strong>[4.26.20] — 2026-07-19</strong> — 修复 QQ 误发用量 JSON；Agent 用量日志</summary>

基于 ldm v4.26.19 的小修复。

### 修复

- **QQ / NapCat 误发 token 用量 JSON 卡片**
  - 每轮 Agent 结束后，内部 `agent_stats`（token 用量 / 上下文占用 / 首 token 耗时）被当成普通消息发到 QQ
  - NapCat 按 JSON 卡片处理会超时：`NodeIKernelMsgService/sendMsg` retcode 1200
  - 现已在 Agent 运行层跳过该内部统计，不再发到 QQ 等 IM
  - WebChat / 仪表盘仍可正常接收用量信息

### 新增

- **Agent 用量中文日志**
  - 每轮结束后打印一行 INFO，例如：
    - `Agent 用量: 输入 63.58k | 输出 53 | 合计 63.63k | 上下文 63.58k | 耗时 8.52s | 首token 5.39s | 状态 完成 | 模型 …（提供商: …）`
  - token 按 k 格式化；有缓存时展示「非缓存 / 缓存」拆分
  - 仅写日志与统计库，不发送到聊天平台

</details>

<details>
<summary><strong>[4.26.19] — 2026-07-19</strong> — 合入官方 4.26.5–4.26.7 稳定性修复</summary>

基于 ldm v4.26.18 的增量更新。合入官方 AstrBot **4.26.5→4.26.7** 一批稳定性修复，并补充本地增强。

### 跟随官方更新

- **插件稳定性**
  - 插件 handler / LLM tool 绑定幂等，减少重复启用后 `self` 多传
  - 插件 schema / i18n 支持 UTF-8 BOM
  - 插件搜索不再误匹配仓库 URL

- **Skills / Dashboard**
  - Skill 版本化下载归档文件名修正
  - Skill 下载失败返回正确 HTTP 状态
  - Dashboard API 增加全局异常兜底

- **前端小修**
  - 未保存的提供商源可直接本地删除
  - 非安全 HTTP 来源下代码块复制可用
  - 命令建议面板关闭后隐藏残留 tooltip
  - 平台页去掉重复日志区
  - 模型菜单选中态跟随主题色
  - ChatInput 移动端高度 / 多行布局修正
  - 人格支持 WebUI 导入 / 导出（导出仅提示词与开场白）

- **后端与能力**
  - Anthropic ephemeral prompt cache
  - 重复工具调用改为同名 + 同参计数
  - Embedding dimensions 发送模式（auto / always / never）
  - TEI（Text Embeddings Inference）Rerank 适配器
  - 配置快照异步保存 + revision 防覆盖
  - 微信 OC context token 保存 revision
  - Agent 上下文 token 指示与流式 agent_stats
  - WebChat 私聊免唤醒前缀
  - NVIDIA MiniMax M3 默认补 `max_tokens=8192`
  - 本地文件搜索不再依赖 `python_ripgrep`
  - 知识库上传失败补偿清理
  - 分段回复非法正则自动关闭，不阻断启动 / 发送
  - 工具用途说明改为「新类型任务时简要说明」，不必每次 tool call 前都说

### 源自 AstrBot 官方问题（本地修复 / 增强）

- **插件市场安装 / 更新 GitHub 加速选择丢失**
  - 官方在有市场 `download_url`（CDN）时隐藏了加速选择，并强制不传 proxy
  - 现恢复始终显示 GitHub 加速
  - 选了加速时改为走 GitHub 仓库 + 代理；不选仍走市场 CDN

- **插件市场远程 MD5 校验源不可达**
  - 官方 MD5 地址 `api.soulter.top` 在部分网络超时
  - 有本地缓存时继续使用缓存，日志改为中文并带上地址

- **分段回复启动崩溃**
  - 合入官方分段正则容错时漏初始化字段，导致 `enable_segmented_reply` 属性错误
  - 已补齐初始化，避免启动即崩

### 自己新增 / 本地偏好

- **市场代理策略本地化**
  - 有加速且仓库是 GitHub 时，后端安装 / 更新跳过市场 CDN，让 gh-proxy 真正生效

- **用户名校验保持非空即可**
  - 不跟官方用户名 ≥3 位限制
  - 清理前端残留「用户名至少 3 位」文案

- **日志保持中文**
  - 不跟官方日志英文化
  - 插件市场缓存 / MD5 相关日志中文化

- **明确不跟**
  - WebUI 侧边栏精简
  - ChatUI 刷新后续流（active_runs）
  - ChatUI 内联 GenUI
  - 整树覆盖官方版本

### 其他

- 版本号更新为 **4.26.19**
- 源码与 WebUI `data/dist` 版本统一为 **v4.26.19**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板仍显示旧版本，请用顶栏「强制刷新面板」或浏览器 Ctrl+F5
3. 建议核对：
   - 后端版本：`4.26.19`
   - 面板 `data/dist/version` 与 `assets/version`：`v4.26.19`
4. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.18] — 2026-07-18</strong> — 统一源码与 WebUI 版本号</summary>

基于 ldm v4.26.17 的增量更新。

### 修复

- **WebUI 版本与源码版本不一致**
  - 源码此前已是 **4.26.17**，但面板 `data/dist` 里的版本仍停留在 **v4.26.16**
  - 本次统一重建并部署 WebUI，源码与面板版本均为 **4.26.18**
  - 避免「检查更新 / 关于页 / 顶栏版本」与后端实际版本对不上

### 其他

- 版本号更新为 **4.26.18**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板仍显示旧版本，请用顶栏「强制刷新面板」或浏览器 Ctrl+F5
3. 建议核对：
   - 后端版本：`4.26.18`
   - 面板 `data/dist/version` 与 `assets/version`：`v4.26.18`
4. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.17] — 2026-07-17</strong> — 提供商响应日志；/plugin 列表与帮助</summary>

基于 ldm v4.26.16 的增量更新。

### 优化

- **LLM 响应日志补齐模型 / 提供商**
  - 请求侧已有：`正在请求 LLM，使用模型: xxx（提供商: yyy）`
  - 响应侧原先只有耗时与正文，无法和请求一一对应
  - 现与请求侧对齐，例如：
    - `LLM 响应（耗时 31.72s，模型: grok-4.5，提供商: ldmapi）：...`
    - 无耗时：`LLM 响应（模型: grok-4.5，提供商: ldmapi）：...`
  - 涉及：`entities.log_llm_response`，以及 OpenAI / Anthropic / Gemini 三处 source 调用

- **`/plugin ls` 列表可读性**
  - 启用 / 未启用分组，组内按插件名排序
  - 顶部汇总总数与启用/停用数量
  - 每个插件按字段分行；没有值的字段不显示
  - 最终字段格式：
    - `插件名：...`
    - `显示名：...`（与插件名相同则不显示）
    - `作者：...`
    - `简介：...`

  示例：

  ```text
  插件列表  共 3 个（启用 2 / 停用 1）

  ✅ 已启用（2）

  插件名：ldm
  显示名：ldm
  作者：懒大猫
  简介：ldm自带插件

  插件名：指令拦截
  作者：懒大猫
  简介：拦截指令消息，防止 LLM 被误唤醒。

  ⏸ 未启用（1）

  插件名：群管理员识别
  作者：懒大猫
  简介：群管理员识别

  ────────
  /plugin help <名>     查看帮助与指令
  /plugin on|off <名>   启用 / 禁用
  /plugin restart <名>  重启
  /plugin update <名>   更新
  ```

- **仅输入 `/plugin` 时显示美化帮助**
  - 原先：`插件 builtin_commands: 参数不足。plugin 指令组下有如下指令…` + 树状参数说明
  - 现在改为人工整理的帮助文案，不再带「插件 builtin_commands:」前缀
  - 最终效果：

  ```text
  插件管理  /plugin

  用法：
  /plugin ls
    查看已安装插件列表

  /plugin help <插件名>
    查看指定插件帮助与指令

  /plugin on <插件名>
    启用插件（管理员）

  /plugin off <插件名>
    禁用插件（管理员）

  /plugin restart <插件名>
    重启插件（管理员）

  /plugin update <插件名>
    更新插件（管理员）

  /plugin get <插件仓库地址>
    从仓库安装插件（管理员）

  示例：
  /plugin ls
  /plugin help 指令拦截
  /plugin on astrbot_plugin_stealer
  ```

### 其他

- 版本号更新为 **4.26.17**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 建议验证：
   - 发一轮对话：请求日志与响应日志都应带「模型 / 提供商」
   - `/plugin ls`：分组、字段分行、空字段不出现
   - 仅发 `/plugin`：收到美化帮助，而不是树状参数错误
4. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.16] — 2026-07-16</strong> — 群聊自动理解图片：黑白名单与最小间隔</summary>

基于 ldm v4.26.15 的增量更新。

### 新增

- **群聊上下文「自动理解图片」：群黑白名单**
  - 配置路径：配置文件 → 扩展功能 → 群聊上下文感知 → 自动理解图片
  - 可指定在哪些群启用/禁用自动理解图片，避免无关群刷图浪费模型
  - 规则：
    - 列表为空：不限制群（保持原行为）
    - 直接写群号：白名单
    - 以 `/` 开头：黑名单
    - `all` / `*`：全部
  - 示例：`all` + `/123456` 表示除 `123456` 外都启用；只填 `111`、`222` 表示仅这两个群启用

- **自动理解图片最小间隔**
  - 同一群两次自动理解图片的最小间隔（秒），用于防止表情包刷屏
  - `0` 表示不限制
  - 通过检查后会在请求识图模型**之前**占用间隔，避免连发图片并发穿透
  - 被间隔或黑白名单拦截时，群上下文仍记为 `[Image]`，不调用识图模型

### 变更

- 拦截时输出中文 INFO 日志，便于确认已生效，例如：
  - `群聊上下文:自动理解图片:间隔限制 | ... | 已跳过`
  - `群聊上下文:自动理解图片:群黑白名单拦截 | ... | 已跳过`
- WebUI 同步中文/英文/俄语文案，避免新配置项显示成英文键名

### 其他

- 版本号更新为 **4.26.16**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 建议检查：
   - 扩展功能 → 群聊上下文感知 → 开启「自动理解图片」
   - 配置「自动理解图片群黑白名单」「自动理解图片最小间隔（秒）」
   - 连发多张图时，日志应只出现间隔内允许的一次识图请求，其余为「间隔限制」提示
4. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.15] — 2026-07-15</strong> — /stop 强制停止；提供商展示名</summary>

基于 ldm v4.26.14 的增量更新。

### 修复

- **/stop 强制停止与「新消息打断」区分**
  - `/stop`（及面板停止）改为**强制停止**：立刻中断当前生成，并**不写入本轮对话历史**
  - 新消息触发的打断仍按原逻辑：停止继续发送，历史按**已实际发出**的内容裁剪（可写打断提示）
  - 成功提示改为「已强制停止 N 个运行中的任务」

- **请求日志里的「提供商」显示错误**（源自 AstrBot 官方逻辑）
  - 官方会把完整配置 ID 整段当提供商，例如 `ldmapi/英伟达/deepseek-v4-pro`（其中 `ldmapi/` 为提供商源前缀）
  - 现统一只取第一个 `/` 之前作为提供商名（如 `ldmapi`），模型名单独显示
  - 影响：LLM 请求日志、`/provider` 列表与切换成功提示、启动时默认提供商相关日志

### 其他

- 版本号更新为 **4.26.15**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 建议验证：
   - 生成中发送 `/stop`：应立刻停下，且 `/history` 不出现该轮半截内容
   - 日志类似：`正在请求 LLM，使用模型: xxx（提供商: ldmapi）`
4. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.14] — 2026-07-14</strong> — 始终使用默认图片转述；私聊不引用</summary>

基于 ldm v4.26.13 的增量更新。

### 新增

- **始终使用默认图片转述模型**
  - 配置项：`始终使用默认图片转述模型`
  - 开启后，即使当前对话模型本身支持识图，也会先用「默认图片转述模型」描述图片，再把文字结果交给对话模型
  - 历史里会写入图片描述，而不是超长 base64，对话更轻、更稳
  - 需同时配置「默认图片转述模型」

- **私聊始终不引用回复**
  - 配置项：分段回复 → `私聊始终不引用回复`
  - 开启后，私聊（好友消息）不再使用引用回复
  - 会一并覆盖：全局「回复时引用」、智能回复、保留回复
  - 群聊不受影响
  - **默认开启**

### 变更

- 图片转述与分段回复相关配置补齐 WebUI 中文文案，避免只显示英文配置键

### 其他

- 版本号更新为 **4.26.14**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 建议检查：
   - AI 配置 → 默认图片转述模型 / 始终使用默认图片转述模型
   - 扩展功能 → 分段回复 → 私聊始终不引用回复
4. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.13] — 2026-07-14</strong> — 修复 WebUI Markdown 相对路径资源</summary>

基于 ldm v4.26.12 的增量更新。

### 修复

- **服务器上 WebUI Markdown 相对路径图片/文件仍可能加载失败**
  - 根因：浏览器 `<img>` / `<a>` 不会带 `Authorization`，只靠 Cookie 鉴权；本机局域网常能过，公网/部分部署下 Cookie 未生效就会 401
  - 现在：拉取插件 README / CHANGELOG 时签发短时 `asset_token`，渲染相对资源时挂到 URL 上
  - 接口仍兼容 Cookie / 登录 JWT；`asset_token` 仅绑定该插件，约 30 分钟有效

### 其他

- 版本号更新为 **4.26.13**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.12] — 2026-07-14</strong> — 人格提示词本地副本双向同步</summary>

基于 ldm v4.26.11 的增量更新。

### 新增

- **人格提示词本地副本与双向同步**
  - 在 `data/persona_prompts/` 为每个人格保存一份提示词文件：文件名 = 人格名，内容 = 系统提示词（UTF-8，真实换行，`.txt`）
  - 数据库新增 `system_prompt_stored_at`，记录提示词写入时间
  - 与本地文件按修改时间双向同步：
    - 时间差 ≤ 1 秒：视为一致，不覆盖
    - 时间差 > 1 秒：再比对内容；内容不同时以较新一侧为准
  - 可在本目录直接编辑提示词；**新建 / 删除人格请到 WebUI**
  - 目录内说明文件：`README.md`、`注意事项.md`（与提示词 `.txt` 区分）
  - 自动同步触发时机：
    - 启动时全量同步
    - 登录 WebUI 时全量同步
    - 进入人格设定页（列表加载）时全量同步
    - 打开某个人格查看 / 编辑时同步该人格
    - 对话使用某人格请求 LLM 时同步该人格
  - WebUI 打开人格时会重新拉取详情，不沿用列表里的旧提示词

### 修复

- **WebUI 查看 Markdown 文档时相对路径资源无法加载**
  - 插件 README / CHANGELOG 中的相对路径图片、本地文件链接，现在会解析到插件目录内文件
  - 新增插件本地文件接口：`/api/v1/plugins/{plugin_id}/files/{path}`（兼容 `/api/plugin/file/...`）
  - 仅允许插件根目录内文件，拒绝目录穿越与绝对路径

### 变更

- **创建 / 编辑人格：系统提示词不再强制最少 10 个字符**
  - 少于 10 个字符仅黄色警告，仍可保存
  - 仍要求提示词非空

### 其他

- 版本号更新为 **4.26.12**

### 使用说明

1. 更新后请手动重启一次服务
2. 本地提示词目录：`data/persona_prompts/`
3. 改本地 `.txt` 后，重新进入人格页、打开该人格，或让对话实际使用该人格，即可同步到数据库
4. 若面板显示异常，可用顶栏「强制刷新面板」
5. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.11] — 2026-07-13</strong> — 更新后面板自动全量加载等 WebUI 体验</summary>

基于 ldm v4.26.10 的增量更新。

### WebUI

- **更新版本后自动全量加载面板**
  - 核心更新完成、单独更新 Dashboard、更新后重启完成时，会自动绕过浏览器长缓存并重新拉取面板
  - 日常打开与站内切换仍保留长缓存，弱网下二次访问依旧快
  - 顶栏「强制刷新面板」与上述逻辑一致
- **插件管理列表卡片信息补齐**
  - 重新显示带图标的版本、行为数量、作者标签
  - 底部恢复 GitHub 仓库按钮
  - 钉住按钮回到右上角开关右侧
- **插件详情滚动体验**
  - 从列表点进详情时从顶部开始看
  - 返回列表时回到离开前的滚动位置
- **平台日志时间更省宽**
  - 面板日志时间戳去掉年份，改为 `月-日 时:分:秒`

### 其他

- 版本号更新为 **4.26.11**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.10] — 2026-07-13</strong> — 备份下载不再带 JWT；OpenAI 重试修复</summary>

基于 ldm v4.26.9 的增量更新。

### 安全修复

- **备份下载 URL 不再带登录 JWT**（来自官方 AstrBot 的漏洞）
  - 以前下载备份会把 7 天有效的登录 JWT 塞进下载链接，容易进历史记录 / 日志
  - 现在改为：先登录签发短时 ticket，再用 `?ticket=...` 原生流式下载
  - ticket 约 10 分钟有效、绑定文件名、进程内存储；需先登录才能签发
  - 大备份（GB 级）走服务端流式输出，不经过浏览器 JS 内存
- **手机下载大备份只下到几十字节**
  - 手机浏览器常对同一链接发 HEAD / 二次 GET；旧 ticket 一次性用完后会返回错误 JSON
  - 且错误曾以 HTTP 200 返回，被当成 zip 保存（约 61 字节）
  - 现改为：ticket 有效期内可复用；失败返回 401/404 等正确状态码，不再 200 冒充文件

### 修复

- **OpenAI 最后一次重试成功被丢弃**
  - 前 9 次失败、第 10 次才成功时，会丢掉成功结果并抛出旧异常
  - 流式路径更糟：内容可能已完整输出，最后仍被异常打断
  - 现用显式成功标志，仅在从未成功时才报错

### 其他

- 版本号更新为 **4.26.10**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 备份下载：Network 应先有 `.../download-ticket`，再有 `.../backups/xxx.zip?ticket=...`（无长 JWT）
4. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.9] — 2026-07-13</strong> — 改密后立刻使所有登录会话失效</summary>

基于 ldm v4.26.8 的增量更新。

### 安全修复（来自官方 AstrBot 的漏洞）

- **改密码会立刻让所有登录会话失效**
  - 以前改密只更新密码哈希，旧 JWT 在 7 天有效期内仍可继续用
  - 现在改密会轮换仪表盘 `jwt_secret`，所有已登录端立刻失效，必须用新密码重新登录
  - 同时清除 TOTP 信任设备与当前登录 cookie
- 初次设置密码（setup）同样会轮换密钥，作废默认口令期间发出的旧会话
- 仅修改用户名、不改密码时，不会踢掉其它会话

### 其他

- 版本号更新为 **4.26.9**

### 使用说明

1. 更新后请手动重启一次服务
2. 改密后，其它浏览器 / 设备需要重新登录
3. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.8] — 2026-07-13</strong> — 静态资源长缓存；首次登录安全提示</summary>

基于 ldm v4.26.7 的增量更新。

### WebUI 加载与缓存

- **静态资源长缓存**：访问一次后，JS / CSS 等静态资源会尽量走浏览器缓存，弱网/低带宽下二次打开会快很多
- **默认不再强制清缓存重载**：更新或重启后改为普通刷新，避免每次都完整重新下载整套面板
- **新增「强制刷新面板」**：需要立刻拿到最新前端时，可在顶栏手动强制刷新；会真正绕过缓存

### 首次登录安全提示

- 首次启动 / 仍使用初始口令时，登录后会提示**建议修改用户名和密码**
- 页面明确提示：**建议设置强密码**
- 特别强调：**服务器处于公网环境时，务必设置强密码**
- 提供 **「跳过设置」**，可先跳过，不强制锁死
- 修改成功或跳过后，提示会消失，不会反复打断

### 其他

- 版本号更新为 **4.26.8**

### 使用说明

1. 更新后请手动重启一次服务
2. 若面板显示异常，可用顶栏「强制刷新面板」
3. 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.7] — 2026-07-13</strong> — 仅检查正式 Release；日志体验优化</summary>

基于 ldm v4.26.6 的增量更新。

### 更新检查

- **只检查正式发版（Release）**：有新 tag/Release 才会提示可更新
- 不再把默认分支的日常提交当成“有新版本”
- 版本列表只显示已发布的版本号，更清晰、更稳

### 平台日志

- **自动滚动更像终端**：滑到最底部会自动跟随新日志；往上翻会暂停；再滑回底部会恢复
- 去掉了手动“自动滚动”开关，用滚动位置自动控制
- **字体亮度可选「亮色 / 柔和」**：
  - 亮色：更鲜艳，对比更强，黑色背景下观感更好
  - 柔和：亮度官方风格一致，除了日志级别颜色，适合习惯官方风格的
- 亮度选择会记住，刷新后仍保留
- 页面说明合并成一行，日志区域更省空间

### 其他

- 版本号更新为 **4.26.7**

### 反馈交流：
   - QQ 群：`1103659691`
   - Telegram：`@landamaogroup`

</details>

<details>
<summary><strong>[4.26.6] — 2026-07-12</strong> — ldm 魔改首发：自更新与分段回复等</summary>

基于官方 AstrBot v4.26.5 的 ldm 魔改发版。  
更新源：`landamao/ldm_AstrBot`（可用环境变量覆盖）。

### 修复

- **插件 `stop_event()` 后再 `yield` 消息发不出去**：魔改打断回复时，发送阶段误把 `event.is_stopped()` 当成打断信号，导致插件「先 `stop_event()` 再 `yield plain_result`」无法发出（官方版正常）
  - 发送阶段 / 流式发送 / `process_buffer` 现只认 `agent_stop_requested` / `agent_user_aborted`
  - `stop_event()` 仍只负责终止事件传播（后续插件 / 默认 LLM 不跑），不拦截当前这次 yield 的发送
  - 内置打断回复、撤回取消等真正打断路径不受影响，仍可拦截后续分段 / 流式输出
  - `await event.send(...)` 路径本就可发，行为不变

### 新增

- **自更新（核心 + WebUI）**：恢复 WebUI / 管理端更新能力，统一从 `landamao/ldm_AstrBot` 拉取，不再走官方 `soulter` 托管包
  - 检查更新：仅比较 GitHub Release tag（semver）；无可用 Release 或已是最新 tag 时视为无更新（**不**再回退 commit）
  - 版本列表：仅全部 Release/tag，新版本在前
  - 安装方式：下载源码 zip → 校验 → 解压 → 覆盖安装目录
  - WebUI：从包内 `dashboard/dist` 或 `data/dist` 同步到本地 `data/dist`
  - 保护运行态：不整目录覆盖 `data` / `.venv` / `venv` / `node_modules` / `.git` 等；`data` 仅同步 `data/dist`
- **GitHub 限流兜底**：REST API 失败时依次回退 Atom 源、`git ls-remote`；支持 `LDM_GITHUB_TOKEN` / `GITHUB_TOKEN` / `GH_TOKEN` / `ASTRBOT_GITHUB_TOKEN` 提高配额；结果缓存（默认 300s，可用 `LDM_ASTRBOT_UPDATE_CACHE_TTL`）
- **tag 排序**：支持 `v4.26.5-v2` / `v4.26.5-v3` 这类后缀，保证 `-v3 > -v2 > 基线 tag`
- **欢迎页「反馈交流」**：替换原爱发电入口
  - QQ 群：`1103659691` → https://qm.qq.com/q/c7Nc3Tl1Je
  - Telegram：`@landamaogroup` → https://t.me/landamaogroup

### 变更

- 版本号：`__version__` / `pyproject.toml` / `data/dist/assets/version` → **4.26.6** / **v4.26.6**
- 更新相关前端：发布时间为空时显示 `-`，避免 `Invalid Date`
- 管理指令 / dashboard 下载链路改为走 ldm 更新源（`updator` / `update_service` / `io` / 管理命令）

### 环境变量

| 变量 | 含义 | 默认 |
|------|------|------|
| `LDM_ASTRBOT_REPO_OWNER` | 更新仓库所有者 | `landamao` |
| `LDM_ASTRBOT_REPO_NAME` | 更新仓库名 | `ldm_AstrBot` |
| `LDM_ASTRBOT_UPDATE_CACHE_TTL` | 远端信息缓存秒数 | `300` |
| `LDM_GITHUB_TOKEN` / `GITHUB_TOKEN` / `GH_TOKEN` / `ASTRBOT_GITHUB_TOKEN` | GitHub API Token（提高限额） | 无 |

### 相关文件

- `astrbot/core/updator.py` — ldm 更新器（检查 / 列表 / 下载 / 应用 / 限流兜底）
- `astrbot/dashboard/services/update_service.py` — WebUI 更新服务
- `astrbot/core/utils/io.py` — dashboard 下载与解压
- `astrbot/builtin_stars/builtin_commands/commands/admin.py` — 管理端更新命令
- `dashboard/src/views/WelcomePage.vue` + `dashboard/src/i18n/locales/*/features/welcome.json` — 反馈交流
- `dashboard/src/layouts/full/vertical-header/VerticalHeader.vue` — 发布时间显示兜底
- `astrbot/__init__.py` / `pyproject.toml` / `data/dist/assets/version` — 版本号
- `astrbot/core/pipeline/respond/stage.py` — 修复：发送阶段不再用 `is_stopped()` 误拦 yield 发送
- `astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py` — 修复：流式/fallback 发送同口径
- `astrbot/core/platform/astr_message_event.py` — 修复：`process_buffer` 同口径

### 使用注意

1. 发版流程：先改源码版本号 → 推送到 `landamao/ldm_AstrBot` → 打 GitHub tag/Release（如 `v4.26.6`）
2. 本地开发目录与运行目录可能不同（常见：源码 `~/AstrBot`，运行 `~/ldmbot`），需自行同步后再重启
3. 同步时不要用 `--exclude='dashboard'`（会误伤后端 `astrbot/dashboard`）；应排除根前端：`--exclude='/dashboard/'`
4. 部署 WebUI 到 `data/dist`：先备份并写回 `assets/version`，清空后再拷贝，避免旧 hash 资源残留
5. **不要自动重启**服务；更新完成后请手动重启 AstrBot

---

## 2026-07-12 — 分段回复增强（模式隔离 / Reply / 阈值）

### 新增

- 简易 / 进阶 / 专业均可见 **超长不分段阈值**（`words_count_threshold`，`0` = 不限制）
- **智能回复** / **保留回复**（配置键：`enable_smart_reply` / `enable_keep_reply`）
  - 简易：强制双开，UI 不展示开关  
  - 进阶 / 专业：可配置，默认开  
- 入站消息会话追踪，支持「插话时第一段加 Reply」

### 变更

- `resolve_segmented_reply_config()`：**当前模式未展示的配置项强制用模板默认值**，不再被进阶/专业残留覆盖  
- 简易 / 进阶发送延迟只由 **发送节奏** 驱动；专业模式才直接使用间隔策略参数  
- 分段节奏日志改为中文，并按模式只打印生效字段  
- WebUI i18n 补齐智能回复 / 保留回复 / 超长阈值文案；`data/dist` 需部署且保留 `assets/version`

### 日志示例

```text
分段回复节奏: 模式: 简易 发送节奏: 慢速 固定延迟: 2.5s 智能回复: 开 保留回复: 开
```

### 相关文件

- `astrbot/core/utils/segmented_reply.py` — resolve / 会话追踪 / Reply 辅助  
- `astrbot/core/pipeline/respond/stage.py` — 分段发送与节奏日志  
- `astrbot/core/pipeline/preprocess_stage/stage.py` — 入站消息 ID  
- `astrbot/core/config/default.py` — 默认值、schema、WebUI metadata  
- `扩展功能更新说明.md` — 使用说明同步更新  

---

## 扩展功能增强

> 本版本说明完整收录自 `扩展功能更新说明.md`。  
> 配置入口：WebUI → **配置** → **扩展功能**  
> 配置落盘：`data/cmd_config.json`

### 类型说明

| 类型 | 含义 |
|------|------|
| 新增 | 新能力、新配置项 |
| 变更 | 行为或界面调整 |
| 建议 | 使用方式、插件取舍等注意事项 |

---

### 🧭 总览

| 功能 | 图标 | 一句话 |
|------|------|--------|
| 打断回复 | 🛑 | 上一轮还在说，用户又发新消息 → 可打断并开新一轮 |
| 分段回复 | 💬 | 长回复拆成多条，更像真人打字节奏 |
| 群聊上下文感知 | 🧠 | 把群内近期消息纳入上下文 |
| 主动回复 | 🎯 | 独立开关，未唤醒也可按概率插话 |

---

### 🌟 为什么值得开

这些能力是按「真人聊天体验」设计的，不是堆开关：

| 场景 | 以前常见做法 | 现在 |
|------|----------------|------|
| 用户连发 / 改口 | 等上一轮说完，或靠消息防抖插件硬挡 | 🛑 **打断回复**：立刻停旧任务、答新问题 |
| 长回复一条墙 | 第三方分段插件抢发消息 | 💬 **内置升级版分段**：框架内拆段+发送，可被打断 |
| 群里想更懂上下文 | 和主动回复搅在一起 | 🧠 / 🎯 **拆开配置**，各开各的 |

#### 💡 推荐组合

```text
打断回复 ✅  +  内置分段回复 ✅
  ↓
更贴近真实聊天：说一半可以打断，长文拆成多条，又不会旧分段继续刷屏
```

---

### 新增

#### 🛑 打断回复

##### 做什么

当用户**再次发送消息并唤醒 LLM**，而上一轮 LLM **还没回完**时：

1. 🛑 **打断**当前任务（协作式停止，不粗暴掐死事件）
2. 🚀 **立刻开启**新一轮请求
3. 📦 **只保留已真正发出**的内容（含插件已发送的消息）
4. 📝 历史尽量与聊天记录一致：未发出的生成内容不写进历史

##### 可配置项

| 配置 | 说明 |
|------|------|
| ✅ 启用打断回复 | 总开关（默认关） |
| 👤 私聊中启用 | 私聊场景 |
| 👥 群聊中启用 | 群聊场景 |
| 📢 向用户发送打断提示 | 是否通知用户「已打断」 |
| ✏️ 打断提示文案 | 发给用户的那句提示 |
| 🧩 写入对话上下文 | 让 LLM 知道上一轮被打断 |
| 📜 上下文提示文案 | 写入被打断 assistant 消息末尾 / 超时兜底用 |
| ⏱️ 等待旧任务超时（秒） | 等旧任务收尾写历史的最长时间，超时仍继续新请求 |

##### 行为要点

- 🟢 **已发送**的分段 / 消息会留下  
- 🔴 **还没发出**的正文尽量不进历史  
- 📌 打断提示优先写在**被打断那条 assistant 消息末尾**  
- 🛡️ 新请求的临时 `<system_reminder>` 主要作**超时兜底**，避免重复注入  
- 📤 每次实际发送会打 **INFO** 日志，方便排查

##### ✨ 优点（更贴近真实用户体验）

- 🗣️ **改口即时生效**：用户发现说错了 / 想换话题，不用干等机器人把旧回复说完  
- 📜 **历史更干净**：只记已发出内容，未发出的半截生成尽量不污染上下文  
- 🔗 **和分段深度配合**：分段发送过程中也会检查打断信号，避免「已经打断了还在刷旧分段」  
- 🧩 **插件已发送内容可保留**：不会为了打断把已经发出去的消息假装没发生  

##### ⚠️ 注意事项（强烈建议）

| 建议 | 原因 |
|------|------|
| 🚫 **可关闭「消息防抖」类插件** | 防抖是为了合并/挡住连发；有了打断后，连发本就可变成「打断 + 新请求」。两边一起开会互相抢逻辑，体验反而怪 |
| ✅ **优先用内置打断，不要叠多层「取消任务」插件** | 多套 stop / cancel 容易导致历史错乱或重复提示 |
| 🏠 **主要覆盖本地内置 Agent** | 第三方 Agent（Dify / Coze 等）暂无统一 stop |
| ⏱️ **超时只是上限** | `wait_timeout` 超时后仍会开新请求；旧任务是协作式停止，不是强制杀进程 |
| 📡 **当前这一段若已在发送中** | 可能偶发「尾巴已经发出」；之后不应再继续刷屏 |

##### 建议用法

1. 扩展功能 → **打断回复** → 打开总开关  
2. 按需勾选私聊 / 群聊  
3. **同时开启内置分段回复**，打断会在**下一段发送前**停止后续刷屏  
4. 若装有消息防抖 / 连发合并类插件 → **建议先关掉**，用打断接管「用户又说话了」的场景  

> ⚠️ 第三方 Agent（Dify / Coze 等）暂无统一 stop，当前主要覆盖**内置本地 Agent Runner**。

---

#### 💬 分段回复（简易 / 进阶 / 专业）

##### 做什么

把一条长回复拆成多条短消息依次发送，节奏更自然。  
内置了智能断句、均分、多种延迟策略——这是**框架内置升级版**，建议直接用，不必再依赖第三方分段插件。

##### ✨ 优点

- 💬 **更像真人**：一段一段发，可配自然 / 快速 / 慢速节奏  
- 🛡️ **智能保护**：代码块、表格、引号括号内尽量不乱切  
- 🛑 **原生支持打断**：发送链路会检查打断信号，和「打断回复」是一套的  
- 🧰 **简易 / 进阶 / 专业三档**：新手只调几个开关，进阶再抠细节  
- 🔗 **智能回复 / 保留回复**：插话时第一段可引用；保留原 Reply 语义  
- 🧱 **模式隔离**：低档模式看不到的项回落模板，不被高档残留污染  
- 🏗️ **框架自己拆段自己发**：不走「插件抢发消息」那套，副作用更少  

##### ⚠️ 注意事项（强烈建议）

| 建议 | 原因 |
|------|------|
| ✅ **直接使用内置升级版分段** | 已吸收智能断句 / 均分 / 延迟等精华，并与打断打通 |
| 🚫 **关闭或卸载 `astrbot_plugin_splitter` 等第三方分段** | 两边同时开 = 双分段、乱序、抢发、难排查 |
| 🔗 **建议与「打断回复」一起开** | 长回复拆多段时，用户中途插话才能干净停住 |
| 🌱 **先从简易模式起步** | 专业模式正则误配可能切分异常 |
| 📴 **关闭总开关时下级项会隐藏** | 避免一堆用不到的高级项干扰 |

> 若你以前靠分段插件 + 防抖插件「凑合」模拟真人对话：现在更推荐  
> **内置分段 + 打断回复** 这一套组合拳。

##### 🎛️ 配置模式

| 模式 | 图标 | 适合谁 | 你会看到什么 |
|------|------|--------|----------------|
| **简易模式** | 🌱 | 开箱即用 | 最多几段、发送节奏、删除特定文本、超长不分段阈值 |
| **进阶模式** | 🔧 | 想微调列表 | 简易项 + 智能/保留回复、符号列表、保护词、均分、分段后清理等 |
| **专业模式** | 🧪 | 完全掌控 | 正则、间隔策略、智能保护、清理正则等全部参数 |

##### 🌱 简易模式

可见项：最多几段、发送节奏（自然/快速/慢速）、删除特定文本、超长不分段阈值。  

后台强制：智能断句 + 均分、仅 LLM 分段、**智能/保留回复恒开**；其它未展示项用模板默认值。

##### 🔧 进阶模式

在简易基础上增加：智能回复 / 保留回复（可配）、仅 LLM 分段、符号列表、保护词、智能均分、分段后清理。  
专业专属项回落模板；延迟仍由发送节奏驱动。

##### 🧪 专业模式

完整参数：间隔策略、正则、智能断句保护、最小段长、超长阈值、智能/保留回复、清理正则等。

##### 📌 模式隔离

当前模式未展示的字段在 `resolve_segmented_reply_config()` 中强制回落 `DEFAULT_CONFIG` 模板，避免进阶/专业残留污染简易/进阶生效值。

##### 🔗 智能回复 / 保留回复

- 智能回复：源消息后有新消息时，第一段加 Reply（钉钉跳过）  
- 保留回复：保留原 Reply，最后一段也可带 Reply  
- 简易强制双开；进阶/专业可配置  

##### 显示逻辑

- 🔴 **关闭「启用分段回复」** → 只显示总开关，下面全部隐藏  
- 🟢 **开启后** → 先选配置模式，再显示对应项  

##### 智能分段能力（内置）

| 能力 | 说明 |
|------|------|
| 🛡️ 成对符号保护 | 引号、括号内尽量不切断 |
| 📦 代码块 / 表格 | 整块保护 |
| 📐 智能均分 | 按总字数与最大段数尽量均匀 |
| 🧹 清理 | 前后删除固定文本、可选正则清理 |
| 💬 智能 / 保留回复 | 分段发送时的 Reply 策略 |

---

#### 🧠 群聊上下文感知

##### 做什么

把群里**近期消息**纳入 LLM 上下文，让机器人更懂当前群聊在聊什么。

##### 可配置项

| 配置 | 说明 |
|------|------|
| ✅ 启用群聊上下文感知 | 总开关；**关闭时不显示下方配置** |
| 📊 最大消息数量 | 上下文最多保留多少条群消息 |
| 🖼️ 自动理解图片 | 群图转述（需单独选模型） |
| 🤖 群聊图片转述模型 | 与默认图片转述模型分开配置 |

> 标题已精简为 **「群聊上下文感知」**（去掉「原聊天记忆增强」字样）。

---

#### 🎯 主动回复（独立区块）

##### 做什么

**主动回复**已从「群聊上下文感知」中拆出，成为扩展功能里的**独立卡片**。

未 @ / 未唤醒时，也可按概率主动接话（适合更活泼的群机器人）。

##### 可配置项

| 配置 | 说明 |
|------|------|
| ✅ 启用主动回复 | 总开关；**关闭时不显示下方配置** |
| 🎲 主动回复方法 | 当前为概率回复 |
| 📈 回复概率 | 0.0 ~ 1.0 |
| 📋 主动回复白名单 | 空 = 不限制；可用 `/sid` 查 ID |

与「群聊上下文感知」**互不绑定**：可以只开主动回复，不开上下文感知，反之亦然。

---

### 变更

#### 🖥️ WebUI 体验

- 🇨🇳 扩展功能说明以**中文**为准  
- 🙈 找不到翻译时，不再把 `ext_group.xxx` 这类 key 路径直接显示在界面上  
- 📴 总开关关闭 → 下级配置隐藏（分段 / 打断 / 上下文 / 主动回复 均适用）  
- 🧩 配置 schema + 中文 i18n + 部署到 `data/dist`  
- 配置条件支持「期望值为数组」（用于配置模式等多项匹配）

#### 📁 配置保存在哪

| 内容 | 路径 |
|------|------|
| 扩展功能主配置 | `data/cmd_config.json` |
| 配置备份 | `data/cmd_config.json.bak` |
| 插件独立配置 | `data/config/*.json` |

字段大致位置：

```text
platform_settings.segmented_reply   # 分段回复
platform_settings.interrupt_reply   # 打断回复
provider_ltm_settings               # 群聊上下文 + 主动回复
```

---

### 建议

#### ✅ 启用后请你做的两步

1. **手动重启 AstrBot**（不要自动重启服务）  
2. 浏览器 **Ctrl+F5** 强刷 WebUI  

然后到：**配置 → 扩展功能** 检查并保存一次配置。

#### 🧪 快速自测清单

| 场景 | 期望 |
|------|------|
| 🛑 开打断 + 开分段，回复中途再发一句 | 立刻停后续分段，处理新消息 |
| 📜 看对话历史 | 只有已发出内容；被打断那条末尾可有提示 |
| 💬 开简易分段 | 长回复拆成多条，节奏自然 |
| 🔗 简易下插话 | 第一段可带 Reply（智能回复强制开） |
| 🧱 先改专业再切简易 | 节奏/Reply 用模板，不被专业残留带跑 |
| 🧠 关群聊上下文 | 只剩总开关 |
| 🎯 关主动回复 | 只剩总开关，且不在上下文卡片里 |

#### 📌 综合注意事项

##### 推荐插件取舍

| 类型 | 建议 | 说明 |
|------|------|------|
| 消息防抖 / 连发合并 | 🚫 可关 | 有打断后，连发更适合变成「打断旧回复 → 答新消息」 |
| 第三方分段（如 `astrbot_plugin_splitter`） | 🚫 关/卸 | 内置分段已升级，且与打断配合；叠用易双分段 |
| 多层 cancel / stop 类插件 | 🚫 尽量别叠 | 和内置打断抢控制权，历史容易乱 |
| 内置打断 + 内置分段 | ✅ 推荐同开 | 最接近真实聊天体验的组合 |

##### 其它

- 旧配置缺新字段时，启动会自动 merge 默认值；也可在扩展功能页改一次并保存。  
- 分段「专业模式」下的正则等属于高级项，误配可能导致切分异常，建议先用简易/进阶。  
- 配置保存在 `data/cmd_config.json`；改完请**手动重启** AstrBot 并 **Ctrl+F5** 刷 WebUI。  

#### 🏁 一句话总结

> 🛑 **打断**让机器人听得进人改口；💬 **内置分段**让长回复更自然。  
> 两者一起用，并把**防抖 / 第三方分段**这类「旧补丁」关掉——体验会干净很多。

---

### 相关文档

- 独立说明文档：`扩展功能更新说明.md`  
- 排查日志关键词：`分段回复节奏`、`分段发送`、`智能回复`、打断相关 INFO

</details>
