"""语音识别 — 支持本地 faster-whisper 与千问云 API 双 provider。

provider 取值（model_config["asr"]["provider"]）：
  - "local"（默认）：本地 faster-whisper，免费/离线，GPU 加速，给出逐句精确时间轴
  - "qwen"：千问 qwen3-asr-flash 云 API（按 50 秒分段）

本地识别失败（模型未下载 / GPU cuDNN 缺失等）会自动回退千问云，
避免字幕"全部 failed"（此前出现过被加速器 SSL 拦截拖垮云 API 的事故）。
"""
import os
import re
import base64

# HuggingFace 国内加速：默认走 hf-mirror.com 镜像下载 whisper 模型
# （官方 huggingface.co 在国内常被代理/加速器 TLS 拦截报 SSL 证书错误）。
# HF_HUB_DISABLE_XET=1：hf-mirror 的 Xet 加速通道不兼容（会 401），禁掉走普通 HTTP。
# 已设置 HF_ENDPOINT / HF_HUB_DISABLE_XET 环境变量时尊重用户自己的配置。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
import subprocess
import tempfile
import asyncio
import httpx
from typing import List, Tuple, Optional
from ..core.config import settings
from ..core.model_config import load_model_config, get_endpoint

# 千问 multimodal-generation 端点（base_url 后拼接的服务路径）
QWEN_ASR_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
CHUNK_SEC = 50

# 本地 faster-whisper 模型缓存：{size: WhisperModel}，避免每个视频重复加载模型
_MODEL_CACHE: dict = {}


def _asr_config():
    cfg = load_model_config()["asr"]
    return cfg, get_endpoint(cfg.get("base_url"), QWEN_ASR_PATH)


def _asr_provider():
    """返回 (provider, size)。provider: local/qwen；size: 模型档位。
    档位说明：small（快）、medium（更准）、large-v3-turbo（快+准，推荐，
    速度接近 small、准确度接近 large-v3）、large-v3（最准最慢）、
    distil-large-v3（最快，精度接近 large-v3）。
    """
    cfg = load_model_config()["asr"]
    provider = (cfg.get("provider") or "local").strip().lower()
    size = (cfg.get("size") or "small").strip().lower()
    if provider not in ("local", "qwen"):
        provider = "local"
    if size not in ("small", "medium", "large-v3", "large-v3-turbo", "distil-large-v3"):
        size = "small"
    return provider, size


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
    """用 ASR 模型识别一段音频（默认千问）"""
    _cfg, _endpoint = _asr_config()
    audio_b64 = base64.b64encode(audio_data).decode("utf-8")
    data_uri = f"data:audio/wav;base64,{audio_b64}"

    headers = {
        "Authorization": f"Bearer {_cfg.get('api_key') or settings.qwen_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _cfg.get("model") or "qwen3-asr-flash",
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
        resp = await client.post(_endpoint, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"ASR 失败: {resp.status_code} {resp.text[:300]}")
        result = resp.json()
        # 从返回中提取文本
        try:
            text = result["output"]["choices"][0]["message"]["content"][0]["text"]
            return text.strip()
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"千问 ASR 解析失败: {result}")


def _get_local_model(size: str):
    """懒加载并缓存 faster-whisper 模型（避免每个视频重复加载模型）"""
    if size not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        # device="auto" 优先 GPU（RTX 4070），compute_type="auto" 自动选 fp16/int8
        _MODEL_CACHE[size] = WhisperModel(size, device="auto", compute_type="auto")
    return _MODEL_CACHE[size]


def _run_local(audio_path: str, size: str) -> dict:
    """同步执行本地识别（在后台线程里跑，不阻塞事件循环）"""
    model = _get_local_model(size)
    # vad_filter 自动去静音，beam_size 提升准确度，language 锁定中文
    segments, info = model.transcribe(
        audio_path, language="zh", vad_filter=True, beam_size=5,
    )
    chunks = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        chunks.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": text,
        })
    silences = detect_silences(audio_path)
    if not chunks:
        raise RuntimeError("本地识别无结果")
    return {
        "chunks": chunks,
        "duration": getattr(info, "duration", None) or 0,
        "silences": silences,
        "precise": True,   # 标记：逐句精确时间轴，videos.py 据此走精确分支
        "engine": "local",
    }


async def _transcribe_local(audio_path: str, size: str) -> dict:
    """本地 faster-whisper 识别（放到后台线程，避免阻塞事件循环）"""
    return await asyncio.to_thread(_run_local, audio_path, size)


async def _transcribe_qwen(audio_data: bytes, audio_format: str) -> dict:
    """千问 ASR 转写 + 静音检测（云 API，按 50 秒分段）
    返回: {"chunks": [{"start": float, "text": str}, ...], "duration": float, "engine": "qwen"}
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

        return {"chunks": chunks, "duration": dur, "silences": silences, "engine": "qwen"}

    except Exception as e:
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except:
            pass
        raise


async def transcribe_audio(
    audio_data: bytes,
    audio_format: str = "mp3",
    audio_path: Optional[str] = None,
) -> dict:
    """按 provider 选择识别引擎。

    provider=local：本地 faster-whisper（需 audio_path），失败自动回退千问云；
    provider=qwen：走云 API。

    返回: {"chunks":[...], "duration":..., "silences":[...], "engine": str}
      local 的 chunks 每项含 start/end（精确时间轴，precise=True）；
      qwen 的 chunks 是 50 秒分段（start 为段起点）。
    """
    provider, size = _asr_provider()
    if provider == "local":
        if audio_path and os.path.exists(audio_path):
            try:
                return await _transcribe_local(audio_path, size)
            except Exception as e:
                # 本地失败（模型未下载 / GPU 问题等）→ 回退云，保证字幕可用
                print(f"[asr] 本地识别失败，回退千问云：{e}")
        else:
            print("[asr] provider=local 但未提供 audio_path，回退千问云")
    return await _transcribe_qwen(audio_data, audio_format)
