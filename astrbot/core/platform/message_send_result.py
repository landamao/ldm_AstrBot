from dataclasses import dataclass
from typing import Any


@dataclass
class MessageSendResult:
    """消息发送结果。

    Attributes:
        message_id (str | None): 平台侧消息 ID，统一为字符串；平台未提供时为 None。
        raw (Any): 平台原生返回值（如 OneBot 协议端的响应 dict），未提供时为 None。
    """

    message_id: str | None = None
    raw: Any = None
