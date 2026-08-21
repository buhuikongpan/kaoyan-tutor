"""冒烟测试：基本路由 + 鉴权行为。

运行方式（在 backend 目录下）：
    python -m unittest tests.test_smoke -v
"""
import os
import sys
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


class SmokeTest(unittest.TestCase):
    def test_health(self):
        r = client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_config_status(self):
        r = client.get("/api/config/status")
        self.assertEqual(r.status_code, 200)
        for key in ("deepseek", "zhipu", "qwen", "auth_enabled"):
            self.assertIn(key, r.json())

    def test_conversations(self):
        r = client.get("/api/chat/conversations?subject=math")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json().keys()), {"A", "B", "C"})

    def test_videos_list(self):
        r = client.get("/api/videos/list")
        self.assertEqual(r.status_code, 200)
        self.assertIn("videos", r.json())

    def test_folder_tree(self):
        r = client.get("/api/folders/tree?subject=math")
        self.assertEqual(r.status_code, 200)
        self.assertIn("folders", r.json())
        self.assertIn("uncategorized", r.json())

    def test_send_stream_requires_query(self):
        r = client.post("/api/chat/send-stream")
        self.assertEqual(r.status_code, 422)  # 缺 query 表单参数


if __name__ == "__main__":
    unittest.main()