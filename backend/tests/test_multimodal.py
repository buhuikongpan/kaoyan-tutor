"""多模态图片直发测试：主模型配置 multimodal=True 时，图片按 OpenAI 格式
直接发给主模型（不再走视觉辅助分析）。

运行（在 backend 目录下）：
    python -m unittest tests.test_multimodal -v
"""
import base64
import json
import os
import sys
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from httpx import MockTransport, Request, Response  # noqa: E402

import app.services.llm_service as llm  # noqa: E402

CALLED_PAYLOAD = {}


async def fake_main(request: Request) -> Response:
    body = json.loads(request.content or b"{}")
    CALLED_PAYLOAD.clear()
    CALLED_PAYLOAD.update(body)
    sse = (
        'data: {"choices":[{"delta":{"content":"看到图片了"}}]}\n\n'
        'data: [DONE]\n\n'
    )
    return Response(200, headers={"Content-Type": "text/event-stream"},
                    content=sse.encode("utf-8"))


_real_async_client = llm.httpx.AsyncClient


class _MockAsyncClient(_real_async_client):
    def __init__(self, *args, **kwargs):
        super().__init__(transport=MockTransport(fake_main), *args, **kwargs)


def setUpModule():
    llm.httpx.AsyncClient = _MockAsyncClient


def tearDownModule():
    llm.httpx.AsyncClient = _real_async_client


from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core.model_config import load_model_config, save_model_config  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import ChatMessage, ChatSession  # noqa: E402

client = TestClient(app)
TEST_CONV = "conv_multimodal_test"


class MultimodalTest(unittest.TestCase):
    def setUp(self):
        self.backup = load_model_config()
        save_model_config({"main": {"multimodal": True}})  # 开启主模型多模态
        db = SessionLocal()
        try:
            db.query(ChatMessage).filter(ChatMessage.conv_id == TEST_CONV).delete()
            db.query(ChatSession).filter(ChatSession.conv_id == TEST_CONV).delete()
            db.commit()
        finally:
            db.close()

    def tearDown(self):
        save_model_config(self.backup)
        db = SessionLocal()
        try:
            db.query(ChatMessage).filter(ChatMessage.conv_id == TEST_CONV).delete()
            db.query(ChatSession).filter(ChatSession.conv_id == TEST_CONV).delete()
            db.commit()
        finally:
            db.close()

    def _send_with_image(self):
        # 构造一张 1x1 PNG
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with client.stream("POST", "/api/chat/send-stream", data={
            "query": "看看这张图",
            "subject": "math",
            "mode": "C",
            "conversation_id": TEST_CONV,
        }, files={"image": ("test.png", png, "image/png")}) as r:
            self.assertEqual(r.status_code, 200)
            body = "".join(r.iter_text())

    def test_image_sent_to_main_model(self):
        self._send_with_image()
        # 主模型收到的最后一条 user 消息是 OpenAI 多模态 content 数组
        calls = CALLED_PAYLOAD
        self.assertTrue(calls, "主模型未被调用")
        last_content = calls["messages"][-1]["content"]
        self.assertIsInstance(last_content, list)
        types = [p["type"] for p in last_content]
        self.assertIn("image_url", types, f"content 应含图片: {last_content}")
        img = [p for p in last_content if p["type"] == "image_url"][0]
        self.assertTrue(img["image_url"]["url"].startswith("data:image/png;base64,"))

        # 落库为文本摘要（历史展示用）
        db = SessionLocal()
        try:
            msgs = db.query(ChatMessage).filter(ChatMessage.conv_id == TEST_CONV)\
                .order_by(ChatMessage.seq).all()
            self.assertEqual([m.role for m in msgs], ["user", "assistant"])
            # 落库为多模态 content 数组的 JSON（历史回填时 _load_messages 会拍平为文本）
            self.assertIn("image_url", msgs[0].content)
            self.assertIn("data:image/png", msgs[0].content)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()