from __future__ import annotations

import os
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from astrbot.api import sp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# WebUI 偏好：存全局 preferences，可备份恢复、跨浏览器共享
SIDEBAR_CUSTOMIZATION_KEY = "dashboard_sidebar_customization"
THEME_COLORS_KEY = "dashboard_theme_colors"
WALLPAPER_KEY = "dashboard_wallpaper"
LOGO_KEY = "dashboard_logo"
LOGIN_WALLPAPER_KEY = "dashboard_login_wallpaper"
# 全局 Q 弹（按压/悬停弹跳）动效开关：默认开启，关闭时才落库
MOTION_KEY = "dashboard_motion_effects"

# 板块透明度范围（0-100，0=完全不透明）
PANEL_OPACITY_MIN = 0
PANEL_OPACITY_MAX = 100
# 板块透明度默认值（50=半透明，2026-08 用户要求默认 50%）
PANEL_OPACITY_DEFAULT = 50
# 壁纸自身不透明度范围（0-100，100=壁纸完全不透明）
WALLPAPER_OPACITY_MIN = 0
WALLPAPER_OPACITY_MAX = 100
# 壁纸自身不透明度默认值（80=略微透明，2026-11 用户要求恢复壁纸透明度滑块，默认 80%）
WALLPAPER_OPACITY_DEFAULT = 80
# 窗口模糊度范围（0-60px，0=无模糊，用于弹窗/临时侧边栏 backdrop-filter）
WINDOW_BLUR_MIN = 0
WINDOW_BLUR_MAX = 41
# 窗口模糊度默认值（20px，保证弹窗半透明时下层文字不混杂）
WINDOW_BLUR_DEFAULT = 20

# 壁纸图片上传
WALLPAPER_UPLOAD_DIR_NAME = "wallpapers"
WALLPAPER_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
WALLPAPER_MAX_SIZE = 10 * 1024 * 1024  # 10MB
# 上传时自动压缩：最长边超过该值则等比缩到该值内（背景图无需原始大图）
WALLPAPER_MAX_EDGE = 1920

# 远程壁纸代理拉取（「下载当前壁纸」用：远程随机壁纸 API 每次请求换图，
# 只有让服务端把字节钉住，前端才能保存「当前看到的这一张」）
WALLPAPER_PROXY_MAX_SIZE = 32 * 1024 * 1024  # 32MB（超过只透传，不进令牌缓存）
WALLPAPER_PROXY_TIMEOUT = 20.0  # 秒
WALLPAPER_PROXY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
# 令牌缓存：前端每次取图带一个 token，服务端把这次流式透传的字节记下来。
# 浏览器用同一个 token 地址再次取图（设置页预览 / 下载）时直接回这份字节，
# 随机接口也不会再换一张。TTL 给够，够用户看完再下载。
WALLPAPER_TOKEN_TTL = 6 * 60 * 60  # 6 小时
WALLPAPER_TOKEN_MAX_ENTRIES = 8  # 最多留 8 张（最近的）
WALLPAPER_TOKEN_MAX_BYTES = 48 * 1024 * 1024  # 令牌缓存总量上限
# 图片文件头嗅探：部分壁纸 API 返回 application/octet-stream 甚至不带类型
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _sniff_image_type(content: bytes) -> str:
    """按文件头判断图片类型，判断不出来返回空串。"""
    for magic, mime in _IMAGE_MAGIC:
        if content.startswith(magic):
            return mime
    # WebP: RIFF....WEBP
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return ""


@dataclass
class _WallpaperTokenEntry:
    """某个令牌钉住的一张远程壁纸。"""

    content: bytes
    media_type: str
    saved_at: float


# 令牌 → 字节（LRU）。前端每次取图带一个 token，服务端把这次透传的字节存下来，
# 之后同一 token 的请求（设置页预览、点下载）直接回这份，随机接口也不会换图。
_token_cache: OrderedDict[str, _WallpaperTokenEntry] = OrderedDict()
_token_cache_bytes = 0

# 共享的 httpx 客户端：复用连接与 TLS 会话，避免每张壁纸都重建一次握手
# （每次新建客户端会让取图白白多花 0.3-1s）
_remote_client: httpx.AsyncClient | None = None


