from astrbot import logger
from astrbot.core.notification import notification_service

from ..message import Message
from .compressor import LLMSummaryCompressor
from .config import ContextConfig
from .round_utils import split_into_rounds
from .token_counter import EstimateTokenCounter


class ContextManager:
    """Context compression manager.

    Three independent limits trigger one unified action (LLM compression):
    1. Conversation turns exceed ``max_context_length``.
    2. Context tokens exceed the absolute budget ``context_max_tokens``.
    3. Context tokens exceed ``context_compress_threshold`` of the model window.

    Whichever limit is reached first triggers compression. Limits set to
    0 or negative values are disabled. No automatic truncation is performed:
    if compression fails or has no effect, the context is sent as-is with a
    warning logged.
    """

    def __init__(
        self,
        config: ContextConfig,
    ) -> None:
        """Initialize the context manager.

        Args:
            config: The context configuration.
        """
        self.config = config

        self.token_counter = config.custom_token_counter or EstimateTokenCounter()

        if config.custom_compressor:
            self.compressor = config.custom_compressor
        elif config.llm_compress_provider:
            self.compressor = LLMSummaryCompressor(
                provider=config.llm_compress_provider,
                keep_recent_rounds=config.llm_compress_keep_recent_rounds,
                instruction_text=config.llm_compress_instruction,
                token_counter=self.token_counter,
            )
        else:
            self.compressor = None

    def check_limits(
        self, messages: list[Message], trusted_token_usage: int = 0
    ) -> str | None:
        """Check the three independent limits.

        Args:
            messages: The message list to evaluate.
            trusted_token_usage: The trusted token usage returned by the LLM
                API for the same context prefix, if available.

        Returns:
            A human-readable reason string when any limit is reached,
            otherwise None.
        """
        # 1. Turn-based limit
        if self.config.max_context_length and self.config.max_context_length > 0:
            rounds = len(split_into_rounds(messages))
            if rounds > self.config.max_context_length:
                return f"turns {rounds} > limit {self.config.max_context_length}"

        # Token-based limits share one count
        total_tokens = 0
        if (self.config.context_max_tokens and self.config.context_max_tokens > 0) or (
            self.config.max_context_tokens and self.config.max_context_tokens > 0
        ):
            total_tokens = self.token_counter.count_tokens(
                messages, trusted_token_usage
            )

        # 2. Absolute token budget
        if self.config.context_max_tokens and self.config.context_max_tokens > 0:
            if total_tokens > self.config.context_max_tokens:
                return (
                    f"tokens {total_tokens} > budget {self.config.context_max_tokens}"
                )

        # 3. Ratio against the model window
        threshold = self.config.context_compress_threshold
        if (
            self.config.max_context_tokens
            and self.config.max_context_tokens > 0
            and 0 < threshold < 1
        ):
            if total_tokens > threshold * self.config.max_context_tokens:
                return (
                    f"tokens {total_tokens} > "
                    f"{threshold:.0%} of window {self.config.max_context_tokens}"
                )

        return None

    async def process(
        self, messages: list[Message], trusted_token_usage: int = 0
    ) -> list[Message]:
        """Process the messages.

        Args:
            messages: The original message list.
            trusted_token_usage: The trusted token usage for the current
                context prefix, if available.

        Returns:
            The processed message list.
        """
        try:
            reason = self.check_limits(messages, trusted_token_usage)
            if reason is None:
                return messages

            if self.compressor is None:
                logger.warning(
                    "Context limit reached (%s) but no compression provider is "
                    "available; sending the context as-is.",
                    reason,
                )
                await notification_service.notify(
                    key="context_compress_no_provider",
                    title="上下文压缩失败：未配置可用的压缩模型",
                    detail=(
                        f"上下文已达到压缩条件（{reason}），但没有可用的压缩模型，"
                        "本次请求按原上下文发送，存在超出模型窗口的风险。"
                        "请在 WebUI「配置页 - 上下文管理策略」中配置"
                        "「用于上下文压缩的模型提供商」，或检查所选模型是否可用。"
                    ),
                )
                return messages

            logger.info("Context compression triggered: %s", reason)
            result = await self.compressor(messages)

            if result is messages or len(result) >= len(messages):
                # Compressor returns the input unchanged on failure or when
                # there is nothing to summarize.
                last_error = ""
                if isinstance(self.compressor, LLMSummaryCompressor):
                    last_error = self.compressor.last_error or ""
                logger.warning(
                    "Context compression had no effect (%s); sending the "
                    "context as-is.",
                    reason,
                )
                await notification_service.notify(
                    key="context_compress_failed",
                    title="上下文压缩失败，本次请求已按原上下文发送",
                    detail=(
                        f"压缩原因：{reason}。"
                        + (f"失败原因：{last_error}。" if last_error else "")
                        + "本次请求未截断、未丢失历史；若该问题持续发生，"
                        "请检查压缩模型是否可用，或调整上下文管理策略配置。"
                    ),
                )
                return messages

            # Report residual pressure without truncating.
            residual = self.check_limits(result)
            if residual is not None:
                logger.warning(
                    "Context still exceeds limits after compression (%s); "
                    "sending it as-is without truncation.",
                    residual,
                )

            prev_tokens = self.token_counter.count_tokens(messages, trusted_token_usage)
            tokens_after = self.token_counter.count_tokens(result)
            logger.info(
                "Compress completed. %s -> %s tokens.",
                prev_tokens,
                tokens_after,
            )
            return result
        except Exception as e:
            logger.error(f"Error during context processing: {e}", exc_info=True)
            return messages
