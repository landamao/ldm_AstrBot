"""音视频转写工具：STT 配置后注入，支持视频与音频，转写结果带标签。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot.core.message.components import Plain, Record, Reply, Video  # noqa: E402
from astrbot.core.pipeline.preprocess_stage.stage import PreProcessStage  # noqa: E402
from astrbot.core.tools import transcription_tools  # noqa: E402
from astrbot.core.tools.registry import (  # noqa: E402
    get_builtin_tool_config_rule,
    get_builtin_tool_config_statuses,
)


def _make_context(stt_provider) -> MagicMock:
    event = MagicMock()
    event.unified_msg_origin = "aiocqhttp:GroupMessage:123"
    plugin_context = MagicMock()
    plugin_context.get_using_stt_provider.return_value = stt_provider
    context = MagicMock()
    context.context.event = event
    context.context.context = plugin_context
    return context


def test_transcribe_media_success():
    async def _run():
        stt_provider = MagicMock()
        stt_provider.get_text = AsyncMock(return_value="你好，世界")
        context = _make_context(stt_provider)

        with patch.object(
            transcription_tools,
            "MediaResolver",
        ) as resolver_cls:
            resolver_cls.return_value.to_path = AsyncMock(return_value="/tmp/a.wav")
            result = await transcription_tools.TranscribeMediaTool().call(
                context, media="http://example.com/v.mp4"
            )

        assert result == "你好，世界"
        resolver_cls.assert_called_once_with(
            "http://example.com/v.mp4",
            media_type="audio",
            default_suffix=".wav",
        )
        stt_provider.get_text.assert_awaited_once_with(audio_url="/tmp/a.wav")

    asyncio.run(_run())


def test_transcribe_media_tracks_temp_file():
    async def _run():
        stt_provider = MagicMock()
        stt_provider.get_text = AsyncMock(return_value="文本")
        context = _make_context(stt_provider)
        resolved_wav = str(Path("/tmp/a.wav").resolve())

        with patch.object(
            transcription_tools,
            "MediaResolver",
        ) as resolver_cls, patch.object(
            transcription_tools,
            "get_astrbot_temp_path",
            return_value="/tmp",
        ):
            resolver_cls.return_value.to_path = AsyncMock(return_value="/tmp/a.wav")
            await transcription_tools.TranscribeMediaTool().call(
                context, media="/tmp/a.mp4"
            )

        context.context.event.track_temporary_local_file.assert_called_once_with(
            resolved_wav
        )

    asyncio.run(_run())


def test_transcribe_media_without_provider_returns_chinese_error():
    async def _run():
        context = _make_context(None)
        result = await transcription_tools.TranscribeMediaTool().call(
            context, media="/tmp/a.mp4"
        )
        assert "未配置语音转文本模型" in result

    asyncio.run(_run())


def test_transcribe_media_empty_media_arg():
    async def _run():
        context = _make_context(MagicMock())
        result = await transcription_tools.TranscribeMediaTool().call(context, media=" ")
        assert "media 不能为空" in result

    asyncio.run(_run())


def test_transcribe_media_provider_error_is_wrapped():
    async def _run():
        stt_provider = MagicMock()
        stt_provider.get_text = AsyncMock(side_effect=RuntimeError("boom"))
        context = _make_context(stt_provider)

        with patch.object(
            transcription_tools,
            "MediaResolver",
        ) as resolver_cls:
            resolver_cls.return_value.to_path = AsyncMock(return_value="/tmp/a.wav")
            result = await transcription_tools.TranscribeMediaTool().call(
                context, media="/tmp/a.mp4"
            )

        assert "语音转文本失败" in result and "boom" in result

    asyncio.run(_run())


def test_transcribe_media_empty_result_hint():
    async def _run():
        stt_provider = MagicMock()
        stt_provider.get_text = AsyncMock(return_value="  ")
        context = _make_context(stt_provider)

        with patch.object(
            transcription_tools,
            "MediaResolver",
        ) as resolver_cls:
            resolver_cls.return_value.to_path = AsyncMock(return_value="/tmp/a.wav")
            result = await transcription_tools.TranscribeMediaTool().call(
                context, media="/tmp/a.mp4"
            )

        assert "没有返回有效文本" in result

    asyncio.run(_run())


def test_transcribe_media_injection_rule():
    rule = get_builtin_tool_config_rule("ldmbot_transcribe_media")
    assert rule is not None

    enabled = {"provider_stt_settings": {"enable": True, "provider_id": "whisper"}}
    statuses = get_builtin_tool_config_statuses(
        "ldmbot_transcribe_media",
        [{"conf_id": "default", "conf_name": "default", "config": enabled}],
    )
    assert statuses[0]["enabled"] is True

    disabled = {"provider_stt_settings": {"enable": False, "provider_id": "whisper"}}
    statuses = get_builtin_tool_config_statuses(
        "ldmbot_transcribe_media",
        [{"conf_id": "default", "conf_name": "default", "config": disabled}],
    )
    assert statuses[0]["enabled"] is False


async def _make_stage(stt_settings: dict) -> PreProcessStage:
    stage = PreProcessStage()
    ctx = MagicMock()
    ctx.astrbot_config = {
        "provider_stt_settings": stt_settings,
        "platform_settings": {},
    }
    await stage.initialize(ctx)
    return stage


def _make_event(components: list) -> MagicMock:
    event = MagicMock()
    event.get_messages.return_value = components
    event.message_str = ""
    event.message_obj.message_str = ""
    event.get_self_id.return_value = "bot"
    event.get_sender_id.return_value = "user"
    event.get_platform_name.return_value = "aiocqhttp"
    return event


def test_preprocess_video_placeholder_when_stt_ready():
    async def _run():
        stage = await _make_stage({"enable": True, "provider_id": "whisper"})
        video = Video.fromURL("http://example.com/v.mp4")
        event = _make_event([video])

        with patch.object(
            Video, "convert_to_file_path", AsyncMock(return_value="D:/tmp/v.mp4")
        ):
            await stage.process(event)

        assert "[视频: D:/tmp/v.mp4]" in event.message_str
        assert "[视频: D:/tmp/v.mp4]" in event.message_obj.message_str
        assert video.file == "D:/tmp/v.mp4"

    asyncio.run(_run())


def test_preprocess_video_skipped_when_stt_disabled():
    async def _run():
        stage = await _make_stage({"enable": False, "provider_id": "whisper"})
        video = Video.fromURL("http://example.com/v.mp4")
        event = _make_event([video])

        convert = AsyncMock(return_value="D:/tmp/v.mp4")
        with patch.object(Video, "convert_to_file_path", convert):
            await stage.process(event)

        convert.assert_not_awaited()
        assert event.message_str == ""

    asyncio.run(_run())


def test_preprocess_wrap_stt_text_tag():
    assert (
        PreProcessStage._wrap_stt_text("大家好", is_reply=False)
        == "[语音转文字: 大家好]"
    )
    assert (
        PreProcessStage._wrap_stt_text("大家好", is_reply=True)
        == "[引用语音转文字: 大家好]"
    )


def test_preprocess_media_placeholder_appends_with_space():
    event = _make_event([])
    event.message_str = "看看这个"
    event.message_obj.message_str = "看看这个"
    PreProcessStage._append_media_placeholder(event, "[视频: D:/tmp/v.mp4]")
    assert event.message_str == "看看这个 [视频: D:/tmp/v.mp4]"
    assert event.message_obj.message_str == "看看这个 [视频: D:/tmp/v.mp4]"

    event2 = _make_event([])
    PreProcessStage._append_media_placeholder(event2, "[视频: D:/tmp/v.mp4]")
    assert event2.message_str == "[视频: D:/tmp/v.mp4]"
    assert event2.message_obj.message_str == "[视频: D:/tmp/v.mp4]"


def test_preprocess_record_transcription_gets_tag():
    async def _run():
        stage = await _make_stage({"enable": True, "provider_id": "whisper"})
        stt_provider = MagicMock()
        stt_provider.get_text = AsyncMock(return_value="早上好")
        stage.plugin_manager.context.get_using_stt_provider.return_value = stt_provider

        record = Record.fromURL("http://example.com/a.wav")
        event = _make_event([record])
        with patch.object(
            Record, "convert_to_file_path", AsyncMock(return_value="D:/tmp/a.wav")
        ):
            await stage.process(event)

        assert "[语音转文字: 早上好]" in event.message_str
        assert isinstance(event.get_messages.return_value[0], Plain)
        assert (
            event.get_messages.return_value[0].text == "[语音转文字: 早上好]"
        )

    asyncio.run(_run())


def test_preprocess_reply_record_transcription_gets_quote_tag():
    async def _run():
        stage = await _make_stage({"enable": True, "provider_id": "whisper"})
        stt_provider = MagicMock()
        stt_provider.get_text = AsyncMock(return_value="引用的语音内容")
        stage.plugin_manager.context.get_using_stt_provider.return_value = stt_provider

        record = Record.fromURL("http://example.com/a.wav")
        reply = Reply(chain=[record], id="1")
        event = _make_event([reply])
        with patch.object(
            Record, "convert_to_file_path", AsyncMock(return_value="D:/tmp/a.wav")
        ):
            await stage.process(event)

        assert "[引用语音转文字: 引用的语音内容]" in event.message_str
        assert reply.chain[0].text == "[引用语音转文字: 引用的语音内容]"

    asyncio.run(_run())
