# 变更日志 — 2026-11-27 — `send_message_to_user` 来源归一化：url 支持 file:// 与本地路径

## 背景

`send_message_to_user` 的 `messages[].path` / `messages[].url` 两个字段并列且都可选，
schema 里没有任何互斥约束和格式约束，`url` 的描述只有一句
"URL for \`image\`, \`record\`, \`video\`, or \`file\` types." —— 既没写「必须 http(s)」，
也没写「本地文件请用 path」。

叠加执行层的两个问题，模型会稳定地掉进同一个坑：

- `Comp.Image/Record/Video.fromURL()` 只接受 `http(s)://`，本地路径一律抛
  `Exception("not a valid url")`，被包成
  `error: failed to build messages[0] component: not a valid url`。
  这句报错没有指出正确字段，模型原样重试，表现为「一直传错参数」。
- `type=file` 更隐蔽：`Comp.File(name=..., url=本地路径)` 在工具层完全不校验，
  返回「发送成功」，真正的失败要等到发送时 `_download_file()` 里的
  `aiohttp.NonHttpUrlClientError`，模型永远看不到。

## 改动

### 1. `astrbot/core/tools/message_tools.py`：path / url 语义等价

- 新增 `_classify_media_source()`，把两个字段的取值统一归类：

  | 取值 | 归类 | 走向 |
  | --- | --- | --- |
  | `http://` / `https://` | `url` | `fromURL()` |
  | `file://` URI（含 Windows 盘符、大小写、百分号编码） | `path` | 先 `file_uri_to_path()` 解码，再走路径解析 |
  | `base64://` / `data:...;base64,` | `base64` | `fromBase64()` |
  | 绝对路径 / 工作区相对路径 / 沙箱路径 | `path` | `_resolve_path_from_sandbox()` |

- 新增 `_strip_source_value()`：清理首尾空白与模型偶尔带上的成对引号
  （`"C:\a\b.png"` 这种）。
- 新增 `_resolve_media_source()`：按 `path` → `url` 顺序尝试，第一个可用来源胜出。
  某个字段不可用时（不存在、被权限拦截）会继续试另一个字段，
  全部失败才报错，并在报错里带上**每个字段的失败原因 + 支持的全部形式**，
  模型可以据此自我纠正，不再只看到 `not a valid url`。
- 新增 `_build_media_component()`：image / record / video 统一走
  `fromFileSystem` / `fromBase64` / `fromURL`，file 类型统一落到 `file=` 字段，
  本地路径不再静默变成 `url=`。
- `_decode_base64_payload()` / `_write_base64_to_temp()`：`file` 类型收到 base64
  时落盘到临时目录再按本地文件发送（组件本身不支持 base64 文件段），
  解码容忍换行、空格和缺失的 padding。
- 组件类型别名收敛：`text/photo/img/audio/voice/attachment/document/at` 等
  常见变体映射到规范类型；`plain` 额外接受 `content` 字段，
  `mention_user` 额外接受 `user_id` / `qq` / `id`。
- schema：`type` 补 `enum`；`url` 描述明确写出支持 `http(s)://`、`file://`、
  `base64://` 与本地路径；`path` 描述说明相对路径按会话工作区解析；
  工具描述补一句「`path` / `url` 可互换」。
- 未知类型的报错补上完整支持列表（原来漏了 `file`）。

### 2. 不改 `components.py` 的 `fromURL()`

`Image/Record/Video.fromURL()` 仍然只认 `http(s)`。归一化只发生在工具层，
因为工具层有 `_can_send_local_file()` 的白名单校验（工作区 / 临时目录，
非 admin 受限）。若放开组件层的 `fromURL`，模型或插件输出里的
`file:///C:/...` 会绕过白名单直接读到本地任意文件，属于本地文件外泄风险。

## 验证

- 新增 `tests/test_send_message_to_user_source.py`（11 个用例，`pytest` 与
  直接 `python tests/...` 均可跑通）：schema 约束、取值归类表、
  `file://` 解码（空格与盘符）、`url` 传本地路径、`path` 传 http 地址、
  file 类型落字段、`text` 作文件名、base64/data URI、缺来源的报错文案、
  类型别名与未知类型的端到端路由。
- 手工复跑改前失败的三类调用：`url=file:///...`、`url=C:\...\a.png`、
  `path=file:///...`，构造出的组件与 `path=` 本地路径完全一致
  （`file` 为 `file:///` URI，`path` 为本地绝对路径）。
- `ruff check` / `ruff format --check` 均通过。

## 行为变化

- `url` 传本地路径从「报 not a valid url」变为「正常发送」。
- `url` 传 `file://` URI 从「必然失败」变为「正常发送」。
- `type=file` 传本地路径不再是「工具层假成功、发送时静默失败」，改为正常发送。
- 字段缺失或路径不可用时的报错从一句 `not a valid url` 变为
  「哪个字段、什么原因、支持哪些形式」。
- 其它来源形式（http(s)、正常本地路径、沙箱路径）行为不变。
