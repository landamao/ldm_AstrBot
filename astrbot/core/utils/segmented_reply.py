"""Built-in segmented reply utilities.

Inspired by astrbot_plugin_splitter (smart split / balanced segments / delay),
kept framework-native: no plugin self-send, no reverse-replace, no prompt injection.
"""

from __future__ import annotations

import math
import random
import re
from typing import Iterable

from astrbot.core.message.components import BaseMessageComponent, Plain, Reply


class SegmentedReplySessionTracker:
    """Track recent message IDs per conversation for smart reply.

    Inspired by astrbot_plugin_splitter: if newer messages arrived after the
    source message, prepend a Reply to the first segment.
    """

    def __init__(self, max_len: int = 200) -> None:
        from collections import defaultdict, deque

        self._queues: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=max_len))
        self._last_bot_mark: dict[str, str] = {}

    def remember_incoming(self, conversation_key: str, message_id: str | None) -> None:
        if not conversation_key or not message_id:
            return
        self._queues[conversation_key].append(str(message_id))

    def mark_bot_reply(self, conversation_key: str, base_message_id: str | None) -> None:
        if not conversation_key or not base_message_id:
            return
        mark = f"__bot_reply__{base_message_id}"
        if self._last_bot_mark.get(conversation_key) == mark:
            return
        self._queues[conversation_key].append(mark)
        self._last_bot_mark[conversation_key] = mark

    def should_add_smart_reply(
        self,
        conversation_key: str,
        message_id: str | None,
        *,
        platform_name: str = "",
    ) -> bool:
        if not conversation_key or not message_id:
            return False
        if str(platform_name or "").lower() == "dingtalk":
            return False
        queue = list(self._queues.get(conversation_key) or [])
        msg_id = str(message_id)
        if msg_id not in queue:
            return False
        idx = queue.index(msg_id)
        return (len(queue) - idx - 1) > 0


_SESSION_TRACKER = SegmentedReplySessionTracker()


def get_segmented_reply_session_tracker() -> SegmentedReplySessionTracker:
    return _SESSION_TRACKER


def has_reply_component(chain: list[BaseMessageComponent]) -> bool:
    return any(isinstance(c, Reply) for c in chain)


def prepend_reply(chain: list[BaseMessageComponent], message_id: str | None) -> None:
    if message_id and not has_reply_component(chain):
        chain.insert(0, Reply(id=str(message_id)))


def strip_reply_components(chain: list[BaseMessageComponent]) -> list[BaseMessageComponent]:
    return [c for c in chain if not isinstance(c, Reply)]



# Paired openers -> closers. Avoid cutting inside matched pairs when smart split is on.
_PAIR_MAP: dict[str, str] = {
    "\u201c": "\u201d",
    "\u300a": "\u300b",
    "\uff08": "\uff09",
    "(": ")",
    "[": "]",
    "{": "}",
    "\u2018": "\u2019",
    "\u3010": "\u3011",
    "<": ">",
}
_QUOTE_CHARS = {'"', "'", "`"}
_SECONDARY_PUNCT = re.compile(r"[，,、；;]+")
_DEFAULT_SPLIT_CHARS = ["。", "？", "！", "?", "!", "；", ";", "\n", "…"]


def build_split_pattern(
    *,
    split_mode: str,
    split_chars: Iterable[str] | None,
    regex: str | None,
) -> str:
    """Build a delimiter pattern for re.split / re.match.

    Args:
        split_mode: ``chars`` (symbol list), ``words`` (legacy alias of chars),
            or ``regex``.
        split_chars: Symbols/strings that mark a split after themselves.
        regex: Custom regex when mode is ``regex``.

    Returns:
        Regex pattern string that matches delimiters.
    """
    if split_mode == "regex":
        return regex or r"[。？！?!…\n]+"

    chars: list[str] = []
    for raw in split_chars or _DEFAULT_SPLIT_CHARS:
        if raw is None:
            continue
        text = str(raw).replace("\\n", "\n").replace("\\t", "\t")
        if text:
            chars.append(re.escape(text))
    chars.sort(key=len, reverse=True)
    if not chars:
        return r"[\n]+"
    return f"(?:{'|'.join(chars)})+"


