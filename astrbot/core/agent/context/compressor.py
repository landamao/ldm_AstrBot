from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ...provider.entities import TokenUsage
from ...provider.modalities import (
    log_context_sanitize_stats,
    sanitize_contexts_by_modalities,
)
from ..message import Message
from .token_counter import EstimateTokenCounter, TokenCounter

if TYPE_CHECKING:
    from astrbot import logger
else:
    try:
        from astrbot import logger
    except ImportError:
        import logging

        logger = logging.getLogger("astrbot")

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider

from ..context.truncator import ContextTruncator


@runtime_checkable
class ContextCompressor(Protocol):
    """
    Protocol for context compressors.
    Provides an interface for compressing message lists.
    """

    def should_compress(
        self, messages: list[Message], current_tokens: int, max_tokens: int
    ) -> bool:
        """Check if compression is needed.

        Args:
            messages: The message list to evaluate.
            current_tokens: The current token count.
            max_tokens: The maximum allowed tokens for the model.

        Returns:
            True if compression is needed, False otherwise.
        """
        ...

    async def __call__(self, messages: list[Message]) -> list[Message]:
        """Compress the message list.

        Args:
            messages: The original message list.

        Returns:
            The compressed message list.
        """
        ...


class TruncateByTurnsCompressor:
    """Truncate by turns compressor implementation.
    Truncates the message list by removing older turns.
    """

    def __init__(
        self, truncate_turns: int = 1, compression_threshold: float = 0.82
    ) -> None:
        """Initialize the truncate by turns compressor.

        Args:
            truncate_turns: The number of turns to remove when truncating (default: 1).
            compression_threshold: The compression trigger threshold (default: 0.82).
        """
        self.truncate_turns = truncate_turns
        self.compression_threshold = compression_threshold

    def should_compress(
        self, messages: list[Message], current_tokens: int, max_tokens: int
    ) -> bool:
        """Check if compression is needed.

        Args:
            messages: The message list to evaluate.
            current_tokens: The current token count.
            max_tokens: The maximum allowed tokens.

        Returns:
            True if compression is needed, False otherwise.
        """
        if max_tokens <= 0 or current_tokens <= 0:
            return False
        usage_rate = current_tokens / max_tokens
        return usage_rate > self.compression_threshold

    async def __call__(self, messages: list[Message]) -> list[Message]:
        truncator = ContextTruncator()
        truncated_messages = truncator.truncate_by_dropping_oldest_turns(
            messages,
            drop_turns=self.truncate_turns,
        )
        return truncated_messages


def _extract_system_messages(messages: list[Message]) -> list[Message]:
    """Return the leading system messages from a message list."""
    result = []
    for msg in messages:
        if msg.role == "system":
            result.append(msg)
        else:
            break
    return result


