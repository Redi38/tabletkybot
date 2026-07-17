import asyncio
import logging
import os
import subprocess
import tempfile

import riva.client

logger = logging.getLogger(__name__)

RIVA_SERVER = "grpc.nvcf.nvidia.com:443"


def _convert_to_wav_sync(src_path: str, wav_path: str) -> None:
    """
    Converts Telegram voice into 16kHz mono 16-bit PCM WAV,
    the format Riva/Whisper NIM expects.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        src_path,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-sample_fmt",
        "s16",
        wav_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr}")


def _transcribe_sync(config, wav_path: str) -> str:
    auth = riva.client.Auth(
        uri=RIVA_SERVER,
        use_ssl=True,
        metadata_args=[
            ["function-id", config.nvidia_riva_function_id],
            ["authorization", f"Bearer {config.nvidia_api_key}"],
        ],
    )
    asr_service = riva.client.ASRService(auth)

    with open(wav_path, "rb") as f:
        audio_bytes = f.read()

    recognition_config = riva.client.RecognitionConfig(
        encoding=riva.client.AudioEncoding.LINEAR_PCM,
        language_code="multi",
        max_alternatives=1,
        enable_automatic_punctuation=True,
        sample_rate_hertz=16000,
        audio_channel_count=1,
    )

    response = asr_service.offline_recognize(audio_bytes, recognition_config)

    if not response.results:
        return ""

    return response.results[0].alternatives[0].transcript.strip()


async def transcribe_voice(config, ogg_path: str) -> str:
    """
    Takes a path to a downloaded Telegram voice file, converts
    it and returns the transcribed text via Whisper NIM.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        wav_path = os.path.join(tmp_dir, "voice.wav")
        try:
            await asyncio.to_thread(_convert_to_wav_sync, ogg_path, wav_path)
            text = await asyncio.to_thread(_transcribe_sync, config, wav_path)
            logger.info(f"🎙️ Voice transcribed: {text!r}")
            return text
        except Exception as e:
            logger.error(f"❌ Voice transcription failed: {e}", exc_info=True)
            return ""