def clean_text(text: str, rule: str | None, *, items: list[str] | None = None) -> str:
    """Apply optional cleanup: list replace first, then regex."""
    if not text:
        return text
    out = text
    if items:
        for item in items:
            if item:
                out = out.replace(str(item), "")
    if rule:
        try:
            out = re.sub(rule, "", out, flags=re.DOTALL)
        except re.error:
            pass
    return out


def trim_edge_blank_lines(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"^(?:[ \t]*\r?\n)+", "", text)
    text = re.sub(r"(?:\r?\n[ \t]*)+$", "", text)
    return text


def calculate_segment_delay(
    text: str,
    *,
    method: str = "linear",
    interval: tuple[float, float] = (1.5, 3.5),
    log_base: float = 2.6,
    linear_base: float = 0.5,
    linear_factor: float = 0.1,
    log_offset: float = 0.5,
    log_factor: float = 0.8,
    fixed_delay: float = 1.5,
) -> float:
    """Compute delay before sending the *next* segment.

    Args:
        text: Text of the upcoming segment (typing simulation).
        method: ``linear`` | ``log`` | ``random`` | ``fixed``.
    """
    length = len(text or "")
    method = (method or "linear").lower()
    if method == "fixed":
        return max(0.0, float(fixed_delay))
    if method == "random":
        lo, hi = interval
        if hi < lo:
            lo, hi = hi, lo
        return random.uniform(float(lo), float(hi))
    if method == "log":
        # Prefer new log_offset/factor style; fall back to math.log base if factor unused.
        try:
            if log_factor and log_factor > 0:
                return min(float(log_offset) + float(log_factor) * math.log(length + 1), 8.0)
            base = max(float(log_base), 1.0001)
            i = math.log(length + 1, base)
            return random.uniform(i, i + 0.5)
        except ValueError:
            return 1.0
    # linear (default, plugin-like natural typing)
    return max(0.0, float(linear_base) + length * float(linear_factor))


