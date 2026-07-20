# Inspired by MoonshotAI/kosong, credits to MoonshotAI/kosong authors for the original implementation.
# License: Apache License 2.0

import re
from typing import Any, ClassVar, Literal, TypeVar, cast

from pydantic import (
    BaseModel,
    GetCoreSchemaHandler,
    PrivateAttr,
    ValidationError,
    model_serializer,
    model_validator,
)
from pydantic_core import core_schema

ContentPartT = TypeVar("ContentPartT", bound="ContentPart")


class ContentPart(BaseModel):
    """A part of the content in a message."""

    __content_part_registry: ClassVar[dict[str, type["ContentPart"]]] = {}

    type: Literal["text", "think", "image_url", "audio_url"]
    _no_save: bool = PrivateAttr(default=False)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        invalid_subclass_error_msg = f"ContentPart subclass {cls.__name__} must have a `type` field of type `str`"

        type_value = getattr(cls, "type", None)
        if type_value is None or not isinstance(type_value, str):
            raise ValueError(invalid_subclass_error_msg)

        cls.__content_part_registry[type_value] = cls

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        # If we're dealing with the base ContentPart class, use custom validation
        if cls.__name__ == "ContentPart":

            def validate_content_part(value: Any) -> Any:
                # if it's already an instance of a ContentPart subclass, return it
                if hasattr(value, "__class__") and issubclass(value.__class__, cls):
                    return value

                # if it's a dict with a type field, dispatch to the appropriate subclass
                if isinstance(value, dict) and "type" in value:
                    type_value: Any | None = cast(dict[str, Any], value).get("type")
                    if not isinstance(type_value, str):
                        raise ValueError(f"Cannot validate {value} as ContentPart")
                    target_class = cls.__content_part_registry[type_value]
                    part = target_class.model_validate(value)
                    if cast(dict[str, Any], value).get("_no_save"):
                        part._no_save = True
                    return part

                raise ValueError(f"Cannot validate {value} as ContentPart")

            return core_schema.no_info_plain_validator_function(validate_content_part)

        # for subclasses, use the default schema
        return handler(source_type)

    def mark_as_temp(self: ContentPartT) -> ContentPartT:
        """Mark this content part as provider-facing only, not persisted."""
        self._no_save = True
        return self

    def model_dump_for_context(self) -> dict[str, Any]:
        data = self.model_dump()
        if self._no_save:
            data["_no_save"] = True
        return data


class TextPart(ContentPart):
    """
    >>> TextPart(text="Hello, world!").model_dump()
    {'type': 'text', 'text': 'Hello, world!'}
    """

    type: str = "text"
    text: str


class ThinkPart(ContentPart):
    """
    >>> ThinkPart(think="I think I need to think about this.").model_dump()
    {'type': 'think', 'think': 'I think I need to think about this.', 'encrypted': None}
    """

    type: str = "think"
    think: str
    encrypted: str | None = None
    """Encrypted thinking content, or signature."""

    def merge_in_place(self, other: Any) -> bool:
        if not isinstance(other, ThinkPart):
            return False
        if self.encrypted:
            return False
        self.think += other.think
        if other.encrypted:
            self.encrypted = other.encrypted
        return True


class ImageURLPart(ContentPart):
    """
    >>> ImageURLPart(image_url="http://example.com/image.jpg").model_dump()
    {'type': 'image_url', 'image_url': 'http://example.com/image.jpg'}
    """

    class ImageURL(BaseModel):
        url: str
        """The URL of the image, can be data URI scheme like `data:image/png;base64,...`."""
        id: str | None = None
        """The ID of the image, to allow LLMs to distinguish different images."""

    type: str = "image_url"
    image_url: ImageURL


class AudioURLPart(ContentPart):
    """
    >>> AudioURLPart(audio_url=AudioURLPart.AudioURL(url="https://example.com/audio.mp3")).model_dump()
    {'type': 'audio_url', 'audio_url': {'url': 'https://example.com/audio.mp3', 'id': None}}
    """

    class AudioURL(BaseModel):
        url: str
        """The URL of the audio, can be data URI scheme like `data:audio/aac;base64,...`."""
        id: str | None = None
        """The ID of the audio, to allow LLMs to distinguish different audios."""

    type: str = "audio_url"
    audio_url: AudioURL


