"""视频管理接口"""
import os
import re
import shutil
import subprocess
import asyncio
import datetime
from pathlib import Path
from typing import List
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db, Video, Subtitle
from ..services.asr_service import transcribe_audio


router = APIRouter(prefix="/api/videos", tags=["视频管理"])

# 全局串行锁：同一时刻只允许一个视频在执行字幕提取（ASR），避免并发触发云接口限流
_extract_lock = asyncio.Lock()

SUBJECTS = {"math": "数学", "english": "英语", "politics": "政治"}


def _get_video_duration(file_path: str) -> float:
    """用 ffprobe 获取视频时长（秒）"""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ], capture_output=True, text=True, timeout=60)
        if result.stdout:
            return float(result.stdout.strip())
    except:
        pass
    return 0.0


def rescan_videos(db: Session = Depends(get_db)):
    """扫描 storage/videos 目录恢复数据库"""
    import glob
    added = 0
    for subject in ["math", "english", "politics"]:
        pattern = os.path.join(settings.video_dir, subject, "*")
        for file_path in glob.glob(pattern):
            filename = os.path.basename(file_path)
            if not filename.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv')):
                continue
            if db.query(Video).filter(Video.file_path == file_path).first():
                continue
            sort_order = 9999
            nums = re.findall(r'(\d+)', Path(filename).stem)
            if nums:
                sort_order = int(nums[-1])
            duration = _get_video_duration(file_path)
            video = Video(
                subject=subject,
                filename=filename,
                title=Path(filename).stem,
                file_path=file_path,
                file_size=os.path.getsize(file_path),
                duration=duration,
                sort_order=sort_order,
                subtitle_status="pending",
            )
            db.add(video)
            added += 1
    db.commit()
    return {"message": f"扫描完成，新增 {added} 个视频"}


@router.post("/rescan")
def rescan(db: Session = Depends(get_db)):
    return rescan_videos(db)


@router.get("/subjects")
def get_subjects():
    return {"subjects": SUBJECTS}


