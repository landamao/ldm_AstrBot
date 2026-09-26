from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.tools.registry import builtin_tool
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
from astrbot.core.utils.media_utils import MediaResolver


def _track_temp_media(event, media_path: str) -> None:
    """登记「本事件结束后可立刻删除」的临时媒体（仅限 AstrBot temp 目录下的文件）。"""

    try:
        path = Path(media_path).resolve()
        temp_dir = Path(get_astrbot_temp_path()).resolve()
        path.relative_to(temp_dir)
    except (OSError, ValueError):
        return
    event.track_temporary_local_file(str(path))


@builtin_tool
@dataclass
class TranscribeMediaTool(FunctionTool[AstrAgentContext]):
    """使用已配置的语音转文本模型转写音频或视频文件。"""

    name: str = "ldmbot_transcribe_media"
    description: str = (
        "Transcribe speech from an audio or video file using the configured "
        "default speech-to-text model. Accepts a local file path or an http(s) "
        "URL. When a user message contains a media placeholder such as "
        "[视频: path] or [语音: path], pass that path to this tool. "
        "Requires provider_stt_settings.enable and "
        "provider_stt_settings.provider_id to be configured."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "media": {
                    "type": "string",
                    "description": (
                        "Audio or video file to transcribe. Supports local file "
                        "paths, file:// URIs, and http(s) URLs."
                    ),
                },
            },
            "required": ["media"],
        }
    )

    async def call(
        self,
        context: ContextWrapper[AstrAgentContext],
        **kwargs,
    ) -> ToolExecResult:
        media_ref = str(kwargs.get("media") or "").strip()
        if not media_ref:
            return "错误：参数 media 不能为空，请提供音频或视频文件的本地路径或 URL。"

        event = context.context.event
        plugin_context = context.context.context

        try:
            stt_provider = plugin_context.get_using_stt_provider(
                umo=event.unified_msg_origin
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("音视频转写工具解析 STT 模型失败: %s", exc)
            return f"错误：解析语音转文本模型失败：{exc}"
        if stt_provider is None:
            return (
                "错误：未配置语音转文本模型。"
                "请先在配置中开启「启用语音转文本」并选择「默认语音转文本模型」"
                "（provider_stt_settings.enable 与 provider_stt_settings.provider_id），"
                "或使用 /provider 指令为本会话选择 STT 模型。"
            )

        try:
            wav_path = await MediaResolver(
                media_ref,
                media_type="audio",
                default_suffix=".wav",
            ).to_path(target_format="wav")
        except Exception as exc:  # noqa: BLE001
            logger.error("音视频转写工具获取音频失败: %s", exc)
            return f"错误：获取音频失败：{exc}"

        # 下载/转换产物位于 temp 目录，事件结束后即可清理
        _track_temp_media(event, wav_path)

        try:
            result = await stt_provider.get_text(audio_url=wav_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("音视频转写工具执行失败: %s", exc)
            return f"错误：语音转文本失败：{exc}"

        text = (result or "").strip()
        if not text:
            return "语音转文本完成，但模型没有返回有效文本。"
        logger.info(f"音视频转写工具结果: {text}")
        return text


__all__ = [
    "TranscribeMediaTool",
]