class SegmentedReplySplitter:
    """Smart text splitter for built-in segmented reply."""

    def __init__(
        self,
        *,
        split_mode: str = "chars",
        split_chars: list[str] | None = None,
        regex: str | None = None,
        enable_smart_split: bool = True,
        balanced_split: bool = True,
        max_segments: int = 7,
        min_segment_length: int = 10,
        balanced_ratio_min: float = 0.4,
        balanced_ratio_max: float = 0.9,
        no_split_around: list[str] | None = None,
        content_cleanup_rule: str | None = None,
        clean_before_items: list[str] | None = None,
        clean_after_items: list[str] | None = None,
        trim_edge_blank_lines: bool = True,
        max_length_to_disable: int = 0,
        min_length_to_split: int = 0,
    ) -> None:
        self.split_mode = split_mode if split_mode != "words" else "chars"
        self.split_chars = list(split_chars or _DEFAULT_SPLIT_CHARS)
        self.regex = regex or r"[。？！?!…\n]+"
        self.enable_smart_split = enable_smart_split
        self.balanced_split = balanced_split
        self.max_segments = max(0, int(max_segments or 0))
        self.min_segment_length = max(0, int(min_segment_length or 0))
        self.balanced_ratio_min = float(balanced_ratio_min)
        self.balanced_ratio_max = float(balanced_ratio_max)
        self.no_split_around = [str(w) for w in (no_split_around or []) if w]
        self.content_cleanup_rule = content_cleanup_rule or ""
        self.clean_before_items = list(clean_before_items or [])
        self.clean_after_items = list(clean_after_items or [])
        self.trim_edge = trim_edge_blank_lines
        self.max_length_to_disable = int(max_length_to_disable or 0)
        self.min_length_to_split = int(min_length_to_split or 0)
        self.pattern = build_split_pattern(
            split_mode=self.split_mode,
            split_chars=self.split_chars,
            regex=self.regex,
        )

    def should_split_text(self, text: str) -> bool:
        length = len(text or "")
        if self.min_length_to_split > 0 and length < self.min_length_to_split:
            return False
        # Legacy words_count_threshold semantics: skip split when longer than limit.
        if self.max_length_to_disable > 0 and length > self.max_length_to_disable:
            return False
        return True

    def split_plain_text(self, text: str) -> list[str]:
        """Split a single plain text into segment strings."""
        if not text:
            return []
        text = clean_text(
            text,
            None,
            items=self.clean_before_items,
        )
        if not text.strip():
            return []
        if not self.should_split_text(text):
            cleaned = clean_text(text, self.content_cleanup_rule, items=self.clean_after_items)
            cleaned = trim_edge_blank_lines(cleaned) if self.trim_edge else cleaned
            return [cleaned] if cleaned.strip() else []

        ideal = 0
        if self.balanced_split and self.max_segments > 0:
            weight = sum(1 for c in text if not c.isspace())
            ideal = max(
                math.ceil(weight / self.max_segments),
                self.min_segment_length or 1,
            )

        segments: list[str] = []
        if self.enable_smart_split:
            segments = self._split_smart(text, ideal)
        else:
            segments = self._split_simple(text)

        if self.max_segments > 0 and len(segments) > self.max_segments:
            head = segments[: self.max_segments - 1]
            tail = "".join(segments[self.max_segments - 1 :])
            segments = head + ([tail] if tail else [])

        if self.balanced_split and len(segments) >= 2:
            last = segments[-1].strip()
            if 0 < len(last) < self.min_segment_length:
                segments[-2] = segments[-2] + segments[-1]
                segments.pop()

        out: list[str] = []
        for seg in segments:
            seg = clean_text(
                seg,
                self.content_cleanup_rule,
                items=self.clean_after_items,
            )
            if self.trim_edge:
                seg = trim_edge_blank_lines(seg)
            seg = seg.strip()
            if seg:
                out.append(seg)
        return out or ([text.strip()] if text.strip() else [])

    def split_chain(self, chain: list[BaseMessageComponent]) -> list[BaseMessageComponent]:
        """Split Plain components in-place style; keep non-text components as-is."""
        new_chain: list[BaseMessageComponent] = []
        for comp in chain:
            if not isinstance(comp, Plain):
                new_chain.append(comp)
                continue
            parts = self.split_plain_text(comp.text or "")
            if not parts:
                continue
            for part in parts:
                new_chain.append(Plain(part))
        return new_chain

    def _is_protected_after(self, text: str, pos_after_delim: int) -> bool:
        if not self.no_split_around:
            return False
        n = len(text)
        scan = pos_after_delim
        while scan < n and text[scan] in " \t":
            scan += 1
        for word in self.no_split_around:
            wl = len(word)
            if scan + wl <= n and text[scan : scan + wl] == word:
                return True
        return False

    def _split_simple(self, text: str) -> list[str]:
        pattern = self.pattern
        parts = re.split(f"({pattern})", text)
        segments: list[str] = []
        tmp = ""
        i = 0
        while i < len(parts):
            p = parts[i]
            if not p:
                i += 1
                continue
            if re.fullmatch(pattern, p):
                after = ""
                for k in range(i + 1, len(parts)):
                    if parts[k]:
                        after = parts[k]
                        break
                protected = False
                after_stripped = after.lstrip(" \t")
                for word in self.no_split_around:
                    if word and after_stripped.startswith(word):
                        protected = True
                        break
                tmp += p
                if not protected:
                    segments.append(tmp)
                    tmp = ""
            else:
                tmp += p
            i += 1
        if tmp:
            segments.append(tmp)
        return segments

    def _split_smart(self, text: str, ideal: int) -> list[str]:
        stack: list[str] = []
        compiled = re.compile(self.pattern)
        i = 0
        n = len(text)
        chunk = ""
        weight = 0
        segments: list[str] = []
        ratio_min = self.balanced_ratio_min
        ratio_max = self.balanced_ratio_max

        def flush(with_delim: str = "") -> None:
            nonlocal chunk, weight
            piece = chunk + with_delim
            if piece:
                segments.append(piece)
            chunk = ""
            weight = 0

        while i < n:
            # Markdown fenced code block
            if text.startswith("```", i) and (i == 0 or text[i - 1] == "\n"):
                idx = text.find("```", i + 3)
                if idx != -1:
                    block = text[i : idx + 3]
                    chunk += block
                    weight += sum(1 for c in block if not c.isspace())
                    i = idx + 3
                    continue
                chunk += text[i:]
                break

            # <think>...</think>
            if text.startswith("<think>", i) and (i == 0 or text[i - 1] == "\n"):
                idx = text.find("</think>", i + 7)
                if idx != -1:
                    block = text[i : idx + 8]
                    chunk += block
                    weight += sum(1 for c in block if not c.isspace())
                    i = idx + 8
                    continue
                chunk += text[i:]
                break

            # Markdown table rows starting with |
            if (i == 0 or text[i - 1] == "\n") and i < n and text[i] == "|":
                table_end = i
                pos = i
                while pos < n:
                    line_end = text.find("\n", pos)
                    if line_end == -1:
                        line_end = n
                    line = text[pos:line_end].strip()
                    if line.startswith("|") or (
                        line and all(c in "-| :" for c in line)
                    ):
                        table_end = line_end + 1 if line_end < n else n
                        pos = table_end
                    else:
                        break
                if table_end > i + 1:
                    table_text = text[i:table_end]
                    chunk += table_text
                    weight += sum(1 for c in table_text if not c.isspace())
                    i = table_end
                    continue

            match = compiled.match(text, pos=i)
            if match:
                delim = match.group()
                should = False
                if not stack or "\n" in delim:
                    should = True
                    if ideal > 0 and weight < ideal * ratio_min:
                        should = False
                    if should and "\n" not in delim and re.match(
                        r"^[ \t.?!,;:\-']+$", delim
                    ):
                        p_c = text[i - 1] if i > 0 else ""
                        n_c = text[i + len(delim)] if i + len(delim) < n else ""
                        # Avoid splitting pure English sentences on .,
                        if re.match(r"^[a-zA-Z0-9 \t.?!,;:\-']$", p_c) and re.match(
                            r"^[a-zA-Z0-9 \t.?!,;:\-']$", n_c
                        ):
                            should = False
                        if should and re.match(r"^[ \t]+$", delim):
                            cjk_lat = (
                                r"[\u4e00-\u9fff\u3400-\u4dbf\uF900-\uFAFFa-zA-Z0-9]"
                            )
                            if (
                                p_c
                                and n_c
                                and re.match(cjk_lat, p_c)
                                and re.match(cjk_lat, n_c)
                            ):
                                should = False
                    if should and self._is_protected_after(text, i + len(delim)):
                        should = False
                if should:
                    flush(delim)
                    i += len(delim)
                else:
                    chunk += delim
                    weight += len(delim)
                    i += len(delim)
                continue

            # Secondary punctuation when segment already long enough
            if ideal > 0 and weight >= ideal * ratio_max and not stack:
                sec = _SECONDARY_PUNCT.match(text, pos=i)
                if sec:
                    delim = sec.group()
                    flush(delim)
                    i += len(delim)
                    continue

            char = text[i]
            if char in _QUOTE_CHARS:
                if stack and stack[-1] == char:
                    stack.pop()
                else:
                    stack.append(char)
            elif not stack and char in _PAIR_MAP:
                stack.append(char)
            elif stack and char == _PAIR_MAP.get(stack[-1]):
                stack.pop()

            chunk += char
            i += 1
            weight += 0 if char.isspace() else 1

        if chunk:
            segments.append(chunk)
        return segments


