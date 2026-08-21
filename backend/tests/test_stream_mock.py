"""流式链路测试：用 httpx.MockTransport 内存模拟 DeepSeek SSE 响应，
验证 send-stream 的事件序列（delta/done）、reasoning 分流与数据库落库。

不访问外网。运行方式（在 backend 目录下）：
    python -m unittest tests.test_stream_mock -v
"""
import json
import os
import sys
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from httpx import MockTransport, Request, Response  # noqa: E402

# 先把 llm_service 的 HTTP 客户端换成内存模拟，再导入 app
import app.services.llm_service as llm  # noqa: E402

TEST_CONV = "conv_unittest_stream"

SSE_RESPONSE = (
    'data: {"choices":[{"delta":{"reasoning_content":"内部思考中"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"，流式测试。"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"公式 $$\\\\int x\\\\,dx$$"}}]}\n\n'
    'data: [DONE]\n\n'
)


async def fake_deepseek(request: Request) -> Response:
    body = json.loads(request.content or b"{}")
    assert body.get("stream") is True, "payload 未开启流式"
    assert body["messages"][0]["role"] == "system"
    return Response(200, headers={"Content-Type": "text/event-stream"},
                    content=SSE_RESPONSE.encode("utf-8"))


_real_async_client = llm.httpx.AsyncClient


class _MockAsyncClient(_real_async_client):
    def __init__(self, *args, **kwargs):
        super().__init__(transport=MockTransport(fake_deepseek), *args, **kwargs)


llm.httpx.AsyncClient = _MockAsyncClient

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import ChatMessage, ChatSession  # noqa: E402

client = TestClient(app)


class StreamTest(unittest.TestCase):
    def setUp(self):
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        db = SessionLocal()
        try:
            db.query(ChatMessage).filter(ChatMessage.conv_id == TEST_CONV).delete()
            db.query(ChatSession).filter(ChatSession.conv_id == TEST_CONV).delete()
            db.commit()
        finally:
            db.close()

    def _read_sse(self):
        events = []
        with client.stream("POST", "/api/chat/send-stream", data={
            "query": "测试流式",
            "subject": "math",
            "mode": "C",
            "conversation_id": TEST_CONV,
        }) as r:
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/event-stream", r.headers.get("content-type", ""))
            buf = ""
            for chunk in r.iter_text():
                buf += chunk
                while "\n\n" in buf:
                    ev, buf = buf.split("\n\n", 1)
                    for line in ev.split("\n"):
                        if line.startswith("data:"):
                            d = line[5:].strip()
                            if d:
                                try:
                                    events.append(json.loads(d))
                                except json.JSONDecodeError:
                                    pass
        return events

    def test_stream_events_and_persistence(self):
        events = self._read_sse()
        deltas = [e["text"] for e in events if e.get("type") == "delta"]
        done = [e for e in events if e.get("type") == "done"]
        errs = [e for e in events if e.get("type") == "error"]

        self.assertFalse(errs, f"出现 error 事件: {errs}")
        self.assertTrue(deltas, "没有收到 delta 事件")
        self.assertEqual("".join(deltas), "你好，流式测试。公式 $$\\int x\\,dx$$")
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["conversation_id"], TEST_CONV)

        db = SessionLocal()
        try:
            msgs = db.query(ChatMessage).filter(ChatMessage.conv_id == TEST_CONV)\
                .order_by(ChatMessage.seq).all()
            self.assertEqual([m.role for m in msgs], ["user", "assistant"])
            self.assertEqual(msgs[-1].content, "你好，流式测试。公式 $$\\int x\\,dx$$")
            self.assertIn("内部思考中", msgs[-1].reasoning)  # reasoning 落库但不进正文
            sess = db.query(ChatSession).filter(ChatSession.conv_id == TEST_CONV).first()
            self.assertEqual(sess.name, "测试流式")  # 首条提问自动命名
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()