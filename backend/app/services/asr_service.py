"""语音识别 — 千问 ASR（Qwen3-ASR-Flash，支持 base64 直接上传）"""
import os
import json
import re
import base64
import subprocess
import tempfile
import asyncio
import httpx
from typing import List, Tuple, Optional
from ..core.config import settings

QWEN_ASR_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
CHUNK_SEC = 50


def detect_silences(audio_path: str) -> List[Tuple[float, float]]:
    """用 ffmpeg 检测静音段，返回 [(silence_start, silence_end), ...]
    只返回 >= 0.5 秒的停顿（自然话与话之间的间隔）
    """
    result = subprocess.run([
        "ffmpeg", "-i", audio_path,
        "-af", "silencedetect=noise=-45dB:d=0.2",

        "-f", "null", "-",
    ], capture_output=True, text=True, timeout=3600)

    starts = []
    ends = []
    for line in result.stderr.split("\n"):
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            starts.append(float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m:
            ends.append(float(m.group(1)))

    silences = []
    for s, e in zip(starts, ends):
        if e - s >= 0.6:
            silences.append((s, e))
    return silences


def audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）"""
    r = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
    ], capture_output=True, text=True, timeout=30)
    return float(r.stdout.strip()) if r.stdout else 0
def _convert_to_pcm_segments(audio_path: str, output_dir: str) -> List[Tuple[str, int]]:
    """把音频转成 16kHz WAV 并按 50 秒切分"""
    full_wav = os.path.join(output_dir, "full.wav")
    subprocess.run([
        "ffmpeg", "-y", "-i", audio_path,
        "-ar", "16000", "-ac", "1", "-f", "wav", full_wav,
    ], capture_output=True, timeout=3600)

    # WAV: 16bit 单声道 16kHz → 每秒 32000 字节
    chunk_bytes = CHUNK_SEC * 16000 * 2
    chunks = []
    with open(full_wav, "rb") as f:
        # 跳过 WAV 头（44 字节）
        header = f.read(44)
        idx = 0
        while True:
            data = f.read(chunk_bytes)
            if not data:
                break
            # 每段加上 WAV 头
            path = os.path.join(output_dir, f"chunk_{idx:03d}.wav")
            with open(path, "wb") as cf:
                # 修改 WAV 头的 data size
                wav_header = bytearray(header)
                data_size = len(data)
                wav_header[4:8] = (36 + data_size).to_bytes(4, 'little')
                wav_header[40:44] = data_size.to_bytes(4, 'little')
                cf.write(bytes(wav_header))
                cf.write(data)
            chunks.append((path, idx))
            idx += 1

    os.remove(full_wav)
    return chunks


async def _transcribe_chunk(audio_data: bytes) -> Optional[str]:
    """用千问 ASR 识别一段音频"""
    audio_b64 = base64.b64encode(audio_data).decode("utf-8")
    data_uri = f"data:audio/wav;base64,{audio_b64}"

    headers = {
        "Authorization": f"Bearer {settings.qwen_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "qwen3-asr-flash",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"audio": data_uri},
                        {"text": ""},
                    ],
                }
            ]
        },
        "parameters": {
            "result_format": "text",
        },
    }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(QWEN_ASR_URL, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"千问 ASR 失败: {resp.status_code} {resp.text[:300]}")
        result = resp.json()
        # 从返回中提取文本
        try:
            text = result["output"]["choices"][0]["message"]["content"][0]["text"]
            return text.strip()
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"千问 ASR 解析失败: {result}")


async def transcribe_audio(
    audio_data: bytes,
    audio_format: str = "mp3",
) -> dict:
    """千问 ASR 转写 + 静音检测
    返回: {"chunks": [{"start": float, "text": str}, ...], "duration": float}
    start 是 50 秒段落的起始时间，text 是该段的转写结果
    """
    tmpdir = tempfile.mkdtemp()
    audio_path = os.path.join(tmpdir, f"input.{audio_format}")
    with open(audio_path, "wb") as f:
        f.write(audio_data)

    try:
        # 1. 获取音频总时长
        dur = audio_duration(audio_path)

        # 2. 检测静音
        silences = detect_silences(audio_path)

        # 3. ASR 转写（按 50 秒分段）
        segments = _convert_to_pcm_segments(audio_path, tmpdir)
        os.remove(audio_path)

        chunks = []
        for wav_path, chunk_idx in segments:
            with open(wav_path, "rb") as f:
                wav_data = f.read()
            text = await _transcribe_chunk(wav_data)
            os.remove(wav_path)
            if text:
                chunks.append({
                    "start": chunk_idx * CHUNK_SEC,
                    "text": text.strip(),
                })

        try:
            os.rmdir(tmpdir)
        except:
            pass

        if not chunks:
            raise RuntimeError("所有段落识别失败")

        return {"chunks": chunks, "duration": dur, "silences": silences}

    except Exception as e:
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except:
            pass
        raise