def apply_send_speed(cfg: dict, speed: str, *, template: dict | None = None) -> None:
    """Map user-facing send_speed to interval fields in-place.

    When speed is natural, linear_base/linear_factor come from template defaults
    (not leftover pro values), unless template is None (then keep cfg values).
    """
    speed = (speed or "natural").strip().lower()
    # Chinese aliases
    speed_map = {
        "自然": "natural",
        "快速": "fast",
        "慢速": "slow",
        "natural": "natural",
        "fast": "fast",
        "slow": "slow",
    }
    speed = speed_map.get(speed, "natural")
    tpl = template or {}
    if speed == "fast":
        cfg["interval_method"] = "fixed"
        cfg["fixed_delay"] = 0.3
    elif speed == "slow":
        cfg["interval_method"] = "fixed"
        cfg["fixed_delay"] = 2.5
    else:
        cfg["interval_method"] = "linear"
        cfg["linear_base"] = float(
            tpl.get("linear_base", cfg.get("linear_base", 0.5)) or 0.5
        )
        cfg["linear_factor"] = float(
            tpl.get("linear_factor", cfg.get("linear_factor", 0.08)) or 0.08
        )


# Keys editable in each WebUI mode (must stay aligned with CONFIG_METADATA_3).
# Keys not listed for the current mode are forced to DEFAULT_CONFIG template values
# so leftovers from advanced/pro never leak into simpler modes.
_SEGMENTED_REPLY_MODE_EDITABLE: dict[str, frozenset[str]] = {
    "simple": frozenset(
        {
            "enable",
            "config_mode",
            "max_segments",
            "send_speed",
            "clean_before_items",
            "words_count_threshold",
            "disable_quote_in_private",
        }
    ),
    "advanced": frozenset(
        {
            "enable",
            "config_mode",
            "max_segments",
            "send_speed",
            "clean_before_items",
            "words_count_threshold",
            "enable_smart_reply",
            "enable_keep_reply",
            "disable_quote_in_private",
            "only_llm_result",
            "split_words",
            "no_split_around",
            "balanced_split",
            "clean_after_items",
        }
    ),
    # pro: all keys from raw + template; no forced hide
    "pro": frozenset(),
}