class ToolCall(BaseModel):
    """
    A tool call requested by the assistant.

    >>> ToolCall(
    ...     id="123",
    ...     function=ToolCall.FunctionBody(
    ...         name="function",
    ...         arguments="{}"
    ...     ),
    ... ).model_dump()
    {'type': 'function', 'id': '123', 'function': {'name': 'function', 'arguments': '{}'}}
    """

    class FunctionBody(BaseModel):
        name: str
        arguments: str | None

    type: Literal["function"] = "function"

    id: str
    """The ID of the tool call."""
    function: FunctionBody
    """The function body of the tool call."""
    extra_content: dict[str, Any] | None = None
    """Extra metadata for the tool call."""

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        if self.extra_content is None:
            data.pop("extra_content", None)
        return data


class ToolCallPart(BaseModel):
    """A part of the tool call."""

    arguments_part: str | None = None
    """A part of the arguments of the tool call."""


class CheckpointData(BaseModel):
    """Internal checkpoint data for linking LLM turns to platform history."""

    id: str


CHECKPOINT_ROLE = "_checkpoint"


class Message(BaseModel):
    """A message in a conversation."""

    role: Literal[
        "system",
        "user",
        "assistant",
        "tool",
        "_checkpoint",
    ]

    content: str | list[ContentPart] | CheckpointData | None = None
    """The content of the message."""

    tool_calls: list[ToolCall] | list[dict] | None = None
    """The tool calls of the message."""

    tool_call_id: str | None = None
    """The ID of the tool call."""

    _no_save: bool = PrivateAttr(default=False)
    _checkpoint_after: CheckpointData | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def check_content_required(self):
        if self.role == CHECKPOINT_ROLE:
            if not isinstance(self.content, CheckpointData):
                raise ValueError("checkpoint message content must be CheckpointData")
            return self

        if isinstance(self.content, CheckpointData):
            raise ValueError("CheckpointData is only allowed for role='_checkpoint'")

        # assistant + tool_calls is not None: allow content to be None
        if self.role == "assistant" and self.tool_calls is not None:
            return self

        # other all cases: content is required
        if self.content is None:
            raise ValueError(
                "content is required unless role='assistant' and tool_calls is not None"
            )
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        data = handler(self)
        if self.tool_calls is None:
            data.pop("tool_calls", None)
        if self.tool_call_id is None:
            data.pop("tool_call_id", None)
        return data


class AssistantMessageSegment(Message):
    """A message segment from the assistant."""

    role: Literal["assistant"] = "assistant"


class ToolCallMessageSegment(Message):
    """A message segment representing a tool call."""

    role: Literal["tool"] = "tool"


class UserMessageSegment(Message):
    """A message segment from the user."""

    role: Literal["user"] = "user"


class SystemMessageSegment(Message):
    """A message segment from the system."""

    role: Literal["system"] = "system"


class CheckpointMessageSegment(Message):
    """Internal checkpoint segment for persisted conversation history."""

    role: Literal["_checkpoint"] = "_checkpoint"
    content: CheckpointData | None = None


def is_checkpoint_message(message: Message | dict) -> bool:
    """Return whether a message is an internal checkpoint."""
    if isinstance(message, Message):
        return message.role == CHECKPOINT_ROLE
    return isinstance(message, dict) and message.get("role") == CHECKPOINT_ROLE


def get_checkpoint_id(message: Message | dict) -> str | None:
    """Return the checkpoint id from an internal checkpoint message."""
    if not is_checkpoint_message(message):
        return None

    content = (
        message.content if isinstance(message, Message) else message.get("content")
    )
    if isinstance(content, CheckpointData):
        return content.id
    if isinstance(content, dict):
        checkpoint_id = content.get("id")
        return (
            checkpoint_id if isinstance(checkpoint_id, str) and checkpoint_id else None
        )
    return None


def strip_checkpoint_messages(history: list[dict]) -> list[dict]:
    """Remove internal checkpoint messages from provider-facing history."""
    return [message for message in history if not is_checkpoint_message(message)]


# 旧版/旁路可能把用户正文写成 "[昵称(ID)]: 正文"，与同轮
# "正文 + <system_reminder>/<favour>" 多段 user 消息并存，造成重复上下文。
_USER_IDENTITY_PREFIX_RE = re.compile(
    r"^\s*\[[^\[\]\n]{1,80}\([^()\[\]\n]{1,64}\)\]\s*[:：]\s*"
)


