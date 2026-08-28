"""语音输入接口测试：mock 掉 ASR 转写函数，验证端点参数校验与文本返回。

不访问外网、不消耗 API 配额、不加载本地 Whisper 模型。
运行方式（在 backend 目录下）：
    python -m unittest tests.test_voice -v
"""
import io
import os
import sys
import unittest
from unittest.mock import patch, AsyncMock

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

FAKE_WAV = b"RIFFfake_wav_data_for_test_only"


class VoiceTest(unittest.TestCase):
    def test_transcribe_ok(self):
        fake = AsyncMock(return_value={"chunks": [{"start": 0.0, "text": "考研数学一"}]})
        with patch("app.api.routes.voice.transcribe_audio", fake):
            r = client.post(
                "/api/voice/transcribe",
                files={"file": ("voice.wav", io.BytesIO(FAKE_WAV), "audio/wav")},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["text"], "考研数学一")
        # 确认转写函数被调用且拿到了字节流与临时路径
        fake.assert_awaited_once()
        args, kwargs = fake.await_args
        self.assertEqual(args[0], FAKE_WAV)
        self.assertTrue(kwargs["audio_path"].endswith(".wav"))

    def test_transcribe_multiple_chunks_joined(self):
        fake = AsyncMock(return_value={"chunks": [
            {"start": 0.0, "text": "第一段。"},
            {"start": 3.0, "text": "第二段。"},
        ]})
        with patch("app.api.routes.voice.transcribe_audio", fake):
            r = client.post(
                "/api/voice/transcribe",
                files={"file": ("v.webm", io.BytesIO(FAKE_WAV), "audio/webm")},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["text"], "第一段。第二段。")

    def test_transcribe_empty_fails(self):
        r = client.post(
            "/api/voice/transcribe",
            files={"file": ("empty.webm", io.BytesIO(b""), "audio/webm")},
        )
        self.assertEqual(r.status_code, 400)

    def test_transcribe_no_result_fails(self):
        fake = AsyncMock(return_value=None)
        with patch("app.api.routes.voice.transcribe_audio", fake):
            r = client.post(
                "/api/voice/transcribe",
                files={"file": ("v.webm", io.BytesIO(FAKE_WAV), "audio/webm")},
            )
        self.assertEqual(r.status_code, 500)

    def test_transcribe_engine_error_message(self):
        fake = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("app.api.routes.voice.transcribe_audio", fake):
            r = client.post(
                "/api/voice/transcribe",
                files={"file": ("v.webm", io.BytesIO(FAKE_WAV), "audio/webm")},
            )
        self.assertEqual(r.status_code, 500)
        self.assertIn("语音识别失败", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()