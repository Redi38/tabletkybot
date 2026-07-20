"""
Tests for services/voice_service.py (previously 0% test coverage).

subprocess.run (ffmpeg) and riva.client (Auth/ASRService) are mocked —
these tests never shell out to a real ffmpeg binary or call NVIDIA's Riva
API. asyncio.to_thread itself is not mocked: it's allowed to actually run
the (now-mocked) sync functions in a thread pool.
"""

import os
from unittest.mock import MagicMock, patch

from services.voice_service import transcribe_voice


class FakeConfig:
    def __init__(self, **overrides):
        self.nvidia_api_key = "test-nvidia-key"
        self.nvidia_riva_function_id = "test-function-id"
        for key, value in overrides.items():
            setattr(self, key, value)


def _fake_ffmpeg_creates_wav(cmd, capture_output, text):
    """Side effect for the mocked subprocess.run call inside
    _convert_to_wav_sync: writes a small dummy file at the output path."""
    wav_path = cmd[-1]
    with open(wav_path, "wb") as f:
        f.write(b"fake wav data")
    return MagicMock(returncode=0, stderr="")


def _make_fake_asr_response(transcript: str | None):
    """Builds a fake response matching riva's offline_recognize() shape."""
    response = MagicMock()
    if transcript is None:
        response.results = []
    else:
        alternative = MagicMock()
        alternative.transcript = transcript
        result = MagicMock()
        result.alternatives = [alternative]
        response.results = [result]
    return response


class TestTranscribeVoiceHappyPath:
    async def test_returns_transcribed_text(self):
        config = FakeConfig()

        with (
            patch("services.voice_service.subprocess.run", side_effect=_fake_ffmpeg_creates_wav),
            patch("riva.client.Auth"),
            patch("riva.client.ASRService") as mock_asr_service_cls,
        ):
            mock_asr_service_cls.return_value.offline_recognize.return_value = _make_fake_asr_response(
                "Take two pills after breakfast"
            )

            result = await transcribe_voice(config, "/fake/path/voice.ogg")

        assert result == "Take two pills after breakfast"

    async def test_strips_whitespace_from_transcript(self):
        config = FakeConfig()

        with (
            patch("services.voice_service.subprocess.run", side_effect=_fake_ffmpeg_creates_wav),
            patch("riva.client.Auth"),
            patch("riva.client.ASRService") as mock_asr_service_cls,
        ):
            mock_asr_service_cls.return_value.offline_recognize.return_value = _make_fake_asr_response(
                "  some text with padding  "
            )

            result = await transcribe_voice(config, "/fake/path/voice.ogg")

        assert result == "some text with padding"

    async def test_authenticates_with_config_credentials(self):
        config = FakeConfig(nvidia_api_key="my-secret-key", nvidia_riva_function_id="my-function-id")

        with (
            patch("services.voice_service.subprocess.run", side_effect=_fake_ffmpeg_creates_wav),
            patch("riva.client.Auth") as mock_auth,
            patch("riva.client.ASRService") as mock_asr_service_cls,
        ):
            mock_asr_service_cls.return_value.offline_recognize.return_value = _make_fake_asr_response("hi")

            await transcribe_voice(config, "/fake/path/voice.ogg")

        _, kwargs = mock_auth.call_args
        assert kwargs["metadata_args"] == [
            ["function-id", "my-function-id"],
            ["authorization", "Bearer my-secret-key"],
        ]


class TestTranscribeVoiceFfmpegConversion:
    async def test_uses_mono_16khz_16bit_pcm(self):
        config = FakeConfig()

        with (
            patch("services.voice_service.subprocess.run", side_effect=_fake_ffmpeg_creates_wav) as mock_run,
            patch("riva.client.Auth"),
            patch("riva.client.ASRService") as mock_asr_service_cls,
        ):
            mock_asr_service_cls.return_value.offline_recognize.return_value = _make_fake_asr_response("hi")

            await transcribe_voice(config, "/fake/path/voice.ogg")

        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "ffmpeg"
        assert "/fake/path/voice.ogg" in called_cmd
        assert "-ac" in called_cmd and called_cmd[called_cmd.index("-ac") + 1] == "1"
        assert "-ar" in called_cmd and called_cmd[called_cmd.index("-ar") + 1] == "16000"
        assert "-sample_fmt" in called_cmd and called_cmd[called_cmd.index("-sample_fmt") + 1] == "s16"


