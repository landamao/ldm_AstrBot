"""Tencent Silk audio conversion helpers."""

import asyncio
import audioop
import os
import subprocess
import wave
from io import BytesIO

from astrbot.core import logger

# The SILK SDK only supports these rates
_PYSILK_SUPPORTED_RATES = frozenset({8000, 12000, 16000, 24000, 32000, 48000})

_SILK_HEADER = b"#!SILK_V3"
# SKP_Silk_define.h: MAX_ARITHM_BYTES, the largest payload the SDK accepts per frame.
_MAX_SILK_FRAME_BYTES = 1024
# Upper bound for the frame-by-frame fallback (~60s of 20ms frames) so a
# pathological file cannot stall the pipeline.
_MAX_FALLBACK_FRAMES = 3000


def _split_silk_frames(body: bytes) -> tuple[bytes, int, int]:
    """Keep only well-formed SILK frames from a raw payload body.

    QQ appends an ``FF FF`` end marker to voice payloads. ``pysilk`` reads that
    marker as a frame length of ``-1``; ``BytesIO.read(-1)`` then returns the
    whole remaining stream and the SILK SDK rejects the negative length with
    ``DEC_PAYLOAD_TOO_LARGE``. Truncated or corrupted frames hit the same path,
    so every frame is validated before it reaches the decoder.

    Args:
        body: Payload bytes following the ``#!SILK_V3`` magic.

    Returns:
        A tuple of (frame bytes, dropped trailing bytes, frame count). Parsing
        stops at the first malformed frame because a broken length field makes
        every following frame boundary unreliable.
    """
    frames = bytearray()
    index = 0
    count = 0
    length = len(body)
    while index + 2 <= length:
        size = body[index] | (body[index + 1] << 8)
        if size == 0 or size > _MAX_SILK_FRAME_BYTES or index + 2 + size > length:
            break
        frames += body[index : index + 2 + size]
        index += 2 + size
        count += 1
    return bytes(frames), length - index, count


def _resync_silk_frames(body: bytes, scan: int = 64) -> tuple[bytes, int, int]:
    """Recover frames when the payload starts mid-frame.

    Used only when a strict parse yields no frame at all, which points at a
    shifted stream (extra or missing leading bytes) rather than a trailing
    marker. Every offset in the scanned window is tried and the one yielding the
    most frames wins.
    """
    best_frames, best_dropped, best_count = b"", len(body), 0
    for offset in range(1, min(scan, len(body))):
        frames, dropped, count = _split_silk_frames(body[offset:])
        if count > best_count:
            best_frames, best_dropped, best_count = frames, dropped + offset, count
    return best_frames, best_dropped, best_count


def _decode_silk_stream(payload: bytes, sample_rate: int) -> bytes:
    """Decode a complete SILK stream to PCM bytes."""
    import pysilk

    output_io = BytesIO()
    pysilk.decode(BytesIO(payload), output_io, sample_rate)
    return output_io.getvalue()


