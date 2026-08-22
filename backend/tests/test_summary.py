"""课程总结功能测试：MockTransport 模拟 DeepSeek 的「分段提取 → 合并」两轮调用，
验证 generate-summary 端点、总结落盘与状态流转。

不访问外网。运行方式（在 backend 目录下）：
    python -m unittest tests.test_summary -v
"""
import json
import os
import sys
import unittest
from pathlib import Path

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from httpx import MockTransport, Request, Response  # noqa: E402

import app.services.llm_service as llm  # noqa: E402

_real_async_client = llm.httpx.AsyncClient


async def fake_llm(request: Request) -> Response:
    """非流式 JSON 响应：按 prompt 内容区分分段提取轮与合并轮"""
    body = json.loads(request.content or b"{}")
    assert body.get("stream") is False, "总结生成应使用非流式调用"
    content = body["messages"][-1]["content"]
    if "逐句扫描" in content:
        reply = ("分段要点：极限的定义 [00:10]；洛必达法则 [15:00]；"
                 "关键公式 $\\lim_{x\\to0}\\frac{\\sin x}{x}=1$；易错：分母不能为0")
    else:
        reply = ("## 知识点\n"
                 "- 极限的定义 [00:10]：函数趋近过程的描述\n"
                 "- 洛必达法则 [15:00]：0/0 型未定式求极限\n"
                 "## 核心公式\n"
                 "$$\\lim_{x\\to0}\\frac{\\sin x}{x}=1$$\n"
                 "## 例题与题型\n- 0/0 型直接洛必达\n"
                 "## 方法技巧\n- 先化简再求导\n"
                 "## 易错点\n- 分母为 0 时不能直接代入")
    return Response(200, json={"choices": [{"message": {"content": reply}}]})


class _MockAsyncClient(_real_async_client):
    def __init__(self, *args, **kwargs):
        super().__init__(transport=MockTransport(fake_llm), *args, **kwargs)


def setUpModule():
    """本模块测试前替换全局 AsyncClient 为总结 mock（运行期替换，与其他测试文件隔离）"""
    llm.httpx.AsyncClient = _MockAsyncClient


def tearDownModule():
    """本模块测试结束后恢复真实 AsyncClient"""
    llm.httpx.AsyncClient = _real_async_client


from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import Video, Subtitle  # noqa: E402

client = TestClient(app)
db = SessionLocal()


class SummaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 建一条专用的测试视频（字幕已 done，无总结）
        v = Video(subject="math", filename="test_summary.mp4", title="测试总结课",
                  file_path="no_such_file.mp4", subtitle_status="done",
                  summary_status="none")
        db.add(v)
        db.commit()
        db.refresh(v)
        cls.video_id = v.id
        for i, (st, txt) in enumerate([(10, "极限的定义，函数趋近过程"), (800, "洛必达法则，0/0型")]):
            db.add(Subtitle(video_id=v.id, subject="math", start_time=float(st),
                            end_time=float(st + 10), text=txt, seq=i))
        db.commit()

    @classmethod
    def tearDownClass(cls):
        db.query(Subtitle).filter(Subtitle.video_id == cls.video_id).delete()
        v = db.query(Video).filter(Video.id == cls.video_id).first()
        if v:
            db.delete(v)
        db.commit()
        summary_file = Path(settings.summary_dir) / f"{cls.video_id}.md"
        if summary_file.exists():
            summary_file.unlink()

    def test_generate_and_read_summary(self):
        # 生成
        r = client.post(f"/api/videos/{self.video_id}/generate-summary")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "done")
        self.assertIn("洛必达", data["summary"])
        self.assertIn("## 核心公式", data["summary"])

        # 落盘 + 状态
        summary_file = Path(settings.summary_dir) / f"{self.video_id}.md"
        self.assertTrue(summary_file.exists())
        v = db.query(Video).filter(Video.id == self.video_id).first()
        self.assertEqual(v.summary_status, "done")
        self.assertTrue(v.summary_path)

        # 读取端点
        g = client.get(f"/api/videos/{self.video_id}/summary")
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.json()["status"], "done")
        self.assertIn("## 易错点", g.json()["content"])

    def test_summary_requires_subtitle(self):
        # 无字幕的视频：生成应失败且有明确提示
        v = Video(subject="math", filename="test_no_subs.mp4", title="无字幕课",
                  file_path="no_such_file.mp4", subtitle_status="pending",
                  summary_status="none")
        db.add(v)
        db.commit()
        db.refresh(v)
        try:
            r = client.post(f"/api/videos/{v.id}/generate-summary")
            self.assertEqual(r.status_code, 400)
        finally:
            db.delete(v)
            db.commit()


if __name__ == "__main__":
    unittest.main()