def _get_remote_client() -> httpx.AsyncClient:
    global _remote_client
    if _remote_client is None or _remote_client.is_closed:
        _remote_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=WALLPAPER_PROXY_TIMEOUT,
            headers={
                "User-Agent": WALLPAPER_PROXY_USER_AGENT,
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            },
        )
    return _remote_client


def _normalize_remote_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise ValueError("壁纸地址为空")
    if not text.lower().startswith(("http://", "https://")):
        raise ValueError("只支持 http/https 壁纸地址")
    return text


def _pick_media_type(declared: str, first_chunk: bytes) -> str:
    """以实际内容为准判定类型（不少壁纸 API 写 octet-stream 或 text/html）。"""
    sniffed = _sniff_image_type(first_chunk)
    if sniffed:
        return sniffed
    if declared.startswith("image/"):
        return declared
    raise ValueError(
        "该地址返回的内容不是图片"
        + (f"（Content-Type: {declared}）" if declared else "")
    )


def _drop_token(token: str) -> None:
    """从令牌缓存里删掉一项并同步总量计数。"""
    global _token_cache_bytes
    entry = _token_cache.pop(token, None)
    if entry is not None:
        _token_cache_bytes -= len(entry.content)
        if _token_cache_bytes < 0:
            _token_cache_bytes = 0

# 横屏/竖屏壁纸使用模式
WALLPAPER_MODE_SEPARATE = "separate"  # 横屏用横屏壁纸、竖屏用竖屏壁纸
WALLPAPER_MODE_PORTRAIT_USE_LANDSCAPE = "portrait_use_landscape"  # 竖屏复用横屏壁纸
WALLPAPER_MODE_LANDSCAPE_USE_PORTRAIT = "landscape_use_portrait"  # 横屏复用竖屏壁纸
WALLPAPER_MODES = (
    WALLPAPER_MODE_SEPARATE,
    WALLPAPER_MODE_PORTRAIT_USE_LANDSCAPE,
    WALLPAPER_MODE_LANDSCAPE_USE_PORTRAIT,
)
# 上传文件可访问的 URL 前缀（API 层 FileResponse 提供）
WALLPAPER_FILES_URL_PREFIX = "/api/v1/ui-preferences/wallpaper/files/"

# 自定义 Logo：顶栏/登录页小图，无需大图
LOGO_UPLOAD_DIR_NAME = "logos"
LOGO_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
LOGO_MAX_SIZE = 10 * 1024 * 1024  # 10MB
LOGO_MAX_EDGE = 512  # 最长边超过该值则等比缩到该值内（顶栏 Logo 不需要大图）
LOGO_FILES_URL_PREFIX = "/api/v1/ui-preferences/logo/files/"


def _optimize_wallpaper_image(
    content: bytes, ext: str, max_edge: int = WALLPAPER_MAX_EDGE
) -> tuple[bytes, str]:
    """用 Pillow 压缩壁纸/Logo 图片，返回 (新内容, 新扩展名)。

    规则：最长边超过 max_edge 等比缩到该值内；无透明通道转
    JPEG（quality 85），有透明通道转 WebP（quality 85）；GIF 动图、无法
    解析、压缩后反而更大的图片原样保留（扩展名不变）。
    """
    try:
        import io as _io

        from PIL import Image

        img = Image.open(_io.BytesIO(content))
        img.load()
    except Exception:
        # 无法解析的图片原样保存，由前端/浏览器兜底
        return content, ext

    # GIF 动图不压缩（保留动画）
    if ext.lower() == ".gif" and getattr(img, "is_animated", False):
        return content, ext

    # 等比缩放：最长边不超过 max_edge 参数
    largest_edge = max(img.size)
    if largest_edge > max_edge:
        ratio = max_edge / largest_edge
        new_size = (
            max(1, round(img.width * ratio)),
            max(1, round(img.height * ratio)),
        )
        img = img.resize(new_size, Image.LANCZOS)

    # 透明判定：RGBA / LA 直接算，P 调色板看是否带 transparency
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )
    buffer = _io.BytesIO()
    if has_alpha:
        out_ext = ".webp"
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        img.save(buffer, "WEBP", quality=85, method=6)
    else:
        out_ext = ".jpg"
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buffer, "JPEG", quality=85, optimize=True)
    optimized = buffer.getvalue()

    # 压缩后反而更大（如小图/已有损图）→ 保留原样
    if len(optimized) >= len(content):
        return content, ext
    return optimized, out_ext


