# ASR 语音识别 —— 本地 Whisper 方案（存档，暂未启用）

> 状态：**仅存档，尚未改代码**。当前线上仍走「千问 ASR 云 API」。
> 用途：当云 API 额度用完 / 想完全免费离线 / 想摆脱对在线识别依赖时，启用本方案。

---

## 一、为什么用本地 Whisper

- **0 成本**：完全免费、无额度、无 quota、无按次计费
- **离线**：不依赖网络，不依赖任何厂商接口
- **隐私**：视频音频不出本机
- **你机器的优势**：有 **NVIDIA RTX 4070 8GB** 显卡，本地识别速度可观

> ⚠️ 常见误解：**ffmpeg 不做语音识别**，它只负责音/视频转换与切分。本地识别必须用 **Whisper**（OpenAI 开源模型）这类引擎；ffmpeg 在本项目里只用来「提取音频 + 切 50 秒分段」的预处理。

---

## 二、推荐技术选型：faster-whisper

| 库 | 说明 | 推荐度 |
|---|---|---|
| **faster-whisper** | CTranslate2 加速，CPU/GPU 都支持，速度快、内存低 | ⭐ 推荐 |
| openai-whisper | 官方版，纯 PyTorch，较慢 | 备选 |
| whisper.cpp | C++ 版，命令行，部署较绕 | 备选 |

用 **faster-whisper**，`pip install faster-whisper` 即可。

### 模型大小选择（中文普通话）

| 模型 | 大小 | 内存 | 速度 | 中文准确度 |
|---|---|---|---|---|
| base | ~140MB | 低 | 最快 | 一般，专业词/口音易错 |
| **small** | ~460MB | 中 | 快 | 基本够用（本方案默认选这个）|
| medium | ~1.5GB | 高 | 慢 | 更准（长视频明显更耗时）|

> 考研数学视频术语多、每段最长 80 分钟，若发现 small 错漏多，可升级 medium。

---

## 三、安装步骤（将来启用时执行）

```bash
# 用跑服务的那个 Python（Python311，不是系统 python）
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe -m pip install faster-whisper
```

首次运行会自动下载所选模型到 `C:\Users\Administrator\.cache\huggingface\`。

### GPU 加速说明
- faster-whisper 默认优先用 GPU（CTranslate2）。
- 若 GPU 报 cuDNN 缺失，可先退 CPU 用（慢一些但能用）；或在 CUDA 环境就绪后开 GPU。
- 已有 4070 8GB，small/base 均能跑。

---

## 四、需要的代码改动（本次未动，仅记录待办）

文件：`backend/app/services/asr_service.py`

1. 新增 `transcribe_audio_local(audio_data, ...)`，内部用 faster-whisper：
   ```python
   from faster_whisper import WhisperModel
   model = WhisperModel("small", device="auto", compute_type="auto")
   segments, info = model.transcribe(wav_path, language="zh", vad_filter=True)
   # 每个 segment 有 start/end/text，可直接产出时间轴，比云 50s 分段更精细
   ```
2. 返回结构与现有 `{"chunks":[...], "duration":..., "silences":...}` 兼容：
   云版给的是「每 50s 一段、start=段起始」；本地版可给出带精确 start/end 的段， voed 更合用。
3. 在 `.env` 设 `ASR_PROVIDER=local`，并在入口按 provider 分发（当前 config.asr_provider 已有此字段但未接线）。
4. 已存在的 `ffmpeg 提取音频 + detect_silences` 逻辑可复用。

> 备注：favicon/`/subtitles` 等无关。改完看 `backend/app/routers/videos.py` 的 `extract_subtitle()` 怎么调 `transcribe_audio`，保证本地模式的返回字段对齐。

---

## 五、备份/回滚

- 改动前备份 `asr_service.py` 与 `.env`。
- 想回到云 API：把 `ASR_PROVIDER` 改回 `qwen` 即可（云 provider 代码保留）。

---

## 六、附：2026-08-18 事故记录（为什么想到本方案）

现象：第9讲 10 个视频字幕识别全部 failed。
排查结果：**不是额度、不是 Key**，而是本机开了 Steam++/Watt Toolkit 加速器，其 **SSL 中间人截获**导致 Python 对 Dashscope/DeepSeek/智谱全线 `CERTIFICATE_VERIFY_FAILED`，调用在联网前即失败。
处理：关闭加速器（或其系统代理/全局/TUN 档）后 SSL 恢复正常。
结论：
- 若长期依赖云 API，用加速器时**别开系统代理/全局拦截档**；
- 若想彻底摆脱这类「环境依赖云 API」的不稳定，可启用本本地 Whisper 方案。