def _decode_silk_frame_by_frame(payload: bytes, sample_rate: int) -> bytes:
    """Decode SILK frames individually, replacing undecodable ones with silence.

    Used as a fallback when the stream fails to decode as a whole, so a single
    corrupt frame no longer discards the rest of the voice message.
    """
    silence = b"\x00\x00" * (sample_rate // 1000 * 20)
    body = payload[len(_SILK_HEADER) :]
    pcm = bytearray()
    index = 0
    length = len(body)
    decoded = 0
    while index + 2 <= length and decoded < _MAX_FALLBACK_FRAMES:
        size = body[index] | (body[index + 1] << 8)
        if size == 0 or size > _MAX_SILK_FRAME_BYTES or index + 2 + size > length:
            break
        frame = body[index : index + 2 + size]
        index += 2 + size
        decoded += 1
        try:
            pcm += _decode_silk_stream(_SILK_HEADER + frame, sample_rate)
        except Exception:
            pcm += silence
    return bytes(pcm)


async def tencent_silk_to_wav(silk_path: str, output_path: str) -> str:
    """Decode a Tencent Silk file to 24 kHz mono PCM WAV.

    The payload is sanitized frame by frame before decoding, because Tencent
    clients append an ``FF FF`` end marker that ``pysilk`` misreads as a
    negative frame length.

    Args:
        silk_path: Input Tencent Silk file path.
        output_path: Output WAV file path.

    Returns:
        The output WAV file path.

    Raises:
        ImportError: Raised when ``silk-python`` is not installed.
        ValueError: Raised when the payload holds no decodable SILK frame.
        OSError: Raised when input or output files cannot be accessed.
    """
    with open(silk_path, "rb") as f:
        input_data = f.read()

    # Locate the magic instead of only stripping the optional 0x02 marker, so a
    # payload with extra leading bytes is still recoverable.
    header_index = input_data.find(_SILK_HEADER)
    if header_index < 0:
        raise ValueError(f"SILK header not found: {silk_path}")

    body_start = header_index + len(_SILK_HEADER)
    frames, dropped, count = _split_silk_frames(input_data[body_start:])
    if count == 0:
        # No frame at all: the payload likely starts mid-frame, try to resync.
        frames, dropped, count = _resync_silk_frames(input_data[body_start:])
        if count:
            logger.warning(f"SILK 帧边界错位，已重同步 {count} 帧: {silk_path}")
    if dropped:
        logger.warning(
            "已丢弃 %d 字节非法 SILK 帧数据（尾部结束标记或截断帧）: %s",
            dropped,
            silk_path,
        )
    if not frames:
        raise ValueError(f"No valid SILK frame found: {silk_path}")

    payload = _SILK_HEADER + frames
    try:
        pcm = _decode_silk_stream(payload, 24000)
    except Exception as e:
        logger.warning(f"SILK 整体解码失败({e})，改用逐帧解码: {silk_path}")
        pcm = _decode_silk_frame_by_frame(payload, 24000)

    if not pcm:
        raise ValueError(f"SILK decoding produced no audio: {silk_path}")

    with wave.open(output_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)

    return output_path


async def wav_to_tencent_silk(wav_path: str, output_path: str) -> float:
    """Encode a WAV file to Tencent Silk.

    Args:
        wav_path: Input WAV file path.
        output_path: Output Tencent Silk file path.

    Returns:
        Audio duration in seconds.

    Raises:
        Exception: Raised when ``silk-python`` is not installed.
        OSError: Raised when input or output files cannot be accessed.
        wave.Error: Raised when the input file is not a readable WAV file.
    """
    try:
        import pysilk
    except (ImportError, ModuleNotFoundError) as e:
        raise Exception(
            "pysilk is not installed. Install the silk-python package from the "
            "dashboard platform logs page.",
        ) from e

    with wave.open(wav_path, "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        sampwidth = wav.getsampwidth()
        pcm_data = wav.readframes(wav.getnframes())

    # Downmix to mono, resample to 24 kHz if needed, and convert to 16-bit PCM
    # (pysilk only accepts 16-bit linear PCM)
    if channels == 2:
        pcm_data = audioop.tomono(pcm_data, sampwidth, 0.5, 0.5)
    if rate not in _PYSILK_SUPPORTED_RATES:
        pcm_data, _ = audioop.ratecv(pcm_data, sampwidth, 1, rate, 24000, None)
        rate = 24000
    if sampwidth != 2:
        pcm_data = audioop.lin2lin(pcm_data, sampwidth, 2)

    input_io = BytesIO(pcm_data)
    output_io = BytesIO()
    # tencent=True makes pysilk emit the QQ-compatible 0x02-prefixed SILK stream.
    pysilk.encode(input_io, output_io, rate, rate, tencent=True)
    with open(output_path, "wb") as f:
        f.write(output_io.getvalue())
    return len(pcm_data) / (2 * rate) if rate else 0


async def convert_to_pcm_wav(input_path: str, output_path: str) -> str:
    """Convert an audio file to 24 kHz mono 16-bit PCM WAV.

    Args:
        input_path: Source audio file path.
        output_path: Destination WAV file path.

    Returns:
        The output WAV file path.

    Raises:
        RuntimeError: Raised when conversion does not produce a non-empty WAV file.
    """
    try:
        from pyffmpeg import FFmpeg

        ff = FFmpeg()
        ff.convert(input_file=input_path, output_file=output_path)
    except Exception as e:
        logger.debug(
            "pyffmpeg conversion failed: %s. Falling back to ffmpeg CLI.",
            e,
        )

        # FFmpeg normalizes arbitrary audio input to the PCM WAV format required
        # by Tencent Silk encoding.
        p = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-acodec",
            "pcm_s16le",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-af",
            "apad=pad_dur=2",
            "-fflags",
            "+genpts",
            "-hide_banner",
            output_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = await p.communicate()
        logger.info(f"[FFmpeg] stdout: {stdout.decode().strip()}")
        logger.debug(f"[FFmpeg] stderr: {stderr.decode().strip()}")
        logger.info(f"[FFmpeg] return code: {p.returncode}")

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        return output_path
    raise RuntimeError("Converted WAV file is missing or empty")
