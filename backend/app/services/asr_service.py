"""语音识别 — 多引擎：本地 faster-whisper + 千问 / 智谱 / 腾讯云 / 硅基流动 云 API。

provider 取值（model_config["asr"]["provider"]）：
  - "auto"（默认）：自动降级链 智谱 → 千问 → 腾讯云 → 硅基流动 → 本地
    （按已配置 key 的引擎依次尝试；本地仅在上面的云全部失败 / 无 key 时兜底）
  - "local"：本地 faster-whisper（免费/离线，GPU 加速，逐句精确时间轴）。
    显式选 local = **只用本地**，失败即报错，不再静默回退云引擎（要云兜底请用 auto）
  - "qwen"：千问 qwen3-asr-flash 云 API（按 50 秒分段）
  - "zhipu"：智谱 GLM-ASR-2512（按 30 秒分段，multipart 上传，支持 hotwords）
  - "tencent"：腾讯云实时语音识别·极速版（按 60 秒分段，TC3-HMAC-SHA256 签名）
  - "siliconflow"：硅基流动 SenseVoiceSmall（按 50 秒分段，multipart，OpenAI 兼容）

auto / 云链模式下，某个引擎失败会自动尝试链上的下一个，避免字幕/语音输入"全部 failed"
（此前出现过被加速器 SSL 拦截拖垮云 API 的事故）。
注意：云引擎只返回整段文本、**没有句子级时间戳**，时间轴由 videos.py 按字数估算，
必然有秒级偏差 —— 这也是"字幕比音频滞后"的根源，要精确时间轴请用 local。
"""
import os
import re
import time
import base64
import hashlib
import hmac
import json
import datetime

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

# ============ 引擎注册表 ============
# mode: qwen（JSON+base64）| multipart（OpenAI 兼容表单上传）| tencent（TC3 签名）
ASR_ENGINES = {
    "qwen": {
        "name": "千问", "chunk_sec": 50, "mode": "qwen",
        "base": "https://dashscope.aliyuncs.com",
        "endpoint": "/api/v1/services/aigc/multimodal-generation/generation",
        "default_model": "qwen3-asr-flash",
    },
    "zhipu": {
        "name": "智谱", "chunk_sec": 30, "mode": "multipart",
        "base": "https://open.bigmodel.cn",
        "endpoint": "/api/paas/v4/audio/transcriptions",
        "default_model": "glm-asr-2512",
    },
    "tencent": {
        "name": "腾讯云", "chunk_sec": 60, "mode": "tencent",
        "base": "https://asr.tencentcloudapi.com",
        "default_model": "16k_zh",   # 实时语音识别极速版引擎类型（普通话）
    },
    "siliconflow": {
        "name": "硅基流动", "chunk_sec": 50, "mode": "multipart",
        "base": "https://api.siliconflow.cn",
        "endpoint": "/v1/audio/transcriptions",
        "default_model": "FunAudioLLM/SenseVoiceSmall",
    },
}

# 自动降级链优先级（用户确认：智谱 → 千问 → 本地；腾讯/硅基在有 key 时参与）
CLOUD_ORDER = ["zhipu", "qwen", "tencent", "siliconflow"]
ALLOWED_PROVIDERS = ("local",) + tuple(ASR_ENGINES)

QWEN_ASR_PATH = "/api/v1/services/aigc/multimodal-generation/generation"

# 本地 faster-whisper 模型缓存：{size: WhisperModel}，避免每个视频重复加载模型
_MODEL_CACHE: dict = {}


def _asr_cfg() -> dict:
    return load_model_config()["asr"]


def _asr_provider():
    """返回 (provider, size)。provider: auto/local/qwen/zhipu/tencent/siliconflow；
    size: 本地模型档位（small/medium/large-v3-turbo/...）。
    """
    cfg = _asr_cfg()
    provider = (cfg.get("provider") or "auto").strip().lower()
    size = (cfg.get("size") or "small").strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        provider = "auto"
    if size not in ("small", "medium", "large-v3", "large-v3-turbo", "distil-large-v3"):
        size = "small"
    return provider, size


