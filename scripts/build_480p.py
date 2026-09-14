# -*- coding: utf-8 -*-
"""
批量压缩 480p（build_480p.py）
  把 storage/videos/{科目}/*.mp4 压成 storage/videos_480p/{科目}/*.mp4
  标准：480p 高度@15fps@libx264 CRF30, maxrate 500k bufsize 1000k, aac 96k, faststart
  幂等：目标已存在且>0 则跳过；可断点续跑
用法：python scripts/build_480p.py   （或用根目录 bat）
"""
import os
import sys
import io
import subprocess

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gb"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
_orig_print = print
def _pf(*a, **k):
    k.setdefault("flush", True)
    _orig_print(*a, **k)
print = _pf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "storage", "videos")
OUT = os.path.join(ROOT, "storage", "videos_480p")
SUBJECTS = ["math", "english", "politics", "zhuanye"]


def _has_nvenc() -> bool:
    """检测 ffmpeg 是否支持 h264_nvenc（NVIDIA 硬编码）"""
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        return "h264_nvenc" in (r.stdout or "")
    except Exception:
        return False


# 本机是否可用 NVIDIA 硬编码（启动时探测一次）
NVENC = _has_nvenc()


def transcode(src, out, force_cpu=False):
    """480p 压缩：优先 NVIDIA 硬编（NVENC + CUDA 硬解），无显卡或失败时回退 CPU libx264。

    NVENC 用 -cq 30 等价于 CPU 的 CRF30；preset p4 平衡速度/画质。
    force_cpu=True 强制走 CPU（单个文件 NVENC 失败时的兜底重试）。
    """
    if NVENC and not force_cpu:
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-hwaccel", "cuda", "-i", src,
               "-vf", "fps=15,scale=-2:480",
               "-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "30", "-preset", "p4",
               "-maxrate", "500k", "-bufsize", "1000k",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
               out]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", src,
               "-vf", "fps=15,scale=-2:480",
               "-c:v", "libx264", "-crf", "30", "-preset", "medium",
               "-maxrate", "500k", "-bufsize", "1000k",
               "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
               out]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    if not subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0:
        print("[错误] 未找到 ffmpeg")
        sys.exit(1)
    print("编码器：%s" % ("NVIDIA NVENC 硬编码（CUDA 硬解）" if NVENC else "libx264 CPU 软编码（未检测到 NVENC）"))
    total_ok, total_fail, total_skip = 0, 0, 0
    for subject in SUBJECTS:
        sdir = os.path.join(SRC, subject)
        if not os.path.isdir(sdir):
            continue
        odir = os.path.join(OUT, subject)
        os.makedirs(odir, exist_ok=True)
        for fn in sorted(os.listdir(sdir)):
            if not fn.lower().endswith((".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv")):
                continue
            src = os.path.join(sdir, fn)
            out = os.path.join(odir, fn)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                print("  [跳过] 已有 480p: %s/%s" % (subject, fn))
                total_skip += 1
                continue
            before = os.path.getsize(src) / 1048576
            print("  [转码] %s/%s (%dMB) ..." % (subject, fn, before))
            r = transcode(src, out)
            # NVENC 失败时自动回退 CPU 重试一次（个别编码参数不被硬编支持的兜底）
            if r.returncode != 0 and NVENC:
                print("        [硬编失败，回退 CPU 重试] %s" % (r.stderr or "").strip()[-150:])
                if os.path.exists(out):
                    try: os.remove(out)
                    except OSError: pass
                r = transcode(src, out, force_cpu=True)
            if r.returncode == 0:
                after = os.path.getsize(out) / 1048576
                print("        ✅ %dMB -> %dMB" % (before, after))
                total_ok += 1
            else:
                print("        [失败] %s" % (r.stderr or "").strip()[-200:])
                # 清理半成品，避免下次误判为已完成
                if os.path.exists(out):
                    try: os.remove(out)
                    except OSError: pass
                total_fail += 1
    print()
    print("===== 压缩完成：成功 %d / 跳过 %d / 失败 %d =====" % (total_ok, total_skip, total_fail))
    size_mb = 0
    for subj in SUBJECTS:
        d = os.path.join(OUT, subj)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                p = os.path.join(d, fn)
                if os.path.isfile(p):
                    size_mb += os.path.getsize(p)
    print("480p 目录总量约 %.1f MB（%d 个）" % (size_mb / 1048576, total_ok + total_skip))


if __name__ == "__main__":
    main()