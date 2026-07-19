from __future__ import annotations

from typing import Any

from astrbot.api import sp

# WebUI 偏好：存全局 preferences，可备份恢复、跨浏览器共享
SIDEBAR_CUSTOMIZATION_KEY = "dashboard_sidebar_customization"
THEME_COLORS_KEY = "dashboard_theme_colors"


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

    async def get_all(self) -> dict[str, Any]:
        return {
            "sidebar": await self.get_sidebar_customization(),
            "theme_colors": await self.get_theme_colors(),
        }