def _engine_conf(engine: str) -> dict:
    """返回某引擎的凭据配置（缺失时补空 dict）"""
    eng = _asr_cfg().get("engines") or {}
    conf = eng.get(engine)
    return conf if isinstance(conf, dict) else {}


def _engine_is_configured(engine: str) -> bool:
    """该引擎是否已配置可用凭据（auto 降级链据此跳过未配置的引擎）"""
    conf = _engine_conf(engine)
    if engine == "tencent":
        return bool((conf.get("secret_id") or "").strip()
                    and (conf.get("secret_key") or "").strip())
    return bool((conf.get("api_key") or "").strip())


def _engine_model(engine: str) -> str:
    """引擎实际调用的模型名：千问允许用户配置（asr.model 或旧顶层 model），其余用默认"""
    cfg = _asr_cfg()
    conf = _engine_conf(engine)
    meta = ASR_ENGINES[engine]
    if conf.get("model"):
        return conf["model"]
    if engine == "qwen":
        return (cfg.get("model") or "").strip() or meta["default_model"]
    return meta["default_model"]


def _cloud_chain() -> List[str]:
    """已配置 key 的云引擎，按优先级：智谱 → 千问 → 腾讯 → 硅基流动"""
    return [e for e in CLOUD_ORDER if _engine_is_configured(e)]


def detect_silences(audio_path: str) -> List[Tuple[float, float]]:
    """用 ffmpeg 检测静音段，返回 [(silence_start, silence_end), ...]
    只返回 >= 0.5 秒的停顿（自然话与话之间的间隔）

    注意：Windows 上 ffmpeg 的 stderr 含中文路径/非 GBK 字节，subprocess 若用系统默认
    编码（gbk）解码会在读取线程抛 UnicodeDecodeError，导致 result.stderr 变成 None，
    随后 .split() 抛 AttributeError —— 曾因此让本地 Whisper 整条路径静默降级到云引擎。
    这里强制 utf-8 + errors="replace"，并把 stderr 兜底成空串。
    """
    result = subprocess.run([
        "ffmpeg", "-i", audio_path,
        "-af", "silencedetect=noise=-45dB:d=0.2",

        "-f", "null", "-",
    ], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=3600)

    starts = []
    ends = []
    for line in (result.stderr or "").split("\n"):
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            starts.append(float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m:
            ends.append(float(m.group(1)))

    silences = []
    for s, e in zip(starts, ends):
        if e - s >= 0.5:
            silences.append((s, e))
    return silences


def audio_duration(audio_path: str) -> float:
    """获取音频时长（秒）"""
    r = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
    ], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30)
    return float(r.stdout.strip()) if r.stdout else 0


def _std_wav_header(data_size: int) -> bytes:
    """构造标准 44 字节 PCM WAV 头（16kHz 单声道 16bit）：
    不依赖输入文件头——ffmpeg 输出的 WAV 常带 LIST 扩展块，截断/重写易产生
    非标准头，被智谱等严格校验的服务端拒绝（code 1214）。
    """
    hdr = bytearray(44)
    hdr[0:4] = b"RIFF"
    hdr[4:8] = (36 + data_size).to_bytes(4, "little")
    hdr[8:12] = b"WAVE"
    hdr[12:16] = b"fmt "
    hdr[16:20] = (16).to_bytes(4, "little")      # fmt 块长
    hdr[20:22] = (1).to_bytes(2, "little")       # PCM
    hdr[22:24] = (1).to_bytes(2, "little")       # 单声道
    hdr[24:28] = (16000).to_bytes(4, "little")   # 采样率
    hdr[28:32] = (32000).to_bytes(4, "little")   # 字节率 = 16000*2
    hdr[32:34] = (2).to_bytes(2, "little")       # 块对齐
    hdr[34:36] = (16).to_bytes(2, "little")      # 位深
    hdr[36:40] = b"data"
    hdr[40:44] = data_size.to_bytes(4, "little")
    return bytes(hdr)


