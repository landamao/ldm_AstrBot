from __future__ import annotations


class AstrBotError(Exception):
    """Base exception for all AstrBot errors."""


class ProviderNotFoundError(AstrBotError):
    """Raised when a specified provider is not found."""


class EmptyModelOutputError(AstrBotError):
    """Raised when the model response contains no usable assistant output."""


class ReasoningOnlyOutputError(EmptyModelOutputError):
    """模型响应只有思考内容、无正文也无工具调用时抛出。

    是否重试由「模型无正文时重新请求」及「重试方式」配置决定。
    """


class KnowledgeBaseUploadError(AstrBotError):
    """Raised when knowledge base upload fails with a user-facing message."""

    def __init__(
        self,
        *,
        stage: str,
        user_message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(user_message)
        self.stage = stage
        self.user_message = user_message
        self.details = details or {}

    def __str__(self) -> str:
        return self.user_message
