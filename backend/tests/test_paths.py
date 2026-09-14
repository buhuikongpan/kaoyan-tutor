# -*- coding: utf-8 -*-
"""路径层测试：相对/绝对互转 + 失效路径按文件名重定位。

覆盖的真实事故：项目搬家后库里的绝对路径全失效（视频播不了），
只要文件还在 storage/ 下，就能按文件名找回来并改存相对路径。

运行方式（在 backend 目录下）：
    python -m unittest tests.test_paths -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.core.config import BASE_DIR  # noqa: E402
from app.core.paths import exists, relocate, resolve, to_abs, to_rel  # noqa: E402


class PathsTest(unittest.TestCase):
    def setUp(self):
        # 在真实 storage/ 下建一个临时文件，避免动到真实数据
        self.tmpdir = Path(BASE_DIR) / "storage" / "videos" / "_test_paths_tmp"
        self.tmpdir.mkdir(parents=True, exist_ok=True)
        self.f = self.tmpdir / "sample_搬家测试.mp4"
        self.f.write_bytes(b"x")
        self.rel = "storage/videos/_test_paths_tmp/sample_搬家测试.mp4"

    def tearDown(self):
        try:
            self.f.unlink()
            self.tmpdir.rmdir()
        except OSError:
            pass

    def test_relative_roundtrip(self):
        """相对路径 → 绝对 → 相对，必须原样回来"""
        self.assertEqual(to_rel(self.rel), self.rel)
        self.assertEqual(to_abs(self.rel), Path(BASE_DIR) / "storage/videos/_test_paths_tmp/sample_搬家测试.mp4")
        self.assertEqual(to_rel(to_abs(self.rel)), self.rel)

    def test_absolute_to_relative(self):
        """项目内的绝对路径自动压成相对路径（这就是"搬家后不用改库"的关键）"""
        abs_path = str(Path(BASE_DIR) / "storage" / "videos" / "math" / "a.mp4")
        self.assertEqual(to_rel(abs_path), "storage/videos/math/a.mp4")

    def test_backslash_normalized(self):
        """Windows 反斜杠写法要归一成正斜杠"""
        self.assertEqual(to_rel(r"storage\videos\math\a.mp4"), "storage/videos/math/a.mp4")

    def test_empty_is_safe(self):
        """空值和 None 不能炸，也不能被当成「当前目录」"""
        for v in (None, "", "   "):
            self.assertEqual(to_rel(v), "")
            self.assertIsNone(to_abs(v))
            self.assertFalse(exists(v))
            self.assertIsNone(resolve(v))

    def test_exists_and_resolve(self):
        self.assertTrue(exists(self.rel))
        self.assertFalse(exists("storage/videos/_test_paths_tmp/不存在.mp4"))
        self.assertEqual(resolve(self.rel), self.f)

    def test_relocate_after_move(self):
        """核心场景：记录里的绝对路径已失效（项目搬走了），按文件名仍能找回"""
        dead = r"C:\Users\Administrator\Desktop\DIFY考研学习平台\本地文件\storage\videos\_test_paths_tmp\sample_搬家测试.mp4"
        self.assertFalse(exists(dead))
        self.assertEqual(relocate(dead), self.rel)   # 找回并转成相对路径
        self.assertEqual(resolve(dead), self.f)

    def test_relocate_missing_returns_empty(self):
        """确实找不到的：返回空串（调用方据此保留原值，不破坏数据）"""
        self.assertEqual(relocate("storage/videos/_test_paths_tmp/never_exists.mp4"), "")


if __name__ == "__main__":
    unittest.main()