def _wav_data_offset(full_wav: str) -> int:
    """定位 WAV 中 data 块的数据起始偏移（兼容带 LIST 等扩展块的头）"""
    with open(full_wav, "rb") as f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise RuntimeError("不是标准 WAV 文件")
        pos = 12
        while True:
            blk = f.read(8)
            if len(blk) < 8:
                raise RuntimeError("WAV 头解析失败（找不到 data 块）")
            cid, csz = blk[:4], int.from_bytes(blk[4:8], "little")
            if cid == b"data":
                return pos + 8
            pos += 8 + csz + (csz % 2)   # 块长对齐到偶数
            f.seek(pos)


def _convert_to_pcm_segments(audio_path: str, output_dir: str, chunk_sec: int = 50) -> List[Tuple[str, int]]:
    """把音频转成 16kHz WAV 并按 chunk_sec 秒切分（每段重建标准 44 字节 WAV 头）"""
    full_wav = os.path.join(output_dir, "full.wav")
    subprocess.run([
        "ffmpeg", "-y", "-i", audio_path,
        "-ar", "16000", "-ac", "1", "-f", "wav", full_wav,
    ], capture_output=True, timeout=3600)

    # WAV: 16bit 单声道 16kHz → 每秒 32000 字节
    chunk_bytes = chunk_sec * 16000 * 2
    chunks = []
    data_pos = _wav_data_offset(full_wav)
    with open(full_wav, "rb") as f:
        f.seek(data_pos)   # 跳过任意长度的 WAV 头，直接读音频数据
        idx = 0
        while True:
            data = f.read(chunk_bytes)
            if not data:
                break
            # 每段用标准头重建，兼容各云服务端的严格校验
            path = os.path.join(output_dir, f"chunk_{idx:03d}.wav")
            with open(path, "wb") as cf:
                cf.write(_std_wav_header(len(data)))
                cf.write(data)
            chunks.append((path, idx))
            idx += 1

    os.remove(full_wav)
    return chunks


async def _transcribe_qwen_chunk(audio_data: bytes) -> Optional[str]:
    """千问 ASR：JSON + base64 data URI（50 秒分段调用）"""
    cfg = _asr_cfg()
    base_url = (cfg.get("base_url") or "").strip() or ASR_ENGINES["qwen"]["base"]
    endpoint = get_endpoint(base_url, QWEN_ASR_PATH)
    api_key = _engine_conf("qwen").get("api_key") or cfg.get("api_key") or settings.qwen_api_key
    audio_b64 = base64.b64encode(audio_data).decode("utf-8")
    data_uri = f"data:audio/wav;base64,{audio_b64}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": _engine_model("qwen"),
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
        resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"ASR 失败: {resp.status_code} {resp.text[:300]}")
        result = resp.json()
        # 从返回中提取文本
        try:
            text = result["output"]["choices"][0]["message"]["content"][0]["text"]
            return text.strip()
        except (KeyError, IndexError, TypeError):
            raise RuntimeError(f"千问 ASR 解析失败: {result}")


