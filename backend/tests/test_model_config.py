"""模型配置读写测试：保存即生效、key 留空保留、掩码回显。

运行（在 backend 目录下）：
    python -m unittest tests.test_model_config -v
"""
import os
import sys
import unittest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core.model_config import load_model_config, save_model_config  # noqa: E402

client = TestClient(app)


class ModelConfigTest(unittest.TestCase):

    def setUp(self):
        self.backup = load_model_config()

    def tearDown(self):
        save_model_config(self.backup)  # 恢复原配置，杜绝污染

    def test_save_and_read(self):
        r = client.post("/api/config/model", json={
            "main": {"base_url": "https://example.com/v1", "api_key": "",
                     "model": "my-test-model", "multimodal": True},
            "vision": {"enabled": False},
            "asr": {"model": "my-asr-model"},
        })
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["config"]
        self.assertEqual(data["main"]["model"], "my-test-model")
        self.assertTrue(data["main"]["multimodal"])
        self.assertTrue(data["main"]["has_key"])  # 留空 key 保留原 key
        self.assertFalse(data["vision"]["enabled"])
        self.assertEqual(data["asr"]["model"], "my-asr-model")

        # GET 回显一致（key 掩码，不泄露明文）
        g = client.get("/api/config/model")
        self.assertEqual(g.status_code, 200)
        cfg = g.json()
        self.assertEqual(cfg["main"]["model"], "my-test-model")
        self.assertNotIn("api_key", cfg["main"])          # 不回传明文 key
        self.assertTrue(cfg["main"]["has_key"])

        # 立即生效：运行时加载到的即是新配置
        live = load_model_config()
        self.assertEqual(live["main"]["model"], "my-test-model")
        self.assertTrue(live["main"]["multimodal"])
        self.assertFalse(live["vision"]["enabled"])

    def test_update_api_key(self):
        # 显式提交新 key 会覆盖
        r = client.post("/api/config/model", json={
            "main": {"api_key": "new-secret-key-123"},
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(load_model_config()["main"]["api_key"].startswith("new-secret-key"))


if __name__ == "__main__":
    unittest.main()