class TestTranscribeVoiceErrorHandling:
    async def test_returns_empty_string_when_no_results(self):
        config = FakeConfig()

        with (
            patch("services.voice_service.subprocess.run", side_effect=_fake_ffmpeg_creates_wav),
            patch("riva.client.Auth"),
            patch("riva.client.ASRService") as mock_asr_service_cls,
        ):
            mock_asr_service_cls.return_value.offline_recognize.return_value = _make_fake_asr_response(None)

            result = await transcribe_voice(config, "/fake/path/voice.ogg")

        assert result == ""

    async def test_returns_empty_string_when_ffmpeg_fails(self):
        config = FakeConfig()

        def _failing_ffmpeg(cmd, capture_output, text):
            return MagicMock(returncode=1, stderr="invalid data found when processing input")

        with patch("services.voice_service.subprocess.run", side_effect=_failing_ffmpeg):
            result = await transcribe_voice(config, "/fake/path/voice.ogg")

        assert result == ""

    async def test_returns_empty_string_when_riva_call_raises(self):
        config = FakeConfig()

        with (
            patch("services.voice_service.subprocess.run", side_effect=_fake_ffmpeg_creates_wav),
            patch("riva.client.Auth"),
            patch("riva.client.ASRService") as mock_asr_service_cls,
        ):
            mock_asr_service_cls.return_value.offline_recognize.side_effect = RuntimeError("gRPC unavailable")

            result = await transcribe_voice(config, "/fake/path/voice.ogg")

        assert result == ""

    async def test_does_not_raise_when_auth_setup_fails(self):
        config = FakeConfig()

        with (
            patch("services.voice_service.subprocess.run", side_effect=_fake_ffmpeg_creates_wav),
            patch("riva.client.Auth", side_effect=ConnectionError("could not reach grpc endpoint")),
        ):
            # Should not raise
            result = await transcribe_voice(config, "/fake/path/voice.ogg")

        assert result == ""


class TestTranscribeVoiceCleansUpTempFiles:
    async def test_temp_directory_is_removed_after_transcription(self):
        config = FakeConfig()
        captured_tmp_dir = {}

        def _capture_and_create(cmd, capture_output, text):
            wav_path = cmd[-1]
            captured_tmp_dir["path"] = os.path.dirname(wav_path)
            with open(wav_path, "wb") as f:
                f.write(b"data")
            return MagicMock(returncode=0, stderr="")

        with (
            patch("services.voice_service.subprocess.run", side_effect=_capture_and_create),
            patch("riva.client.Auth"),
            patch("riva.client.ASRService") as mock_asr_service_cls,
        ):
            mock_asr_service_cls.return_value.offline_recognize.return_value = _make_fake_asr_response("hi")

            await transcribe_voice(config, "/fake/path/voice.ogg")

        assert not os.path.exists(captured_tmp_dir["path"])

    async def test_temp_directory_is_removed_even_on_failure(self):
        config = FakeConfig()
        captured_tmp_dir = {}

        def _capture_and_fail(cmd, capture_output, text):
            wav_path = cmd[-1]
            captured_tmp_dir["path"] = os.path.dirname(wav_path)
            return MagicMock(returncode=1, stderr="boom")

        with patch("services.voice_service.subprocess.run", side_effect=_capture_and_fail):
            await transcribe_voice(config, "/fake/path/voice.ogg")

        assert not os.path.exists(captured_tmp_dir["path"])