async def _transcribe_multipart_chunk(engine: str, audio_data: bytes, audio_format: str) -> str:
    """OpenAI 兼容 multipart 上传（智谱 GLM-ASR-2512 / 硅基流动 SenseVoiceSmall）"""
    meta = ASR_ENGINES[engine]
    base_url = (meta["base"] or "").strip().rstrip("/")
    endpoint = base_url + meta["endpoint"]
    api_key = _engine_conf(engine).get("api_key") or ""
    model = _engine_model(engine)

    # 智谱支持 hotwords（热词表），从配置读取（可后续设置页扩展）
    hotwords = None
    if engine == "zhipu":
        hw = _engine_conf("zhipu").get("hotwords")
        if isinstance(hw, list):
            hotwords = hw

    fmt = "wav" if audio_format in ("wav",) else audio_format or "wav"
    files = {
        "file": (f"audio.{fmt}", audio_data, f"audio/{fmt}"),
        "model": (None, model),
    }
    if hotwords:
        files["hotwords"] = (None, json.dumps(hotwords, ensure_ascii=False))

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(endpoint, files=files,
                                 headers={"Authorization": f"Bearer {api_key}"})
        if resp.status_code != 200:
            raise RuntimeError(f"{meta['name']} ASR 失败: {resp.status_code} {resp.text[:300]}")
        result = resp.json()
        text = (result.get("text") or "").strip()
        if not text:
            raise RuntimeError(f"{meta['name']} ASR 空结果: {result}")
        return text


# ---------- 腾讯云：TC3-HMAC-SHA256 签名 + 实时语音识别极速版 ----------

def _tc3_sign(secret_id: str, secret_key: str, service: str, action: str, payload: dict) -> dict:
    """腾讯云 API 3.0 TC3-HMAC-SHA256 签名，返回完整请求头（POST application/json）"""
    host = f"{service}.tencentcloudapi.com"
    ct = "application/json; charset=utf-8"
    timestamp = int(time.time())
    date = datetime.datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hashed_payload = hashlib.sha256(body).hexdigest()

    canonical_headers = f"content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = "\n".join([
        "POST", "/", "",
        canonical_headers, signed_headers, hashed_payload,
    ])
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256", str(timestamp), credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    secret_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac(secret_date, service)
    secret_signing = _hmac(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return {
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "Content-Type": ct,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Version": "2019-06-14",
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Region": "ap-guangzhou",
    }


async def _transcribe_tencent_chunk(audio_data: bytes, audio_format: str) -> str:
    """腾讯云实时语音识别·极速版（SentenceRecognition，≤60 秒，同步返回）"""
    conf = _engine_conf("tencent")
    secret_id = (conf.get("secret_id") or "").strip()
    secret_key = (conf.get("secret_key") or "").strip()
    if not (secret_id and secret_key):
        raise RuntimeError("腾讯云未配置 SecretId/SecretKey")

    voice_format = "wav" if audio_format == "wav" else "mp3"
    payload = {
        "ProjectId": 0,
        "SubServiceType": 2,           # 极速版
        "EngSerViceType": _engine_model("tencent"),   # 16k_zh 普通话（默认）
        "SourceType": 1,               # 1 = 本地文件 Data 上送
        "VoiceFormat": voice_format,
        "UsrAudioKey": f"kaoyan_{int(time.time() * 1000)}",
        "Data": base64.b64encode(audio_data).decode("utf-8"),
        "DataLen": len(audio_data),
    }
    headers = _tc3_sign(secret_id, secret_key, "asr", "SentenceRecognition", payload)

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(ASR_ENGINES["tencent"]["base"],
                                 json=payload, headers=headers)
        body = resp.json() if resp.content else {}
        # 成功：Response.Result 为文本
        text = ""
        r = body.get("Response", body)
        if isinstance(r, dict):
            res = r.get("Result")
            if isinstance(res, dict):
                text = (res.get("ResultText") or res.get("Text") or "").strip()
            elif isinstance(res, str):
                text = res.strip()
            if not text:
                text = (r.get("ResultText") or r.get("Text") or "").strip()
        if not text:
            err = r.get("Error") or body.get("Error") or {}
            raise RuntimeError(f"腾讯云 ASR 失败: {resp.status_code} {json.dumps(err, ensure_ascii=False)[:300]}")
        return text


async def _transcribe_single(engine: str, audio_data: bytes, audio_format: str) -> str:
    """按引擎模式识别一段音频，返回文本"""
    mode = ASR_ENGINES[engine]["mode"]
    if mode == "qwen":
        return await _transcribe_qwen_chunk(audio_data) or ""
    if mode == "multipart":
        return await _transcribe_multipart_chunk(engine, audio_data, audio_format)
    if mode == "tencent":
        return await _transcribe_tencent_chunk(audio_data, audio_format)
    raise RuntimeError(f"未知引擎模式: {mode}")


def _get_local_model(size: str):
    """懒加载并缓存 faster-whisper 模型（避免每个视频重复加载模型）"""
    if size not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        try:
            # 优先 GPU（RTX 4070）：显式 CUDA + float16 最快最省显存
            _MODEL_CACHE[size] = WhisperModel(size, device="cuda", compute_type="float16")
        except Exception:
            # GPU 不可用自动回退 CPU int8（保持功能可用）
            _MODEL_CACHE[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL_CACHE[size]


def _run_local(audio_path: str, size: str) -> dict:
    """同步执行本地识别（在后台线程里跑，不阻塞事件循环）"""
    model = _get_local_model(size)
    # 参数取舍（实测 4 分钟讲座，large-v3-turbo / beam_size=5）：
    #   vad_filter=True（旧写法）→ faster-whisper 1.2.x 会把整讲人声拼成一条，
    #     模型退化成 ~30 秒巨型段（13 段 / 平均 19 秒），字幕没法看；
    #   vad_filter=False → 模型按句给时间戳（约 2 秒/段）。
    #   condition_on_previous_text=False 防复读式幻觉；
    #   hallucination_silence_threshold 需要 word_timestamps=True，
    #     用来丢掉静音段里的幻觉（如片尾凭空多出的"谢谢大家"）。
    segments, info = model.transcribe(
        audio_path, language="zh",
        vad_filter=False, beam_size=5,
        condition_on_previous_text=False,
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
    )
    duration = float(getattr(info, "duration", 0) or 0) or audio_duration(audio_path)
    chunks = []
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        start = max(0.0, float(seg.start))
        # 夹到音频时长内：whisper 最后一窗会补零，时间戳可能越过音频结尾
        end = min(float(seg.end), duration) if duration else float(seg.end)
        if end - start < 0.2:      # 越界/零长段直接丢
            continue
        chunks.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text,
        })
    if not chunks:
        raise RuntimeError("本地识别无结果")
    return {
        "chunks": chunks,
        "duration": duration,
        # 本地是逐句精确时间轴，videos.py 的精确分支不用静音表；
        # 不再调 ffmpeg detect_silences（省一次解析，也避开 Windows 解码坑）
        "silences": [],
        "precise": True,   # 标记：逐句精确时间轴，videos.py 据此走精确分支
        "engine": "local",
    }