def _segmented_reply_template() -> dict:
    """Template defaults for segmented_reply (DEFAULT_CONFIG)."""
    try:
        from astrbot.core.config.default import DEFAULT_CONFIG

        tpl = dict(
            DEFAULT_CONFIG.get("platform_settings", {}).get("segmented_reply", {})
            or {}
        )
        if tpl:
            return tpl
    except Exception:
        pass
    # Fallback if import fails (should not happen in normal runtime)
    return {
        "enable": False,
        "config_mode": "simple",
        "only_llm_result": True,
        "send_speed": "natural",
        "interval_method": "linear",
        "interval": "1.5,3.5",
        "log_base": 2.6,
        "linear_base": 0.5,
        "linear_factor": 0.08,
        "fixed_delay": 1.5,
        "words_count_threshold": 0,
        "max_length_to_disable": 0,
        "min_length_to_split": 0,
        "split_mode": "chars",
        "regex": r"[。？！?!…\n]+",
        "split_words": ["。", "？", "！", "?", "!", "；", ";", "\n", "…"],
        "content_cleanup_rule": "",
        "clean_before_items": [],
        "clean_after_items": [],
        "enable_smart_split": True,
        "balanced_split": True,
        "max_segments": 5,
        "min_segment_length": 10,
        "balanced_ratio_min": 0.4,
        "balanced_ratio_max": 0.9,
        "no_split_around": [],
        "trim_edge_blank_lines": True,
        "enable_smart_reply": True,
        "enable_keep_reply": True,
        "disable_quote_in_private": True,
    }