async def _save_single_video(
    subject: str,
    file: UploadFile,
    title: str,
    db: Session,
) -> dict:
    """保存单个视频到磁盘和数据库"""
    subject_dir = Path(settings.video_dir) / subject
    subject_dir.mkdir(parents=True, exist_ok=True)

    # 从文件名提取序号（取最后一位数字，如 "01_导数.mp4" → 1）
    sort_order = 9999
    name_no_ext = Path(file.filename).stem
    nums = re.findall(r'(\d+)', name_no_ext)
    if nums:
        sort_order = int(nums[-1])

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # 清理文件名：webkitdirectory 会把路径带进来，如 "文件夹/视频.mp4"
    clean_name = file.filename.replace("/", "_").replace("\\", "_")
    safe_filename = f"{timestamp}_{clean_name}"
    file_path = subject_dir / safe_filename

    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = os.path.getsize(file_path)
    duration = _get_video_duration(file_path)

    video = Video(
        subject=subject,
        filename=clean_name,
        title=title or clean_name,
        file_path=str(file_path),
        file_size=file_size,
        duration=duration,
        subtitle_status="pending",
        sort_order=sort_order,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    return {
        "id": video.id,
        "filename": video.filename,
        "title": video.title,
        "file_size": file_size,
        "subject": subject,
        "subtitle_status": video.subtitle_status,
        "message": "上传成功",
    }


@router.post("/upload")
async def upload_video(
    subject: str = Form(...),
    title: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """上传单个视频"""
    if subject not in SUBJECTS:
        raise HTTPException(400, f"不支持的科目: {subject}")
    return await _save_single_video(subject, file, title, db)


@router.post("/upload-batch")
async def upload_videos_batch(
    subject: str = Form(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """批量上传视频"""
    if subject not in SUBJECTS:
        raise HTTPException(400, f"不支持的科目: {subject}")
    if len(files) > 50:
        raise HTTPException(400, "单次最多上传 50 个视频")

    results = []
    errors = []
    for file in files:
        try:
            result = await _save_single_video(subject, file, "", db)
            results.append(result)
        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})

    return {
        "total": len(files),
        "success": len(results),
        "failed": len(errors),
        "videos": results,
        "errors": errors,
    }


@router.get("/list")
def list_videos(
    subject: str = Query("", description="筛选科目"),
    db: Session = Depends(get_db),
):
    """获取视频列表"""
    query = db.query(Video)
    if subject and subject in SUBJECTS:
        query = query.filter(Video.subject == subject)
    videos = query.order_by(Video.sort_order).all()

    return {
        "videos": [
            {
                "id": v.id,
                "subject": v.subject,
                "subject_name": SUBJECTS.get(v.subject, v.subject),
                "filename": v.filename,
                "title": v.title,
                "file_size": v.file_size,
                "duration": v.duration,
                "sort_order": v.sort_order,
                "subtitle_status": v.subtitle_status,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in videos
        ]
    }


@router.get("/stream/{video_id}")
def stream_video(video_id: int, db: Session = Depends(get_db)):
    """流式播放视频。

    FileResponse（starlette ≥0.36）原生支持 HTTP Range（206 / 416 / If-Range），
    浏览器请求头里自带 Range 即实现拖拽跳播，无需手写分段逻辑。
    """
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    if not os.path.exists(video.file_path):
        raise HTTPException(404, "视频文件不存在")
    return FileResponse(video.file_path, media_type="video/mp4")


@router.get("/{video_id}/subtitles")
def get_subtitles(video_id: int, db: Session = Depends(get_db)):
    """获取视频字幕（含时间戳）"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")

    subtitles = db.query(Subtitle).filter(Subtitle.video_id == video_id).order_by(Subtitle.seq).all()

    return {
        "video_id": video_id,
        "subtitle_status": video.subtitle_status,
        "subtitles": [
            {"seq": s.seq, "start": s.start_time, "end": s.end_time, "text": s.text}
            for s in subtitles
        ],
    }


@router.get("/{video_id}/subtitles.vtt")
def get_subtitles_vtt(video_id: int, db: Session = Depends(get_db)):
    """WebVTT 格式字幕 → HTML5 <track> 实时显示在视频画面上"""
    from fastapi.responses import PlainTextResponse
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    subtitles = db.query(Subtitle).filter(Subtitle.video_id == video_id).order_by(Subtitle.seq).all()
    if not subtitles:
        raise HTTPException(404, "暂无字幕")

    def _fmt(s: float) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h:02d}:{m:02d}:{sec:06.3f}"

    lines = ["WEBVTT\n"]
    for i, sub in enumerate(subtitles, 1):
        lines.append(str(i))
        # line:90% 保证字幕固定在底部同一位置，不会上下跳动
        lines.append(f"{_fmt(sub.start_time)} --> {_fmt(sub.end_time)} line:90%")
        lines.append(sub.text)
        lines.append("")
    return PlainTextResponse("\n".join(lines), media_type="text/vtt; charset=utf-8")


@router.post("/{video_id}/extract-subtitle")
async def extract_subtitle(video_id: int, db: Session = Depends(get_db)):
    """提取视频字幕（ASR）—— 串行处理：全局锁保证同一时刻只有一个视频在识别，
    多个请求自动排队等待，避免并发触发云 ASR 限流/超时
    """
    async with _extract_lock:
        return await _extract_subtitle_impl(video_id, db)


async def _extract_subtitle_impl(video_id: int, db: Session):
    """提取视频字幕（ASR）实际处理体"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")

    # 如果卡在 processing 超过 30 分钟，自动重置
    if video.subtitle_status == "processing":
        if video.created_at:
            elapsed = (datetime.datetime.utcnow() - video.created_at).total_seconds()
            if elapsed < 1800:  # 30分钟以内，还在正常处理
                raise HTTPException(400, "正在提取中，请稍候")
        # 超过 30 分钟，视为卡住，重置重试
        video.subtitle_status = "pending"
        db.commit()

    video.subtitle_status = "processing"
    video.created_at = datetime.datetime.utcnow()  # 刷新开始时间
    db.commit()

    try:
        file_path = video.file_path
        if not os.path.exists(file_path):
            raise HTTPException(404, "视频文件不存在")

        # 1. 用 ffmpeg 提取音频
        audio_path = file_path + ".mp3"
        subprocess.run(
            ["ffmpeg", "-i", file_path, "-vn", "-ar", "16000", "-ac", "1", audio_path, "-y"],
            capture_output=True,
            timeout=3600,
        )

        # 2. 读取音频并调用 ASR（含静音检测）
        with open(audio_path, "rb") as f:
            audio_data = f.read()
        asr_result = await transcribe_audio(audio_data, audio_format="mp3")
        os.remove(audio_path)

        if not asr_result or not asr_result.get("chunks"):
            video.subtitle_status = "failed"
            db.commit()
            raise HTTPException(500, "语音识别失败")

        chunks = asr_result["chunks"]
        silences = asr_result.get("silences", [])
        duration = asr_result.get("duration", 0)

        # 3. 保存原文字幕
        plain_text = "\n".join(c["text"] for c in chunks)
        sub_file = Path(settings.subtitle_dir) / f"{video_id}.txt"
        sub_file.parent.mkdir(parents=True, exist_ok=True)
        sub_file.write_text(plain_text, encoding="utf-8")

        # 4. 字数比例分配 + 静音微调
        db.query(Subtitle).filter(Subtitle.video_id == video_id).delete()
        import re as _re

        # 把所有的 silence 中点收集为候选切分点（忽略开头2秒，一般是片头空白）
        split_candidates = set()
        for s, e in silences:
            mid = (s + e) / 2
            if mid > 2.0:  # 跳过开头空白
                split_candidates.add(mid)

        seq = 0
        # 用音量检测找前 50 秒内人声开始的位置
        voice_start = 0.0
        try:
            import json as _json
            vr = subprocess.run(["ffmpeg", "-i", file_path, "-t", "50",
                "-af", "astats=metadata=1:reset=1", "-f", "null", "-"],
                capture_output=True, text=True, timeout=60)
            rms_values = []
            for line in vr.stderr.split("\n"):
                if "RMS_level" in line or "rms" in line.lower():
                    try:
                        val = float(line.split(":")[-1].strip().replace(" dB", ""))
                        rms_values.append(val)
                    except:
                        pass
            for i, v in enumerate(rms_values):
                if v > -25:
                    voice_start = max(2.0, i - 2)
                    break
        except:
            voice_start = 2.0

        for chunk in chunks:
            offset = chunk["start"]
            text = chunk["text"]
            chunk_end = offset + 50

            # 先按句号+逗号拆，再合并短句
            # 先按句号拆
            by_sent = _re.split(r'(?<=[。？！])', text)
            # 再按逗号拆
            by_comma = []
            for s in by_sent:
                if '，' in s:
                    for p in _re.split(r'(?<=，)', s):
                        if p.strip(): by_comma.append(p.strip())
                else:
                    s2 = s.strip()
                    if s2: by_comma.append(s2)
            # 合并短句（<8字合并到前一段）
            final = []
            for s in by_comma:
                if final and len(s) < 8:
                    final[-1] += s
                else:
                    final.append(s)
            if not final:
                continue

            # 按字符占比分时间
            total_chars = sum(len(s) for s in final)
            effective_start = voice_start if offset == 0 else offset
            est_duration = chunk_end - effective_start
            char_acc = 0
            min_dur = 2.0

            subs_in_chunk = []
            for s in final:
                ratio = len(s) / max(total_chars, 1)
                start = effective_start + (char_acc / max(total_chars, 1)) * est_duration
                dur = max(ratio * est_duration, min_dur)
                end = min(start + dur, chunk_end)
                char_acc += len(s)
                subs_in_chunk.append((start, end, s))

            # 不重叠
            for i in range(len(subs_in_chunk)):
                start, end, s = subs_in_chunk[i]
                if i < len(subs_in_chunk) - 1:
                    end = min(end, subs_in_chunk[i + 1][0])
                else:
                    end = min(end, chunk_end)

                # 微调：找结束时间附近最近的静音中点
                if split_candidates:
                    best = None
                    best_dist = 999
                    for cp in split_candidates:
                        dist = abs(cp - end)
                        if dist < best_dist and dist <= 0.5:
                            best = cp
                            best_dist = dist
                    if best is not None and best > start + 0.5:
                        end = best

                if end - start < 1.5:
                    end = start + 2.0

                db.add(Subtitle(video_id=video_id, subject=video.subject,
                       start_time=round(start,1), end_time=round(end,1),
                       text=s, seq=seq))
                seq += 1

        video.subtitle_path = str(sub_file)
        video.subtitle_status = "done"
        db.commit()

        return {"status": "done", "message": "字幕提取完成"}

    except HTTPException:
        raise
    except Exception as e:
        video.subtitle_status = "failed"
        db.commit()
        raise HTTPException(500, f"字幕提取失败: {str(e)}")


@router.get("/{video_id}/subtitle-file")
def download_subtitle_file(video_id: int, db: Session = Depends(get_db)):
    """下载字幕文件（用于手动上传到 Dify 知识库）"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    if video.subtitle_status != "done" or not video.subtitle_path:
        raise HTTPException(400, "字幕尚未生成")
    if not os.path.exists(video.subtitle_path):
        raise HTTPException(404, "字幕文件不存在")
    return FileResponse(
        video.subtitle_path,
        media_type="text/plain",
        filename=f"{video.subject}_{video.title}_字幕.txt",
    )


@router.put("/reorder")
def reorder_videos(data: dict, db: Session = Depends(get_db)):
    """批量更新视频排序"""
    orders = data.get("orders", {})  # {video_id: sort_order}
    for vid, order in orders.items():
        video = db.query(Video).filter(Video.id == int(vid)).first()
        if video:
            video.sort_order = int(order)
    db.commit()
    return {"message": "排序已更新"}


@router.delete("/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    """删除视频"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    if os.path.exists(video.file_path):
        os.remove(video.file_path)
    db.query(Subtitle).filter(Subtitle.video_id == video_id).delete()
    db.delete(video)
    db.commit()
    return {"message": "删除成功"}