async def _transcribe_local(audio_path: str, size: str) -> dict:
    """本地 faster-whisper 识别（放到后台线程，避免阻塞事件循环）"""
    return await asyncio.to_thread(_run_local, audio_path, size)


async def _transcribe_cloud(engine: str, audio_data: bytes, audio_format: str) -> dict:
    """某个云引擎的整段转写：按该引擎的分段时长切分 + 静音检测。
    返回: {"chunks": [{"start": float, "text": str}, ...], "duration": float,
           "silences": [...], "engine": str}
    """
    meta = ASR_ENGINES[engine]
    tmpdir = tempfile.mkdtemp()
    audio_path = os.path.join(tmpdir, f"input.{audio_format}")
    with open(audio_path, "wb") as f:
        f.write(audio_data)

    try:
        dur = audio_duration(audio_path)
        silences = detect_silences(audio_path)

        segments = _convert_to_pcm_segments(audio_path, tmpdir, chunk_sec=meta["chunk_sec"])
        os.remove(audio_path)

        chunks = []
        for wav_path, chunk_idx in segments:
            with open(wav_path, "rb") as f:
                wav_data = f.read()
            text = await _transcribe_single(engine, wav_data, "wav")
            os.remove(wav_path)
            if text:
                chunks.append({
                    "start": chunk_idx * meta["chunk_sec"],
                    "text": text.strip(),
                })

        try:
            os.rmdir(tmpdir)
        except Exception:
            pass

        if not chunks:
            raise RuntimeError("所有段落识别失败")

        return {"chunks": chunks, "duration": dur, "silences": silences, "engine": engine}

    except Exception as e:
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except Exception:
            pass
        raise


