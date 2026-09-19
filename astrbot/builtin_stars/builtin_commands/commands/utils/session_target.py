"""人格及上下文指令共用的会话号码、别称和线程匹配。"""
from sqlalchemy import select

from astrbot.core import logger
from astrbot.core.db.po import ConversationV2


class SessionTargetResolver:
    async def _known_umos(self) -> list[str]:
        """已知 UMO 列表，与 WebUI 会话管理同源（conversations 表 distinct user_id）"""
        db = self.context.get_db()
        async with db.get_db() as session:
            result = await session.execute(select(ConversationV2.user_id).distinct())
            return sorted({str(row[0]) for row in result.fetchall() if row[0]})

    @staticmethod
    def _umo_matches(umo: str, target: str) -> bool:
        """会话ID匹配：完整 UMO / 会话ID段 / WebChat 线程的 ! 分段（不含部分数字误匹配）"""
        parts = umo.split(":", 2)
        sid = parts[2] if len(parts) >= 3 else umo
        return (
            umo == target
            or sid == target
            or target in sid.split("!")
            or sid.startswith(f"{target}!")
        )

    async def _resolve_targets(self, raw: str) -> tuple[list[str], dict]:
        """把输入解析为候选 UMO 列表与别称映射。

        支持群号/QQ号/WebChat 线程分段/完整 UMO/昵称/群名（别称仅精确相等）。
        """
        raw = (raw or "").strip()
        candidates: set[str] = set()
        aliases: list = []
        if raw:
            try:
                aliases = await self.context.get_db().get_umo_aliases()
            except Exception:
                logger.warning("会话定位: 读取会话别称失败", exc_info=True)
                aliases = []
            try:
                umos = await self._known_umos()
            except Exception:
                logger.warning("会话定位: 读取已知会话失败", exc_info=True)
                umos = []
            candidates = {umo for umo in umos if self._umo_matches(umo, raw)}
            umo_set = set(umos)
            for alias in aliases:
                # 昵称/群名命中也必须在 conversations 表里有对话，
                # 防止只剩昵称残留的死会话被远程换人格建活
                if (
                    raw in (alias.user_alias, alias.auto_name)
                    and alias.umo
                    and alias.umo in umo_set
                ):
                    candidates.add(alias.umo)
        alias_map = {a.umo: a for a in aliases}
        return sorted(candidates), alias_map

    @staticmethod
    def _display(umo: str, alias_map: dict) -> str:
        alias = alias_map.get(umo)
        name = ""
        if alias is not None:
            name = (alias.user_alias or alias.auto_name or "").strip()
        return f"{umo}（{name}）" if name else umo

