# -*- coding: utf-8 -*-
"""
本地识别字幕脚本（local_subtitle.py）
  1. 确保本地平台后端在跑（8000；没跑则自动启动）
  2. 扫描本地 videos 里尚未识别字幕的视频（subtitle_status != done）
  3. 逐个调用 POST /api/videos/{id}/extract-subtitle 触发识别（串行），完成后校验
用法：双击「本地识别字幕.bat」，或 python scripts/local_subtitle.py
"""
import os
import io
import sys
import json
import time
import socket
import sqlite3
import subprocess

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gb"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

_orig_print = print
def _pf(*a, **k):
    k.setdefault("flush", True)
    _orig_print(*a, **k)
print = _pf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "kaoyan.db")
BASE = "http://127.0.0.1:8000"
PORT = 8000


def _port_open(port):
    s = socket.socket()
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_backend():
    """本地后端没跑就启动（后台），等待 /api/health 就绪"""
    if _port_open(PORT):
        print("本地平台已在运行 (127.0.0.1:%d)" % PORT)
        return True
    print("本地平台未运行，正在启动…")
    log = os.path.join(ROOT, "data", "uvicorn_auto.log")
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--host", "127.0.0.1", "--port", str(PORT)]
    try:
        subprocess.Popen(cmd, cwd=os.path.join(ROOT, "backend"),
                         stdout=open(log, "w", encoding="utf-8"),
                         stderr=subprocess.STDOUT, close_fds=True)
    except Exception as e:
        print("  [错误] 启动失败:", e)
        return False
    for _ in range(60):
        time.sleep(1)
        try:
            import urllib.request
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    print("本地平台已就绪")
                    return True
        except Exception:
            pass
    print("  [错误] 等待后端就绪超时，请手动 python -m uvicorn app.main:app --app-dir backend 启动后重试")
    return False


def get_pending():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, title, subtitle_status FROM videos WHERE subtitle_status != 'done' AND file_path IS NOT NULL"
        " AND file_path != '' ORDER BY id").fetchall()
    con.close()
    return rows


def api_post(path):
    import urllib.request
    req = urllib.request.Request(BASE + path, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=3600 * 4) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def check_status(video_id):
    import urllib.request
    try:
        with urllib.request.urlopen(BASE + "/api/videos/%s/subtitles" % video_id, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
            return d.get("subtitle_status")
    except Exception:
        return None


def main():
    pending = get_pending()
    if not pending:
        print("没有需要识别的视频（全部已识别 done）。")
        return
    print("待识别 %d 个:" % len(pending))
    for vid, title, st in pending:
        print("  #%d %s [%s]" % (vid, (title or "")[:40], st))
    if not ensure_backend():
        sys.exit(1)

    ok, fail = 0, 0
    for vid, title, st in pending:
        print("== 识别 #%d %s ==" % (vid, (title or "")[:40]), flush=True)
        try:
            resp = api_post("/api/videos/%d/extract-subtitle" % vid)
            print("  触发返回:", str(resp)[:100], flush=True)
        except Exception as e:
            print("  触发异常:", e, flush=True)
            # 已在处理中或锁等待：继续轮询状态
        # 轮询直到 done / failed
        for _ in range(240 * 20):  # 最长约 8 小时（实际更快）
            st = check_status(vid)
            if st == "done":
                print("  ✅ 完成", flush=True)
                ok += 1
                break
            if st in ("failed",):
                print("  ❌ 识别失败", flush=True)
                fail += 1
                break
            time.sleep(3)
        else:
            print("  ⏳ 超时未完成", flush=True)
            fail += 1
    print("===== 完成：成功 %d / 失败 %d =====" % (ok, fail))
    print("识别好的字幕可直接用「上传字幕到云端.bat」传到服务器。")


if __name__ == "__main__":
    main()