class LLMSummaryCompressor:
    """LLM-based summary compressor.
    Uses LLM to summarize old conversation history while keeping the most
    recent rounds as exact context.
    """

    TASK_CONTINUATION_INSTRUCTION = (
        "If a task appears to be in progress, end the summary with the latest "
        "known result and the concrete next step to continue the task."
    )

    def __init__(
        self,
        provider: "Provider",
        keep_recent_rounds: int = 5,
        instruction_text: str | None = None,
        compression_threshold: float = 0.8,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """Initialize the LLM summary compressor.

        Args:
            provider: The LLM provider instance.
            keep_recent_rounds: Keep this many most recent rounds as exact
                context and summarize everything older. <= 0 summarizes all
                rounds (except the active user round).
            instruction_text: Custom instruction for summary generation.
            compression_threshold: The compression trigger threshold (default: 0.8).
        """
        self.provider = provider
        self.compression_threshold = compression_threshold
        self.token_counter = token_counter or EstimateTokenCounter()
        self.keep_recent_rounds = max(0, int(keep_recent_rounds))
        # 最近一次压缩请求的模型返回用量；None 表示本次压缩没有真实模型数据
        self.last_usage: TokenUsage | None = None
        # 最近一次压缩失败的原因；None 表示最近一次压缩成功
        self.last_error: str | None = None

        self.instruction_text = instruction_text or (
            "Based on our full conversation history, produce a concise summary of key takeaways and/or project progress.\n"
            "The primary goal of this summary is to enable seamless continuation of the work that follows.\n"
            "1. Systematically cover all core topics discussed and the final conclusion/outcome for each; clearly highlight the latest primary focus.\n"
            "2. If any tools were used, summarize tool usage (total call count) and extract the most valuable insights from tool outputs.\n"
            "3. If any materials (files, documents, code, references) were read during the conversation that may be helpful for subsequent work, list each one with its scope and path.\n"
            "4. If there was an initial user goal, state it first and describe the current progress/status.\n"
            "5. Write the summary in the user's language.\n"
        )

    def should_compress(
        self, messages: list[Message], current_tokens: int, max_tokens: int
    ) -> bool:
        """Check if compression is needed.

        Args:
            messages: The message list to evaluate.
            current_tokens: The current token count.
            max_tokens: The maximum allowed tokens.

        Returns:
            True if compression is needed, False otherwise.
        """
        if max_tokens <= 0 or current_tokens <= 0:
            return False
        usage_rate = current_tokens / max_tokens
        return usage_rate > self.compression_threshold

    def _split_recent_rounds(
        self,
        rounds: list[list[Message]],
    ) -> tuple[list[list[Message]], list[list[Message]]]:
        """Split rounds into summarised history and exact recent context.

        Keep exactly ``keep_recent_rounds`` most recent whole rounds; all
        older rounds go into the summary. When ``keep_recent_rounds`` is 0,
        every round is summarised (the active user round is protected by the
        caller). No round is split.
        """
        if not rounds:
            return rounds, []

        if self.keep_recent_rounds > 0:
            keep = min(self.keep_recent_rounds, len(rounds))
            return rounds[: len(rounds) - keep], rounds[len(rounds) - keep :]

        return rounds, []

    async def __call__(self, messages: list[Message]) -> list[Message]:
        """Use LLM to generate a summary of the conversation history.

        Uses round-based splitting to preserve user-assistant turn boundaries.
        On LLM failure, returns the original messages unchanged (the caller
        sends the context as-is).
        """
        from .round_utils import split_into_rounds

        self.last_error = None
        rounds = split_into_rounds(messages)
        message_rounds = [
            [seg for seg in rnd if isinstance(seg, Message)] for rnd in rounds
        ]
        old_rounds, recent_rounds = self._split_recent_rounds(message_rounds)

        # The latest user message is the active request. Keep its whole round
        # exact even when keep_recent_rounds is 0 (which summarizes every
        # other round).
        if messages and messages[-1].role == "user" and old_rounds:
            latest_old_round = old_rounds[-1]
            if latest_old_round and latest_old_round[-1] is messages[-1]:
                old_rounds = old_rounds[:-1]
                recent_rounds = [latest_old_round, *recent_rounds]

        if not old_rounds:
            if recent_rounds and messages and messages[-1].role == "user":
                return messages
            old_rounds = message_rounds
            recent_rounds = []

        summary_contexts = [msg for rnd in old_rounds for msg in rnd]
        if not any(msg.role != "system" for msg in summary_contexts):
            if recent_rounds and messages and messages[-1].role == "user":
                return messages
            old_rounds = message_rounds
            recent_rounds = []
            summary_contexts = [msg for rnd in old_rounds for msg in rnd]
            if not any(msg.role != "system" for msg in summary_contexts):
                return messages

        if summary_contexts[-1].role != "assistant":
            summary_contexts.append(
                Message(
                    role="assistant",
                    content="Acknowledged.",
                )
            )
        summary_contexts.append(
            Message(
                role="user",
                content=(
                    "Generate a summary of our previous conversation history.\n"
                    f"<extra_instruction>\n{self.instruction_text}\n\n"
                    f"{self.TASK_CONTINUATION_INSTRUCTION}</extra_instruction>\n"
                    "Respond ONLY with the summary content, without any additional text or formatting."
                ),
            )
        )
        sanitized_summary_contexts, sanitize_stats = sanitize_contexts_by_modalities(
            summary_contexts,
            self.provider.provider_config.get("modalities", None),
        )
        log_context_sanitize_stats(sanitize_stats)

        # Generate summary
        try:
            response = await self.provider.text_chat(
                contexts=sanitized_summary_contexts,
            )
            summary_content = (response.completion_text or "").strip()
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            self.last_usage = None
            self.last_error = f"调用压缩模型失败：{e}"
            return messages
        self.last_usage = getattr(response, "usage", None) or None

        if not summary_content:
            logger.warning("LLM context compression returned an empty summary.")
            self.last_error = "压缩模型返回了空摘要"
            return messages

        # Build result: system messages + summary pair + recent rounds
        result = _extract_system_messages(messages)

        result.append(
            Message(
                role="user",
                content=f"Our previous history conversation summary: {summary_content}",
            )
        )
        result.append(
            Message(
                role="assistant",
                content="Acknowledged the summary of our previous conversation history.",
            )
        )

        # Flatten recent rounds back to message list
        for rnd in recent_rounds:
            for seg in rnd:
                if isinstance(seg, Message):
                    result.append(seg)

        return result