def resolve_voice_engine() -> str:
    """语音输入（🎤）的独立引擎选择（与视频字幕 asr.provider 解耦）。

    asr.voice_provider 取值：
      - "auto"（默认）：自动降级链 智谱 → 千问 → 腾讯 → 硅基流动（按已配 key），
        全部未配置时本地 Whisper
      - "local"：强制本地 Whisper（免费离线）
      - "qwen" / "zhipu" / "tencent" / "siliconflow"：强制指定云引擎
    """
    cfg = _asr_cfg()
    vp = (cfg.get("voice_provider") or "auto").strip().lower()
    if vp in ALLOWED_PROVIDERS and vp != "auto":
        return vp
    chain = _cloud_chain()
    return chain[0] if chain else "local"


def _resolve_chain(provider: str, audio_path_available: bool) -> List[str]:
    """把 provider 展开成实际尝试的引擎顺序

    显式选 "local" = 只用本地（2026-09-10 改）：旧行为是 ["local"] + 云链，
    本地一失败就静默降级到智谱/千问，产出的是"字数估时"的假时间轴，
    而界面上仍显示"本地 Whisper"，用户完全无从察觉（已踩过一次：51 讲字幕全部如此）。
    想保留云兜底请用 "auto"。
    """
    if provider == "local":
        return ["local"]
    if provider in ASR_ENGINES:
        return [provider]
    # auto：云链 + 本地兜底
    chain = _cloud_chain()
    if audio_path_available:
        chain.append("local")
    return chain


async def transcribe_audio(
    audio_data: bytes,
    audio_format: str = "mp3",
    audio_path: Optional[str] = None,
    provider: Optional[str] = None,
) -> dict:
    """按引擎顺序识别音频；链上引擎依次尝试，全部失败才抛错。

    provider=None：用全局设置（asr.provider，视频字幕路径，默认 auto 降级链）；
    provider="local"/"qwen"/"zhipu"/"tencent"/"siliconflow"：显式指定
    （语音输入用 resolve_voice_engine() 的结果，可能已是具体引擎）。

    返回: {"chunks":[...], "duration":..., "silences":[...], "engine": str}
      local 的 chunks 每项含 start/end（精确时间轴，precise=True）；
      云引擎的 chunks 是按各自分段时长的段起点。
    """
    g_provider, size = _asr_provider()
    if provider:
        provider = provider.strip().lower()
        if provider not in ALLOWED_PROVIDERS:
            provider = g_provider
    else:
        provider = g_provider

    path_ok = bool(audio_path and os.path.exists(audio_path))
    if provider == "local" and not path_ok:
        raise RuntimeError("本地 Whisper 需要音频文件路径，但文件不存在")
    chain = _resolve_chain(provider, path_ok)
    if not chain:
        raise RuntimeError("没有可用的语音识别引擎（请在设置页配置任一云引擎 Key 或本机模型）")

    last_err = None
    for engine in chain:
        try:
            if engine == "local":
                result = await _transcribe_local(audio_path, size)
            else:
                result = await _transcribe_cloud(engine, audio_data, audio_format)
            # 明确记录实际生效的引擎（此前静默降级时，界面上完全看不出用了哪个）
            print(f"[asr] 引擎 {engine} 识别完成：{len(result.get('chunks') or [])} 段")
            return result
        except Exception as e:
            last_err = e
            print(f"[asr] 引擎 {engine} 识别失败，切换下一个：{e}")
            continue

    raise RuntimeError(f"所有识别引擎均失败：{last_err}" if last_err
                       else "没有可用的语音识别引擎")