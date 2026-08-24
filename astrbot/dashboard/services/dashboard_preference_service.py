from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from astrbot.api import sp
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

# WebUI 偏好：存全局 preferences，可备份恢复、跨浏览器共享
SIDEBAR_CUSTOMIZATION_KEY = "dashboard_sidebar_customization"
THEME_COLORS_KEY = "dashboard_theme_colors"
WALLPAPER_KEY = "dashboard_wallpaper"
LOGO_KEY = "dashboard_logo"
LOGIN_WALLPAPER_KEY = "dashboard_login_wallpaper"

# 壁纸透明度范围（10-100，100=完全不透明）
WALLPAPER_OPACITY_MIN = 10
WALLPAPER_OPACITY_MAX = 100
# 壁纸透明度默认值（50=半透明，2026-08 用户要求默认 50%）
WALLPAPER_OPACITY_DEFAULT = 50
# 板块透明度范围（0-90，0=完全不透明）
PANEL_OPACITY_MIN = 0
PANEL_OPACITY_MAX = 90
# 板块透明度默认值（50=半透明，2026-08 用户要求默认 50%）
PANEL_OPACITY_DEFAULT = 50

# 壁纸图片上传
WALLPAPER_UPLOAD_DIR_NAME = "wallpapers"
WALLPAPER_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
WALLPAPER_MAX_SIZE = 10 * 1024 * 1024  # 10MB
# 上传时自动压缩：最长边超过该值则等比缩到该值内（背景图无需原始大图）
WALLPAPER_MAX_EDGE = 1920
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

    @classmethod
    def normalize_wallpaper(cls, value: object) -> dict[str, object] | None:
        """规范化壁纸设置；None 表示使用默认（无壁纸、不透明板块）。

        新结构：{landscape, portrait, mode, opacity, panelOpacity}
        - landscape/portrait: {"url": 地址} 或 None（缺省=该方向不用壁纸）
        - mode: separate（横竖各用各的）/ portrait_use_landscape（竖屏复用横屏）
          / landscape_use_portrait（横屏复用竖屏）
        - opacity: 壁纸透明度 10-100（100=完全不透明，共用）
        - panelOpacity: 界面板块透明度 0-90（0=完全不透明，共用）
        - enabled: 是否启用壁纸（默认 true；false=隐藏壁纸但保留设置）

        兼容旧结构 {url, opacity, panelOpacity}：旧 url 自动迁移为竖屏壁纸
        （portrait），桌面横屏需要壁纸时再单独设置或选择复用模式。
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
        opacity = _clamp_number(
            value.get("opacity", value.get("wallpaperOpacity")),
            WALLPAPER_OPACITY_DEFAULT,
            WALLPAPER_OPACITY_MIN,
            WALLPAPER_OPACITY_MAX,
        )
        panel_opacity = _clamp_number(
            value.get("panelOpacity", value.get("panel_opacity")),
            PANEL_OPACITY_DEFAULT,
            PANEL_OPACITY_MIN,
            PANEL_OPACITY_MAX,
        )

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
            and opacity == WALLPAPER_OPACITY_DEFAULT
            and panel_opacity == PANEL_OPACITY_DEFAULT
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
        if opacity != WALLPAPER_OPACITY_DEFAULT:
            result["opacity"] = opacity
        if panel_opacity != PANEL_OPACITY_DEFAULT:
            result["panelOpacity"] = panel_opacity
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
    def normalize_logo(value: object) -> dict[str, str] | None:
        """规范化自定义 Logo 设置；None 表示使用默认 Logo。

        结构：{url: 地址}；url 白名单与壁纸一致（http/https、data: 图片、
        本站上传的 logo 文件路径）。显式 reset/clear 视为清除。
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
            return {"url": url}
        if lowered.startswith(LOGO_FILES_URL_PREFIX):
            return {"url": url}
        return None

    async def get_logo(self) -> dict[str, str] | None:
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
