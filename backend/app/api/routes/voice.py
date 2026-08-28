"""语音输入接口：浏览器录音 → 当前配置的 ASR 引擎（本地 Whisper / 千问云）转文字 → 返回文本。

与视频字幕提取共用同一套 ASR 配置（设置页「🎬 语音识别 ASR」），
不占用视频字幕的串行锁（短视频转写很快，互不阻塞）。
"""
import os
import tempfile

from fastapi import APIRouter, UploadFile, File, HTTPException

from ...services.asr_service import transcribe_audio, resolve_voice_engine

router = APIRouter(prefix="/api/voice", tags=["语音输入"])

# 录音上限：25MB（webm/opus 约几分钟，远超单句语音输入需要）
MAX_VOICE_BYTES = 25 * 1024 * 1024
ALLOWED_EXTS = {"webm", "wav", "mp3", "m4a", "ogg", "mp4", "aac", "flac"}


@router.post("/transcribe")
async def transcribe_voice(file: UploadFile = File(...)):
    """上传一段录音，用当前配置的 ASR 引擎识别为文字。

    返回 {"text": "识别出的文本"}；出错返回 4xx/5xx 与中文提示。
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "录音内容为空")
    if len(data) > MAX_VOICE_BYTES:
        raise HTTPException(400, "录音过大（上限 25MB，约几分钟）")

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTS:
        ext = "webm"  # 未知后缀按 webm 处理（MediaRecorder 默认输出）

    tmp_path = None
    try:
        # 本地 Whisper 需要音频文件路径（faster-whisper 直接吃文件）；
        # 千问云只需要字节流。两种情况都传，覆盖两种引擎。
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            tmp_path = f.name
            f.write(data)

        # 语音输入走设置页独立配置的引擎（voice_provider：auto/local/qwen），
        # 与视频字幕的全局 ASR 引擎解耦
        engine = resolve_voice_engine()
        result = await transcribe_audio(data, audio_format=ext, audio_path=tmp_path,
                                        provider=engine)
        if not result or not result.get("chunks"):
            raise HTTPException(500, "识别无结果")
        text = "".join(c.get("text", "") for c in result["chunks"]).strip()
        if not text:
            raise HTTPException(500, "未识别到语音内容（请靠近麦克风重试）")
        return {"text": text}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"语音识别失败: {str(e)}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass