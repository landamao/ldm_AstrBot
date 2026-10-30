from dataclasses import dataclass
from typing import TYPE_CHECKING

from .compressor import ContextCompressor
from .token_counter import TokenCounter

if TYPE_CHECKING:
    from astrbot.core.provider.provider import Provider


@dataclass
class ContextConfig:
    """Context configuration class.

    Three independent limits share one unified behavior: whichever limit is
    reached first triggers an LLM compression. Setting a limit to 0 or a
    negative value disables that check entirely. No automatic truncation is
    performed.
    """

    max_context_tokens: int = 0
    """The model context window size, used as the denominator of
    context_compress_threshold. <= 0 disables the ratio-based check."""
    max_context_length: int = -1
    """Maximum number of conversation turns. When the history exceeds this
    number of turns, compression is triggered. <= 0 disables the check."""
    context_max_tokens: int = -1
    """Absolute token budget. When the estimated context tokens exceed this
    value, compression is triggered. <= 0 disables the check."""
    context_compress_threshold: float = 0.8
    """Context token usage ratio (against the model window) above which
    compression is triggered. Values <= 0 or >= 1 disable the check."""
    llm_compress_instruction: str | None = None
    """Instruction prompt for LLM-based compression."""
    llm_compress_keep_recent_rounds: int = 5
    """Number of most recent turns kept verbatim during compression; all
    older turns are summarized. <= 0 summarizes every turn."""
    llm_compress_provider: "Provider | None" = None
    """LLM provider used for compression tasks. If None, compression cannot
    run and the context is sent as-is."""
    custom_token_counter: TokenCounter | None = None
    """Custom token counting method. If None, the default method is used."""
    custom_compressor: ContextCompressor | None = None
    """Custom context compression method. If None, the default method is used."""