def _extract_user_plain_from_content(content: Any) -> str:
    """提取 user 消息中的「正文」纯文本（忽略 system_reminder/favour 等标签块）。"""
    if content is None:
        return ""
    if isinstance(content, str):
        text = content.strip()
        if not text:
            return ""
        # 整段都是标签时不当作正文
        if text.startswith("<") and text.endswith(">"):
            return ""
        return _USER_IDENTITY_PREFIX_RE.sub("", text).strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            text = ""
            if isinstance(part, dict):
                if part.get("type") not in (None, "text"):
                    continue
                text = str(part.get("text") or "").strip()
            else:
                text = str(getattr(part, "text", "") or "").strip()
            if not text:
                continue
            if text.startswith("<") and (
                text.startswith("<system_reminder>")
                or text.startswith("<favour>")
                or text.startswith("<Quoted Message>")
                or text.startswith("<selected_excerpt>")
            ):
                continue
            parts.append(_USER_IDENTITY_PREFIX_RE.sub("", text).strip())
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def _is_identity_prefixed_user_message(item: dict[str, Any] | Message) -> bool:
    """是否为仅含「[昵称(ID)]: 正文」的 user 字符串消息。"""
    if isinstance(item, Message):
        if item.role != "user":
            return False
        content = item.content
    else:
        if item.get("role") != "user":
            return False
        content = item.get("content")
    if not isinstance(content, str):
        return False
    text = content.strip()
    return bool(_USER_IDENTITY_PREFIX_RE.match(text))


def dedupe_identity_prefixed_user_messages(
    history: list[dict[str, Any]] | list[Message],
) -> list:
    """去掉与相邻「正文+提示」重复的 [昵称(ID)]: 正文 消息。

    保留目标形态：
    - 正文
    - <system_reminder> / <favour> 等提示块

    丢弃：
    - 仅有 [名字(ID)]: 正文，且与相邻 list/多段 user 正文相同的那条
    """
    if not history:
        return history

    keep_flags = [True] * len(history)
    plains: list[str | None] = [None] * len(history)

    def plain_at(idx: int) -> str:
        cached = plains[idx]
        if cached is not None:
            return cached
        item = history[idx]
        if isinstance(item, Message):
            role = item.role
            content = item.content
        else:
            role = item.get("role")
            content = item.get("content")
        if role != "user":
            plains[idx] = ""
            return ""
        val = _extract_user_plain_from_content(content)
        plains[idx] = val
        return val

    for i, item in enumerate(history):
        if not _is_identity_prefixed_user_message(item):
            continue
        plain = plain_at(i)
        if not plain:
            continue
        # 优先与后一条（常见：先写前缀字符串，再写 list 正文+提示）比对
        for j in (i + 1, i - 1):
            if j < 0 or j >= len(history) or not keep_flags[j]:
                continue
            other = history[j]
            if isinstance(other, Message):
                other_role = other.role
                other_content = other.content
            else:
                other_role = other.get("role")
                other_content = other.get("content")
            if other_role != "user":
                continue
            # 另一条最好是 list 多段（正文+提示）；字符串纯正文也可去重
            if isinstance(other_content, list) or (
                isinstance(other_content, str)
                and not _USER_IDENTITY_PREFIX_RE.match(other_content.strip())
            ):
                if plain_at(j) == plain:
                    keep_flags[i] = False
                    break

    if all(keep_flags):
        return history
    return [item for item, keep in zip(history, keep_flags) if keep]


def _get_checkpoint_data(message: Message | dict) -> CheckpointData | None:
    if not is_checkpoint_message(message):
        return None

    content = (
        message.content if isinstance(message, Message) else message.get("content")
    )
    if isinstance(content, CheckpointData):
        return content
    if isinstance(content, dict):
        try:
            return CheckpointData.model_validate(content)
        except ValidationError:
            return None
    return None


def bind_checkpoint_messages(history: list[dict]) -> list[Message]:
    """Load persisted history and bind checkpoint segments to prior messages."""
    # 加载时去掉与「正文+提示」重复的 [昵称(ID)]: 正文
    history = dedupe_identity_prefixed_user_messages(history)
    messages: list[Message] = []
    for item in history:
        if is_checkpoint_message(item):
            checkpoint = _get_checkpoint_data(item)
            if checkpoint is not None and messages:
                messages[-1]._checkpoint_after = checkpoint
            continue

        message = Message.model_validate(item)
        if item.get("_no_save"):
            message._no_save = True
        messages.append(message)

    return messages


def dump_messages_with_checkpoints(messages: list[Message]) -> list[dict]:
    """Dump runtime messages and reinsert bound checkpoint segments."""
    # 落库前再去一次，避免新路径继续写入重复前缀消息
    messages = dedupe_identity_prefixed_user_messages(messages)
    dumped: list[dict] = []
    for message in messages:
        message_data = message.model_dump()
        if isinstance(message.content, list):
            message_data["content"] = [
                part.model_dump()
                for part in message.content
                if not getattr(part, "_no_save", False)
            ]
        dumped.append(message_data)
        if message._checkpoint_after is not None:
            dumped.append(
                CheckpointMessageSegment(content=message._checkpoint_after).model_dump()
            )
    return dumped