def resolve_segmented_reply_config(raw: dict | None) -> dict:
    """Resolve effective segmented-reply settings by config_mode.

    Modes:
        - simple: few knobs; hidden fields always use template defaults
        - advanced: list-based split/cleanup; pro-only leftovers reset to template
        - pro: full control (user values kept)

    For simple/advanced, any key not shown in the current WebUI mode is taken
    from DEFAULT_CONFIG.platform_settings.segmented_reply, never from leftover
    advanced/pro values stored in the same dict.

    Args:
        raw: platform_settings.segmented_reply dict.

    Returns:
        Normalized config dict for splitter/delay.
    """
    raw = dict(raw or {})
    template = _segmented_reply_template()
    mode = str(raw.get("config_mode", template.get("config_mode", "simple")) or "simple")
    mode = mode.strip().lower()
    mode_map = {
        "简易": "simple",
        "简易模式": "simple",
        "simple": "simple",
        "进阶": "advanced",
        "进阶模式": "advanced",
        "advanced": "advanced",
        "专业": "pro",
        "专业模式": "pro",
        "pro": "pro",
        "professional": "pro",
    }
    mode = mode_map.get(mode, "simple")

    editable = _SEGMENTED_REPLY_MODE_EDITABLE.get(mode, frozenset())

    def pick(key: str, *, cast=None, default=None):
        """Read raw only if key is editable in this mode; else template."""
        if mode == "pro" or key in editable:
            if key in raw:
                val = raw[key]
            elif default is not None:
                val = default
            else:
                val = template.get(key)
        else:
            val = template.get(key) if default is None else template.get(key, default)
            if val is None and default is not None:
                val = default
        if cast is bool:
            return bool(val)
        if cast is int:
            try:
                return int(val or 0)
            except (TypeError, ValueError):
                return int(default or 0)
        if cast is float:
            try:
                return float(val if val is not None else (default or 0.0))
            except (TypeError, ValueError):
                return float(default or 0.0)
        if cast is list:
            return list(val or [])
        if cast is str:
            if val is None:
                return str(default or "")
            return str(val)
        return val

    default_chars = list(
        template.get("split_words")
        or ["。", "？", "！", "?", "!", "；", ";", "\n", "…"]
    )

    out = {
        "enable": pick("enable", cast=bool, default=False),
        "only_llm_result": pick("only_llm_result", cast=bool, default=True),
        "config_mode": mode,
        "interval_method": pick("interval_method", cast=str, default="linear"),
        "interval": pick("interval", cast=str, default="1.5,3.5"),
        "log_base": pick("log_base", cast=float, default=2.6),
        "linear_base": pick("linear_base", cast=float, default=0.5),
        "linear_factor": pick("linear_factor", cast=float, default=0.08),
        "fixed_delay": pick("fixed_delay", cast=float, default=1.5),
        "words_count_threshold": pick("words_count_threshold", cast=int, default=0),
        "max_length_to_disable": pick("max_length_to_disable", cast=int, default=0),
        "min_length_to_split": pick("min_length_to_split", cast=int, default=0),
        "split_mode": pick("split_mode", cast=str, default="chars"),
        "regex": pick("regex", cast=str, default=r"[。？！?!…\n]+"),
        "split_words": pick("split_words", cast=list, default=default_chars)
        or list(default_chars),
        "content_cleanup_rule": pick("content_cleanup_rule", cast=str, default=""),
        "clean_before_items": pick("clean_before_items", cast=list, default=[]),
        "clean_after_items": pick("clean_after_items", cast=list, default=[]),
        "enable_smart_split": pick("enable_smart_split", cast=bool, default=True),
        "balanced_split": pick("balanced_split", cast=bool, default=True),
        "max_segments": pick(
            "max_segments",
            cast=int,
            default=int(template.get("max_segments", 5) or 5),
        ),
        "min_segment_length": pick("min_segment_length", cast=int, default=10),
        "balanced_ratio_min": pick("balanced_ratio_min", cast=float, default=0.4),
        "balanced_ratio_max": pick("balanced_ratio_max", cast=float, default=0.9),
        "no_split_around": pick("no_split_around", cast=list, default=[]),
        "trim_edge_blank_lines": pick("trim_edge_blank_lines", cast=bool, default=True),
        "send_speed": pick("send_speed", cast=str, default="natural"),
        "enable_smart_reply": pick("enable_smart_reply", cast=bool, default=True),
        "enable_keep_reply": pick("enable_keep_reply", cast=bool, default=True),
        "disable_quote_in_private": pick(
            "disable_quote_in_private", cast=bool, default=True
        ),
    }

    if mode == "simple":
        # Hidden in simple: force template-level smart defaults (already via pick),
        # plus product rules that are stronger than raw template pollution.
        out["only_llm_result"] = True
        out["split_mode"] = "chars"
        out["split_words"] = list(default_chars)
        out["enable_smart_split"] = True
        out["balanced_split"] = True
        out["min_segment_length"] = int(template.get("min_segment_length", 10) or 10)
        out["balanced_ratio_min"] = float(template.get("balanced_ratio_min", 0.4) or 0.4)
        out["balanced_ratio_max"] = float(template.get("balanced_ratio_max", 0.9) or 0.9)
        out["no_split_around"] = []
        out["clean_after_items"] = []
        out["content_cleanup_rule"] = ""
        out["trim_edge_blank_lines"] = True
        # simple: both Reply behaviors always on (UI does not expose toggles)
        out["enable_smart_reply"] = True
        out["enable_keep_reply"] = True
        # reset interval leftovers then apply send_speed from template bases
        out["interval"] = str(template.get("interval", "1.5,3.5") or "1.5,3.5")
        out["log_base"] = float(template.get("log_base", 2.6) or 2.6)
        out["linear_base"] = float(template.get("linear_base", 0.5) or 0.5)
        out["linear_factor"] = float(template.get("linear_factor", 0.08) or 0.08)
        out["fixed_delay"] = float(template.get("fixed_delay", 1.5) or 1.5)
        apply_send_speed(out, out["send_speed"], template=template)
        # max_length_to_disable is not shown; only words_count_threshold is.
        # Do not keep pro leftover max_length_to_disable.
        out["max_length_to_disable"] = 0
        out["min_length_to_split"] = int(template.get("min_length_to_split", 0) or 0)
        out["regex"] = str(template.get("regex") or r"[。？！?!…\n]+")
    elif mode == "advanced":
        # Pro-only fields forced to template
        out["split_mode"] = "chars"
        if not out["split_words"]:
            out["split_words"] = list(default_chars)
        out["enable_smart_split"] = bool(template.get("enable_smart_split", True))
        out["min_segment_length"] = int(template.get("min_segment_length", 10) or 10)
        out["balanced_ratio_min"] = float(template.get("balanced_ratio_min", 0.4) or 0.4)
        out["balanced_ratio_max"] = float(template.get("balanced_ratio_max", 0.9) or 0.9)
        out["content_cleanup_rule"] = ""
        out["trim_edge_blank_lines"] = bool(template.get("trim_edge_blank_lines", True))
        out["interval"] = str(template.get("interval", "1.5,3.5") or "1.5,3.5")
        out["log_base"] = float(template.get("log_base", 2.6) or 2.6)
        out["linear_base"] = float(template.get("linear_base", 0.5) or 0.5)
        out["linear_factor"] = float(template.get("linear_factor", 0.08) or 0.08)
        out["fixed_delay"] = float(template.get("fixed_delay", 1.5) or 1.5)
        out["regex"] = str(template.get("regex") or r"[。？！?!…\n]+")
        out["max_length_to_disable"] = 0
        out["min_length_to_split"] = int(template.get("min_length_to_split", 0) or 0)
        # send_speed drives delay (pro interval_method must not leak)
        apply_send_speed(out, out["send_speed"] or "natural", template=template)
    else:
        # pro: keep user values; normalize words mode alias
        if out["split_mode"] == "words":
            out["split_mode"] = "chars"
        if not out["split_words"]:
            out["split_words"] = list(default_chars)

    # legacy threshold merge: words_count_threshold is the visible key
    if out["words_count_threshold"] > 0 and out["max_length_to_disable"] <= 0:
        out["max_length_to_disable"] = out["words_count_threshold"]
    return out