def _clamp_number(value: object, default: int, minimum: int, maximum: int) -> int:
    """把任意值规范为 [minimum, maximum] 的整数；非法值用默认值。"""
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < minimum:
        return minimum
    if number > maximum:
        return maximum
    return number


def _as_bool(value: object, default: bool = True) -> bool:
    """把任意值规范为布尔；非法值用默认值（前端可能传 "false"/0 等）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("false", "0", "off", "no", "disabled"):
            return False
        if text in ("true", "1", "on", "yes", "enabled"):
            return True
    return default


def _normalize_version(value: object) -> int:
    """版本号规范化：只接受正整数（毫秒时间戳），其余按「无版本号」处理。

    版本号用于「图片地址没变但内容换了」时给展示地址附加参数破浏览器缓存。
    """
    if isinstance(value, bool) or value is None:
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _clean_wallpaper_url(value: object) -> str:
    """壁纸地址白名单：http/https、data: 图片，或本站上传的壁纸文件路径。"""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    lowered = text.lower()
    if not lowered:
        return ""
    if lowered.startswith(("http://", "https://", "data:image/")):
        return text
    # 本站上传的壁纸文件（uuid 文件名，访问接口另有防穿越校验）
    if lowered.startswith(WALLPAPER_FILES_URL_PREFIX):
        return text
    return ""


class DashboardPreferenceService:
    """仪表盘 UI 偏好（侧边栏顺序、主题色等）。"""

    @staticmethod
    def normalize_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                continue
            text = item.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    @classmethod
    def normalize_sidebar_customization(cls, value: object) -> dict[str, list[str]] | None:
        """规范化侧边栏定制；None 表示使用默认布局。"""
        if value is None:
            return None
        if not isinstance(value, dict):
            return None
        # 显式清空
        if value.get("reset") is True or value.get("clear") is True:
            return None
        main_items = cls.normalize_string_list(value.get("mainItems", value.get("main_items")))
        more_items = cls.normalize_string_list(value.get("moreItems", value.get("more_items")))
        # 两边都空也视为默认
        if not main_items and not more_items:
            return None
        # 主区优先：同一项不重复出现在更多区
        main_set = set(main_items)
        more_items = [name for name in more_items if name not in main_set]
        return {"mainItems": main_items, "moreItems": more_items}

    @staticmethod
    def normalize_theme_colors(value: object) -> dict[str, str] | None:
        """规范化主题色；None 表示使用默认色。"""
        if value is None:
            return None
        if not isinstance(value, dict):
            return None
        if value.get("reset") is True or value.get("clear") is True:
            return None

        def _color(key: str, *aliases: str) -> str:
            for name in (key, *aliases):
                raw = value.get(name)
                if isinstance(raw, str):
                    text = raw.strip()
                    if text:
                        return text
            return ""

        primary = _color("primary", "themePrimary", "theme_primary")
        secondary = _color("secondary", "themeSecondary", "theme_secondary")
        if not primary and not secondary:
            return None
        result: dict[str, str] = {}
        if primary:
            result["primary"] = primary
        if secondary:
            result["secondary"] = secondary
        return result or None

    async def get_sidebar_customization(self) -> dict[str, list[str]] | None:
        raw = await sp.global_get(SIDEBAR_CUSTOMIZATION_KEY, None)
        return self.normalize_sidebar_customization(raw)

    async def set_sidebar_customization(self, data: object) -> dict[str, list[str]] | None:
        normalized = self.normalize_sidebar_customization(data)
        if normalized is None:
            await sp.global_remove(SIDEBAR_CUSTOMIZATION_KEY)
            return None
        await sp.global_put(SIDEBAR_CUSTOMIZATION_KEY, normalized)
        return normalized

    async def get_theme_colors(self) -> dict[str, str] | None:
        raw = await sp.global_get(THEME_COLORS_KEY, None)
        return self.normalize_theme_colors(raw)

    async def set_theme_colors(self, data: object) -> dict[str, str] | None:
        normalized = self.normalize_theme_colors(data)
        if normalized is None:
            await sp.global_remove(THEME_COLORS_KEY)
            return None
        await sp.global_put(THEME_COLORS_KEY, normalized)
        return normalized

    @staticmethod
    def normalize_motion(value: object) -> dict[str, object] | None:
        """规范化全局 Q 弹动效开关；None 表示使用默认（开启弹动）。

        结构：{enabled: false}。开启（默认值）不落库，删除记录即恢复默认；
        兼容直接传 bool、{motion: {...}} 外层已由 API 层剥开、
        以及 reset/clear 清除写法。
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return None if value else {"enabled": False}
        if not isinstance(value, dict):
            # 兼容直接传 "false"/0 这类裸值
            return None if _as_bool(value, True) else {"enabled": False}
        if value.get("reset") is True or value.get("clear") is True:
            return None
        enabled = _as_bool(value.get("enabled", True), True)
        return None if enabled else {"enabled": False}

    async def get_motion(self) -> dict[str, object] | None:
        raw = await sp.global_get(MOTION_KEY, None)
        return self.normalize_motion(raw)

    async def set_motion(self, data: object) -> dict[str, object] | None:
        normalized = self.normalize_motion(data)
        if normalized is None:
            await sp.global_remove(MOTION_KEY)
            return None
        await sp.global_put(MOTION_KEY, normalized)
        return normalized

    @classmethod
    def normalize_wallpaper(cls, value: object) -> dict[str, object] | None:
        """规范化壁纸设置；None 表示使用默认（无壁纸、不透明板块）。

        新结构：{landscape, portrait, mode, panelOpacity, opacity, version}
        - landscape/portrait: {"url": 地址} 或 None（缺省=该方向不用壁纸）
        - mode: separate（横竖各用各的）/ portrait_use_landscape（竖屏复用横屏）
          / landscape_use_portrait（横屏复用竖屏）
        - panelOpacity: 界面板块透明度 0-100（0=完全不透明，共用）
        - opacity: 壁纸自身不透明度 0-100（100=完全不透明，默认 80）
        - version: 版本号（毫秒时间戳，保存时刷新）：外链壁纸「换了图但地址
          没变」时用于破缓存，避免浏览器继续用旧图
        - enabled: 是否启用壁纸（默认 true；false=隐藏壁纸但保留设置）

        兼容旧结构 {url, opacity, panelOpacity}：旧 url 自动迁移为竖屏壁纸
        （portrait），旧 opacity 沿用「壁纸不透明度」语义（100=完全不透明）。
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            return None
        if value.get("reset") is True or value.get("clear") is True:
            return None
        enabled = value.get("enabled", True)
        if not isinstance(enabled, bool):
            enabled = True
        panel_opacity = _clamp_number(
            value.get("panelOpacity", value.get("panel_opacity")),
            PANEL_OPACITY_DEFAULT,
            PANEL_OPACITY_MIN,
            PANEL_OPACITY_MAX,
        )
        opacity = _clamp_number(
            value.get("opacity", value.get("wallpaper_opacity")),
            WALLPAPER_OPACITY_DEFAULT,
            WALLPAPER_OPACITY_MIN,
            WALLPAPER_OPACITY_MAX,
        )
        window_blur = _clamp_number(
            value.get("windowBlur", value.get("window_blur")),
            WINDOW_BLUR_DEFAULT,
            WINDOW_BLUR_MIN,
            WINDOW_BLUR_MAX,
        )
        version = _normalize_version(value.get("version", value.get("v")))

        def _slot(raw: object) -> dict[str, str] | None:
            """规范化单个方向的壁纸槽：{url} 或 None。"""
            if not isinstance(raw, dict):
                return None
            url = _clean_wallpaper_url(raw.get("url", raw.get("wallpaperUrl", "")))
            return {"url": url} if url else None

        landscape = _slot(value.get("landscape", value.get("landscapeUrl")))
        portrait = _slot(value.get("portrait", value.get("portraitUrl")))
        # 旧结构迁移：{url, ...} → 竖屏壁纸
        if portrait is None:
            legacy_url = _clean_wallpaper_url(
                value.get("url", value.get("wallpaperUrl", ""))
            )
            if legacy_url:
                portrait = {"url": legacy_url}

        mode = value.get("mode", WALLPAPER_MODE_SEPARATE)
        if mode not in WALLPAPER_MODES:
            mode = WALLPAPER_MODE_SEPARATE

        if (
            enabled
            and landscape is None
            and portrait is None
            and panel_opacity == PANEL_OPACITY_DEFAULT
            and opacity == WALLPAPER_OPACITY_DEFAULT
            and window_blur == WINDOW_BLUR_DEFAULT
        ):
            # 启用中且无壁纸 + 全默认透明度 = 未设置
            return None

        result: dict[str, object] = {}
        if not enabled:
            result["enabled"] = False
        if landscape is not None:
            result["landscape"] = landscape
        if portrait is not None:
            result["portrait"] = portrait
        if mode != WALLPAPER_MODE_SEPARATE:
            result["mode"] = mode
        if panel_opacity != PANEL_OPACITY_DEFAULT:
            result["panelOpacity"] = panel_opacity
        if opacity != WALLPAPER_OPACITY_DEFAULT:
            result["opacity"] = opacity
        if window_blur != WINDOW_BLUR_DEFAULT:
            result["windowBlur"] = window_blur
        if version:
            result["version"] = version
        return result or None

    async def get_wallpaper(self) -> dict[str, object] | None:
        raw = await sp.global_get(WALLPAPER_KEY, None)
        return self.normalize_wallpaper(raw)

    @staticmethod
    def get_wallpapers_dir() -> Path:
        """壁纸上传文件的存放目录（data/wallpapers）。"""
        return Path(get_astrbot_data_path()) / WALLPAPER_UPLOAD_DIR_NAME

    @classmethod
    def resolve_wallpaper_file_path(cls, url: str) -> Path | None:
        """把上传壁纸 URL 解析为磁盘路径；非上传 URL 或非法文件名返回 None。"""
        if not url.startswith(WALLPAPER_FILES_URL_PREFIX):
            return None
        filename = url[len(WALLPAPER_FILES_URL_PREFIX):].strip()
        # 只允许单段文件名，防路径穿越
        if not filename or filename != os.path.basename(filename):
            return None
        return cls.get_wallpapers_dir() / filename

    @classmethod
    def save_uploaded_wallpaper(cls, filename: str, content: bytes, compress: bool = True) -> str:
        """保存上传的壁纸图片，返回可访问 URL。

        只接受白名单扩展名、限制大小；文件名用 uuid 重命名防覆盖/穿越。
        compress=True 时自动压缩（_optimize_wallpaper_image）：最长边缩到
        1920px 内、无透明转 JPEG / 有透明转 WebP；GIF 动图原样保留。
        """
        if not isinstance(filename, str):
            raise ValueError("无效的文件名")
        ext = os.path.splitext(filename)[1].lower()
        if ext not in WALLPAPER_ALLOWED_EXTS:
            raise ValueError(f"不支持的图片格式：{ext or '未知'}")
        if not content or len(content) > WALLPAPER_MAX_SIZE:
            raise ValueError("图片大小超出限制（最大 10MB）")
        if compress:
            content, ext = _optimize_wallpaper_image(content, ext)
        upload_dir = cls.get_wallpapers_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{ext}"
        (upload_dir / name).write_bytes(content)
        return f"{WALLPAPER_FILES_URL_PREFIX}{name}"

    @staticmethod
    def _collect_wallpaper_urls(wallpaper: dict[str, object] | None) -> list[str]:
        """收集壁纸设置里横竖两个方向的 url（用于对比删除旧上传文件）。"""
        if not wallpaper:
            return []
        urls: list[str] = []
        for key in ("landscape", "portrait"):
            slot = wallpaper.get(key)
            if isinstance(slot, dict):
                url = slot.get("url")
                if isinstance(url, str) and url:
                    urls.append(url)
        return urls

    @classmethod
    def delete_uploaded_wallpaper(cls, url: str) -> None:
        """删除上传的壁纸文件（仅限 wallpapers 目录内的文件）。"""
        path = cls.resolve_wallpaper_file_path(url)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def get_cached_wallpaper_image(token: str) -> tuple[bytes, str] | None:
        """按令牌取回已钉住的壁纸字节；没有或已过期返回 None。"""
        if not token:
            return None
        entry = _token_cache.get(token)
        if entry is None:
            return None
        if time.time() - entry.saved_at > WALLPAPER_TOKEN_TTL:
            _drop_token(token)
            return None
        _token_cache.move_to_end(token)
        return entry.content, entry.media_type

    @staticmethod
    def cache_wallpaper_image(token: str, content: bytes, media_type: str) -> None:
        """把某个令牌这次透传的字节钉住（LRU + 总量上限）。"""
        global _token_cache_bytes
        if not token or not content:
            return
        if len(content) > WALLPAPER_PROXY_MAX_SIZE:
            return
        _drop_token(token)
        _token_cache[token] = _WallpaperTokenEntry(
            content=content,
            media_type=media_type,
            saved_at=time.time(),
        )
        _token_cache_bytes += len(content)
        # 先按条目数淘汰，再按总量淘汰
        while len(_token_cache) > WALLPAPER_TOKEN_MAX_ENTRIES:
            oldest = next(iter(_token_cache))
            _drop_token(oldest)
        while _token_cache_bytes > WALLPAPER_TOKEN_MAX_BYTES and len(_token_cache) > 1:
            oldest = next(iter(_token_cache))
            _drop_token(oldest)

    @classmethod
    async def open_remote_wallpaper_stream(
        cls,
        url: str,
        token: str = "",
    ) -> tuple[AsyncGenerator[bytes, None], str]:
        """打开远程壁纸的流式响应，返回 (异步字节流, content-type)。

        为什么不先收完再返回：壁纸通常几百 KB 到几 MB，先缓冲完再给浏览器
        会让首屏多等好几秒；这里边收边转发，浏览器可以边下边画。

        流读完后（且没有被中途掐断）把字节钉到 token 上，供「下载当前壁纸」
        和设置页预览取回同一张。
        """
        text = _normalize_remote_url(url)
        client = _get_remote_client()
        request = client.build_request("GET", text)
        resp = await client.send(request, stream=True)

        if resp.status_code >= 400:
            await resp.aclose()
            raise ValueError(f"壁纸地址返回 HTTP {resp.status_code}")

        declared = (resp.headers.get("content-type") or "")
        declared = declared.split(";")[0].strip().lower()
        iterator = resp.aiter_bytes(64 * 1024)
        try:
            first_chunk = await iterator.__anext__()
        except StopAsyncIteration:
            await resp.aclose()
            raise ValueError("壁纸地址返回空内容") from None

        try:
            media_type = _pick_media_type(declared, first_chunk)
        except ValueError:
            await resp.aclose()
            raise

        async def _stream() -> AsyncGenerator[bytes, None]:
            buffer = bytearray()
            completed = False
            try:
                buffer.extend(first_chunk)
                yield first_chunk
                async for chunk in iterator:
                    # 超过上限只透传不落缓存，避免把内存顶爆
                    if len(buffer) <= WALLPAPER_PROXY_MAX_SIZE:
                        buffer.extend(chunk)
                    yield chunk
                completed = True
            finally:
                await resp.aclose()
                if completed and token and buffer:
                    cls.cache_wallpaper_image(token, bytes(buffer), media_type)

        return _stream(), media_type

    @classmethod
    async def fetch_remote_wallpaper(cls, url: str) -> tuple[bytes, str]:
        """服务端拉取远程壁纸图片（一次性收完），返回 (图片字节, content-type)。

        用于不带令牌的请求；带令牌的走 open_remote_wallpaper_stream 流式透传。
        """
        text = _normalize_remote_url(url)
        client = _get_remote_client()
        chunks: list[bytes] = []
        total = 0
        async with client.stream("GET", text) as resp:
            if resp.status_code >= 400:
                raise ValueError(f"壁纸地址返回 HTTP {resp.status_code}")
            declared = (resp.headers.get("content-type") or "")
            declared = declared.split(";")[0].strip().lower()
            async for chunk in resp.aiter_bytes(64 * 1024):
                total += len(chunk)
                if total > WALLPAPER_PROXY_MAX_SIZE:
                    raise ValueError(
                        f"壁纸图片超过 {WALLPAPER_PROXY_MAX_SIZE // 1024 // 1024}MB 上限"
                    )
                chunks.append(chunk)

        content = b"".join(chunks)
        if not content:
            raise ValueError("壁纸地址返回空内容")
        return content, _pick_media_type(declared, content[:32])

    async def set_wallpaper(self, data: object) -> dict[str, object] | None:
        normalized = self.normalize_wallpaper(data)
        old = await self.get_wallpaper()
        # 换壁纸/清除时，删除旧的横竖上传文件（不误删 http 外链）
        if old:
            old_urls = self._collect_wallpaper_urls(old)
            new_urls = self._collect_wallpaper_urls(normalized)
            for old_url in old_urls:
                if old_url not in new_urls:
                    self.delete_uploaded_wallpaper(old_url)
        if normalized is None:
            await sp.global_remove(WALLPAPER_KEY)
            return None
        await sp.global_put(WALLPAPER_KEY, normalized)
        return normalized

    # ---- 自定义 Logo ----

    @staticmethod
    def normalize_logo(value: object) -> dict[str, object] | None:
        """规范化自定义 Logo 设置；None 表示使用默认 Logo。

        结构：{url: 地址, version: 版本号}
        - url：白名单与壁纸一致（http/https、data: 图片、本站上传的 logo 文件路径）
        - version：毫秒时间戳，保存时刷新；用于地址不变时强制浏览器重新拉图
        显式 reset/clear 视为清除。
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            return None
        if value.get("reset") is True or value.get("clear") is True:
            return None
        raw = value.get("url", value.get("logoUrl", ""))
        if not isinstance(raw, str):
            return None
        url = raw.strip()
        if not url:
            return None
        lowered = url.lower()
        if lowered.startswith(("http://", "https://", "data:image/")):
            pass
        elif lowered.startswith(LOGO_FILES_URL_PREFIX):
            pass
        else:
            return None
        version = _normalize_version(value.get("version", value.get("v")))
        result: dict[str, object] = {"url": url}
        if version:
            result["version"] = version
        return result

    async def get_logo(self) -> dict[str, object] | None:
        raw = await sp.global_get(LOGO_KEY, None)
        return self.normalize_logo(raw)

    @staticmethod
    def get_logos_dir() -> Path:
        """Logo 上传文件的存放目录（data/logos）。"""
        return Path(get_astrbot_data_path()) / LOGO_UPLOAD_DIR_NAME

    @classmethod
    def resolve_logo_file_path(cls, url: str) -> Path | None:
        """把上传 Logo URL 解析为磁盘路径；非上传 URL 或非法文件名返回 None。"""
        if not url.startswith(LOGO_FILES_URL_PREFIX):
            return None
        filename = url[len(LOGO_FILES_URL_PREFIX):].strip()
        # 只允许单段文件名，防路径穿越
        if not filename or filename != os.path.basename(filename):
            return None
        return cls.get_logos_dir() / filename

    @classmethod
    def save_uploaded_logo(
        cls, filename: str, content: bytes, compress: bool = True
    ) -> str:
        """保存上传的 Logo 图片，返回可访问 URL。

        复用壁纸上传的校验/压缩逻辑，最长边缩到 LOGO_MAX_EDGE（512px）内。
        """
        if not isinstance(filename, str):
            raise ValueError("无效的文件名")
        ext = os.path.splitext(filename)[1].lower()
        if ext not in LOGO_ALLOWED_EXTS:
            raise ValueError(f"不支持的图片格式：{ext or '未知'}")
        if not content or len(content) > LOGO_MAX_SIZE:
            raise ValueError("图片大小超出限制（最大 10MB）")
        if compress:
            content, ext = _optimize_wallpaper_image(content, ext, LOGO_MAX_EDGE)
        upload_dir = cls.get_logos_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)
        name = f"{uuid.uuid4().hex}{ext}"
        (upload_dir / name).write_bytes(content)
        return f"{LOGO_FILES_URL_PREFIX}{name}"

    @classmethod
    def delete_uploaded_logo(cls, url: str) -> None:
        """删除上传的 Logo 文件（仅限 logos 目录内的文件）。"""
        path = cls.resolve_logo_file_path(url)
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    async def set_logo(self, data: object) -> dict[str, str] | None:
        normalized = self.normalize_logo(data)
        old = await self.get_logo()
        # 换 Logo/清除时，删除旧的上传文件（不误删 http 外链）
        if old:
            old_url = old.get("url", "")
            new_url = normalized.get("url", "") if normalized else ""
            if old_url and old_url != new_url:
                self.delete_uploaded_logo(old_url)
        if normalized is None:
            await sp.global_remove(LOGO_KEY)
            return None
        await sp.global_put(LOGO_KEY, normalized)
        return normalized

    async def get_all(self) -> dict[str, Any]:
        return {
            "sidebar": await self.get_sidebar_customization(),
            "theme_colors": await self.get_theme_colors(),
            "wallpaper": await self.get_wallpaper(),
            "logo": await self.get_logo(),
            "login_wallpaper": await self.get_login_wallpaper(),
            "motion": await self.get_motion(),
        }

    # ---- 登录页独立壁纸 ----

    @classmethod
    def normalize_login_wallpaper(cls, value: object) -> dict[str, object] | None:
        """规范化登录页壁纸设置；None 表示未设置（使用默认渐变+光斑效果）。

        字段：{landscape, portrait, mode, cardBlur}
        - landscape/portrait: {"url": 地址} 或 None
        - mode: separate / portrait_use_landscape / landscape_use_portrait
        - cardBlur: 登录框背景模糊度 0-40（px，0=无模糊，默认24）

        与主壁纸结构一致但独立存储，不含 opacity/panelOpacity/enabled。
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            return None
        if value.get("reset") is True or value.get("clear") is True:
            return None

        card_blur = _clamp_number(
            value.get("cardBlur", value.get("card_blur", 24)),
            24,
            0,
            40,
        )

        def _slot(raw: object) -> dict[str, str] | None:
            if not isinstance(raw, dict):
                return None
            url = _clean_wallpaper_url(raw.get("url", ""))
            return {"url": url} if url else None

        landscape = _slot(value.get("landscape"))
        portrait = _slot(value.get("portrait"))

        mode = value.get("mode", WALLPAPER_MODE_SEPARATE)
        if mode not in WALLPAPER_MODES:
            mode = WALLPAPER_MODE_SEPARATE

        if (
            landscape is None
            and portrait is None
            and card_blur == 24
        ):
            return None

        result: dict[str, object] = {}
        if landscape is not None:
            result["landscape"] = landscape
        if portrait is not None:
            result["portrait"] = portrait
        if mode != WALLPAPER_MODE_SEPARATE:
            result["mode"] = mode
        if card_blur != 24:
            result["cardBlur"] = card_blur
        return result or None

    async def get_login_wallpaper(self) -> dict[str, object] | None:
        raw = await sp.global_get(LOGIN_WALLPAPER_KEY, None)
        return self.normalize_login_wallpaper(raw)

    async def set_login_wallpaper(self, data: object) -> dict[str, object] | None:
        normalized = self.normalize_login_wallpaper(data)
        old = await self.get_login_wallpaper()
        # 换壁纸/清除时，删除旧的横竖上传文件（不误删 http 外链）
        if old:
            old_urls = self._collect_wallpaper_urls(old)
            new_urls = self._collect_wallpaper_urls(normalized)
            for old_url in old_urls:
                if old_url not in new_urls:
                    self.delete_uploaded_wallpaper(old_url)
        if normalized is None:
            await sp.global_remove(LOGIN_WALLPAPER_KEY)
            return None
        await sp.global_put(LOGIN_WALLPAPER_KEY, normalized)
        return normalized
