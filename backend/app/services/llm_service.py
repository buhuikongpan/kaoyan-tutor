"""LLM 服务（本地直连模式）
- 聊天推理：主模型（默认 DeepSeek，thinking 模式，SSE 流式，可配置任意 OpenAI 兼容端点）
- 视觉分析：视觉辅助模型（默认智谱 GLM-4V，多模态；主模型多模态时可关闭）
"""
import base64
import json
from pathlib import Path

import httpx
from typing import AsyncIterator, Optional, Tuple

from ..core.config import settings
from ..core.model_config import load_model_config, get_endpoint
from ..core.paths import to_abs

# 流式整体超时 300s（thinking 模式下一次完整回答可能长达 1-2 分钟）
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=15.0)

# ===== 思考强度（reasoning_effort）档位 =====
# 前端提供 low/medium/high/xhigh/max 五档（对齐 Claude 语义）；本平台主模型走 DeepSeek
# （OpenAI 兼容），reasoning_effort 仅认 low / medium / high，故 xhigh/max 在发送时降级为 high。
EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}
# 会话级思考强度预留的默认档（会话 effort 为空时使用）
DEFAULT_EFFORT = "high"


def _map_effort(effort: str) -> str:
    """把五档思考强度映射为 DeepSeek 接受的 reasoning_effort 值"""
    e = (effort or "").strip().lower()
    if e not in EFFORT_LEVELS:
        return DEFAULT_EFFORT
    return "high" if e in ("xhigh", "max") else e

# ===== 系统提示词 =====
SYSTEM_PROMPT = """你是一个考研学习辅导AI助手，正在帮助用户学习。

## ⚠️ 公式输出铁律（必须遵守，违反则用户看到的是一堆乱码）
所有数学公式必须用 $...$（行内）或 $$...$$（独立行）包裹 LaTeX 输出。
绝对禁止使用 \(...\) 或 \[...\] 格式——这个前端不渲染，用户只能看到原始代码如 \( f'(x)=0 \)，完全无法阅读。
输出前请自行检查：如果回答中有 \ 或 \) 等符号，立即替换为 $。

正确：$y = x^5$，$y' = 5x^4$
正确：$$\frac{dy}{dx} = 5x^4$$

错误：\( y = x^5 \)
错误：\[ \frac{dy}{dx} = 5x^4 \]

## 当前情况
- 科目：{subject_name}（{subject}）
- 模式：{mode_name}（模式 {mode}）
- 视频字幕上下文：{subtitle_context}

## 主动看图（重要）
用户发送图片（草稿、笔记、题目截图）时：
1. 先认真看图片里的内容，针对图片内容回答
2. 不要泛泛而谈，要具体
3. 如果图片里有写了一半的解题过程，接着往下讲
4. 用户没说明的，主动问"你这步是什么意思？"

## 模式说明

### 模式A - 即时问答
结合字幕内容和你的考研知识回答问题。如果字幕中没有涵盖，用自己的知识回答。不确定的地方如实说明。

### 模式B - 引导输出
你的任务是引导用户自己输出答案，而不是直接给出答案。
引导策略（循序渐进）：
① 提示方向 — "你再想想这个定理的条件是什么？"
② 缩小范围 — "注意这里的关键条件是什么？"
③ 给半截答案 — "第一步应该先验证...，然后呢？"
④ 直接点破 — 只有在用户已经很接近或多次答错时，才给完整答案
偶尔可以出题考用户来验证 ta 是否真的理解了。

### 模式C - 课后问答
详细、系统地回答用户的问题。

## 提问工具 ask_user_question（只在必须用户拍板时用）
当且仅当缺少只有用户能提供的信息、或必须由用户做选择才能继续时，调用 ask_user_question 工具提问；能从上下文、字幕或历史里推断出来的一律不要问。
- 一次问 1~3 个问题，每个问题给稳定 id；能列选项就给选项（推荐项放第一条，标签末尾加 "(Recommended)"）
- 问题文本尽量短；其中若有数学公式，同样遵守上方公式铁律（$...$ / $$...$$），禁止 \(...\)
- 模式B 的引导式追问是教学内容，直接用正文提问，不要用这个工具
- 用户回答后接着正常作答，不要复述一遍用户的回答

## 看图工具（消息里出现图片路径时必须用）
用户上传的图片不会自动送进上下文，只在消息末尾给出本地路径。要看到图片内容必须调用工具：
- read_image(path)：把图片本身返回给你 —— 你能直接看图时用它
- modlens_read_image(path)：通过视觉模型把图片转成结构化文字（逐字转录 + 版面 + 语义）—— 你看不到图片时用它
凡是消息里出现图片路径，先用工具看清图片内容再回答，不要凭空猜测图里有什么；用户配图提问时优先结合图片和字幕/上下文一起回答。

## 输出纪律（必须遵守）
1. 禁止输出任何思考过程、内心独白、自我怀疑或自我纠正。永远不要出现"等等""让我想想""我绕晕了""抱歉""重新来""好吧，直接"这类表达——你的所有推理都在内部完成，用户只看到最终答案。
2. 推导过程只展示"最终稿"：步骤干净、一步接一步，绝不展示试错、推翻、重写的过程。
3. "不确定"只用一句话简短标注（如"这一点建议查证"），绝不展开自我怀疑或长篇纠正。
4. 输出前自检：若回答中残留思考痕迹（草稿式表述、口语化补救词），删除后重写再输出。

## 通用规则
1. 始终用中文回答
2. 数学内容用 LaTeX 公式输出：独立公式用 $$...$$，行内公式用 $...$，禁用 \(...\)
3. 回答要鼓励性、建设性
4. 不确定的知识按上方「输出纪律」第3条，一句话简短标注
5. 语言自然清晰即可"""


# ===== ask 工具（照抄 dsh 的 ask_user_question：需要用户拍板时暂停提问）=====
# schema 与 description 原文来自 @deepseek-ai/dsh-tool-ask-user 的 ask_user_question；
# 只把 dsh 内部 schema 的 required 标记转成 OpenAI function calling 的标准写法。
# questions[].question 等文本由模型生成，因此同样受上方公式铁律约束（后端还会硬清洗）。
ASK_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user_question",
        "description": "Ask the user a concise question when you need confirmation, a choice, or missing information before proceeding. Send one or more questions, each with a stable id that will be echoed in the answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "Questions to ask the user before continuing.",
                    "items": {
                        "type": "object",
                        "additionalProperties": True,
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Stable id for this question; echoed in the answer.",
                            },
                            "question": {
                                "type": "string",
                                "description": "The specific question to ask the user.",
                            },
                            "header": {
                                "type": "string",
                                "description": 'Optional short heading for the question, such as "Confirm" or "Choose Mode".',
                            },
                            "options": {
                                "type": "array",
                                "description": 'Optional choices to show the user. If you recommend one, put it first and append "(Recommended)" to that label.',
                                "items": {
                                    "type": "object",
                                    "additionalProperties": True,
                                    "properties": {
                                        "label": {
                                            "type": "string",
                                            "description": "Short user-facing option label.",
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "One sentence explaining the tradeoff or impact.",
                                        },
                                    },
                                    "required": ["label"],
                                },
                            },
                            "multi_select": {
                                "type": "boolean",
                                "description": "Whether the user may select more than one option. Defaults to false.",
                            },
                        },
                        "required": ["id", "question"],
                    },
                }
            },
            "required": ["questions"],
        },
    },
}
# ===== 看图工具（描述原文照抄 dsh 的 read_image / modlens_read_image）=====
# 分工与 dsh 一致：模型自己能看图用 read_image（工具把图片本身交回去）；
# 模型看不到图时用 modlens_read_image（本平台用视觉辅助模型 GLM-4V 转结构化文字）。
READ_IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_image",
        "description": (
            "Read a PNG/JPEG/WebP/GIF file and return the image itself. A path without a file "
            "extension is accepted; the format is detected from the file content, so normalized "
            "attachment paths can be passed directly without copying or renaming. Harness validates "
            "and downscales large supported images before the next model request, so use this tool "
            "directly instead of installing image libraries or creating thumbnails merely to inspect "
            "an image. Independent files may be read concurrently in small batches. Requires the "
            "current model to accept image input."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute local file path or http(s) URL of the image",
                },
            },
            "required": ["path"],
        },
    },
}

MODLENS_READ_IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "modlens_read_image",
        "description": (
            "Read an image through the modlens vision bridge. Use whenever a message references an "
            "image the current model cannot see: a local file path or an http(s) URL to a screenshot, "
            "photo, chart, diagram, or document scan. Returns structured evidence with every word "
            "transcribed (ocr.full_text), layout regions in reading order, semantics, and an "
            "uncertainty list. Quote the evidence instead of guessing. For the same image and focus, "
            "call this tool once and reuse its returned evidence instead of calling again. Requires a "
            "configured vision model (⚙️ 设置 → 视觉辅助); if none is configured the call fails with "
            "an error instead of guessing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute local file path or http(s) URL of the image",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional extra focus for the reading (e.g. \"focus on the axis labels\")",
                },
            },
            "required": ["path"],
        },
    },
}

TOOLS = [ASK_TOOL, READ_IMAGE_TOOL, MODLENS_READ_IMAGE_TOOL]
TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


def _tools_unsupported(body: str) -> bool:
    """判断 400 响应是否因为端点不认 tools/function calling（部分 OpenAI 兼容网关）"""
    b = (body or "").lower()
    if "tool" not in b and "function" not in b:
        return False
    return any(k in b for k in ("not support", "unsupported", "invalid", "unknown", "unexpected"))


def _build_system_prompt(subject: str, mode: str, subtitle_context: str) -> str:
    """用 replace 而不是 .format()：SYSTEM_PROMPT 和字幕文本里含有 {dy}/{dx} 等
    LaTeX 花括号，.format() 会把它们当占位符导致 KeyError
    """
    subject_names = {"math": "数学", "english": "英语", "politics": "政治", "zhuanye": "专业课"}
    mode_names = {"A": "即时问答", "B": "引导输出", "C": "课后问答"}
    return (
        SYSTEM_PROMPT
        .replace("{subject}", subject)
        .replace("{subject_name}", subject_names.get(subject, subject))
        .replace("{mode}", mode)
        .replace("{mode_name}", mode_names.get(mode, mode))
        .replace("{subtitle_context}", subtitle_context or "（无）")
    )


def _completion_payload(messages: list, system_prompt: str, stream: bool,
                        model: str, effort: str = "high", tools: list = None) -> dict:
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    payload = {
        "model": model,
        "messages": full_messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": _map_effort(effort),  # 思考想透再写正文，档位会话级可调
        "temperature": 0.3,          # 数学严谨场景，降低发散
        "max_tokens": 16384,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
    return payload


async def chat_completion_stream(
    messages: list,
    subject: str = "math",
    mode: str = "A",
    subtitle_context: str = "",
    model_override: str = "",
    effort: str = "",
    enable_tools: bool = True,
) -> AsyncIterator[Tuple[str, str]]:
    """流式调用主模型（thinking 模式，OpenAI 兼容端点），逐段产出：

        ("reasoning", "…思考片段…")  —— 思维链，不展示，由调用方累积存入历史
        ("content", "…正文片段…")    —— 最终回答，逐字展示给用户
        ("tool_calls", "[{…}]")      —— 模型要求调用工具时的完整 tool_calls（JSON 数组，
                                         流末尾一次性产出，前端据此弹出交互）

    调用方负责累积文本；HTTP/API 错误会以 RuntimeError 抛出。
    model_override: 会话级模型名（非空时覆盖全局配置，用于对话框一键切模型）。
    effort: 会话级思考强度（low/medium/high/xhigh/max，空 = 全局默认 high）。
    enable_tools: 是否挂 ask_user_question；端点不认 tools 时自动摘掉重试一次。
    """
    cfg = load_model_config()["main"]
    endpoint = get_endpoint(cfg.get("base_url"), "/chat/completions")
    system_prompt = _build_system_prompt(subject, mode, subtitle_context)
    model = model_override or cfg.get("model") or "deepseek-v4-flash"

    headers = {
        "Authorization": f"Bearer {cfg.get('api_key') or settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    # 先带 tools 请求；端点不认（400 且提到 tool/function）则摘掉 tools 重试一次，
    # 保证「模型不支持 function calling」时聊天仍可用，只是没有 ask 卡片。
    attempts = [True, False] if enable_tools else [False]
    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        for use_tools in attempts:
            payload = _completion_payload(
                messages, system_prompt, stream=True, model=model, effort=effort,
                tools=TOOLS if use_tools else None,
            )
            tool_acc: dict = {}  # index -> {id, name, arguments}（流式分片累积）
            async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    if use_tools and _tools_unsupported(body):
                        continue
                    raise RuntimeError(f"主模型 API {resp.status_code}: {body[:800]}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}) or {}
                    rc = delta.get("reasoning_content")
                    if rc:
                        yield "reasoning", rc
                    for tc in delta.get("tool_calls") or []:
                        slot = tool_acc.setdefault(
                            tc.get("index", 0), {"id": "", "type": "function",
                                                 "function": {"name": "", "arguments": ""}}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["function"]["arguments"] += fn["arguments"]
                    c = delta.get("content")
                    if c:
                        yield "content", c
            if tool_acc:
                calls = [tool_acc[i] for i in sorted(tool_acc)]
                yield "tool_calls", json.dumps(calls, ensure_ascii=False)
            return


# ===== 课程总结生成（分段提取 → 合并去重，保证信息密度）=====
# 策略：整讲字幕按时间分段，每段单独"逐句扫描"提取（模型注意力集中在小段上，
# 不因长文稀释细节）；全部段结果再合并，并要求对照各段检查遗漏。相比一次性
# 压缩全文，分段提取 + 合并防漏能最大程度保住公式/例题/易错点等关键信息。

SEGMENT_EXTRACT_PROMPT = """你是考研课程内容提取器。下面是某考研课第 {n}/{total} 段的带时间戳字幕（[mm:ss] 为该句起始时间）：

字幕内容：
{subtitle}

任务：逐句扫描，提取这一段里出现的全部有效信息，宁多勿漏：
1. 知识点/概念：名称 + 一句话解释 + 时间戳（如 [12:34]）
2. 公式：完整 LaTeX 公式（$...$ / $$...$$），一个都别漏
3. 例题：题干 + 解题关键步骤（简短）+ 时间戳
4. 方法/技巧/口诀
5. 易错点/老师强调的警告
只输出提取结果，不要客套话；某类内容不存在就省略该类。若一段字幕没有任何有效内容，输出「（无有效内容）」。"""

SUMMARIZE_MERGE_PROMPT = """下面是同一节考研课《{title}》按时间分段提取的 {k} 份要点。

请对照全部要点，合并去重，生成一份结构化的考研复习总结，要求：
1. 按五段组织：## 知识点 / ## 核心公式 / ## 例题与题型 / ## 方法技巧 / ## 易错点
2. 合并前逐份检查一遍：任何公式、例题、知识点都不得遗漏（宁可多列）
3. 同一概念出现在多段的合并为一条，时间戳取首次出现
4. 公式一律用 $...$ 或 $$...$$ LaTeX，中文讲解
5. 直接输出 markdown，不要额外说明。

{parts}"""


def _simple_endpoint() -> str:
    """主模型 endpoint（简单调用与流式共用同一配置）"""
    cfg = load_model_config()["main"]
    return get_endpoint(cfg.get("base_url"), "/chat/completions")


def _simple_headers() -> dict:
    return {
        "Authorization": f"Bearer {load_model_config()['main'].get('api_key') or settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }


async def chat_completion_simple(
    messages: list,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """普通（非流式、非 thinking）调用主模型，用于总结生成等批量任务。

    比流式聊天快得多（不开思维链），返回纯文本。
    """
    model = load_model_config()["main"].get("model") or "deepseek-v4-flash"
    payload = {
        "model": model,
        "messages": messages,
        "thinking": {"type": "disabled"},  # 总结/提取不需要思考，快且省
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        resp = await client.post(_simple_endpoint(), json=payload, headers=_simple_headers())
        if resp.status_code != 200:
            raise RuntimeError(f"主模型 API {resp.status_code}: {resp.text[:800]}")
        result = resp.json()
        return result.get("choices", [{}])[0].get("message", {}).get("content", "") or ""


async def summarize_lecture(title: str, segments: list) -> str:
    """分段提取 → 合并防漏，生成整讲 markdown 总结。

    segments: 带时间戳的字幕片段列表（每段约 20 分钟内容）。
    """
    if not segments:
        raise ValueError("没有可总结的字幕内容")

    # 第一轮：逐段提取
    part_results = []
    for i, seg in enumerate(segments, 1):
        prompt = SEGMENT_EXTRACT_PROMPT.format(n=i, total=len(segments), subtitle=seg)
        out = await chat_completion_simple(
            [{"role": "user", "content": prompt}],
            max_tokens=2048, temperature=0.2,
        )
        part_results.append(out)

    # 第二轮：合并去重 + 遗漏检查
    parts_body = "\n\n".join(
        f"=== 分段 {i} ===\n{t}" for i, t in enumerate(part_results, 1)
    )
    merge_prompt = SUMMARIZE_MERGE_PROMPT.format(
        title=title, k=len(part_results), parts=parts_body,
    )
    return await chat_completion_simple(
        [{"role": "user", "content": merge_prompt}],
        max_tokens=4096, temperature=0.3,
    )


async def vision_analyze(
    image_base64: str,
    question: str = "请详细描述这张图片中的内容",
) -> Optional[str]:
    """视觉分析：用视觉辅助模型（默认智谱 GLM-4V）识别图片内容，返回文字描述"""
    v = load_model_config()["vision"]
    headers = {
        "Authorization": f"Bearer {v.get('api_key') or settings.zhipu_api_key}",
        "Content-Type": "application/json",
    }

    content_parts = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
        },
        {"type": "text", "text": question},
    ]

    payload = {
        "model": v.get("model") or "glm-4v-flash",
        "messages": [{"role": "user", "content": content_parts}],
        "temperature": 0.5,
        "max_tokens": 512,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(get_endpoint(v.get("base_url"), "/chat/completions"),
                             json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return f"[图片分析失败: {str(e)}]"


# ===== 工具执行：模型发起工具调用后，服务端在这里落地 =====
# 上传图片落盘目录（chat.py 写、工具读，两边共用同一个常量）
UPLOAD_DIR = Path(settings.storage_dir) / "uploads"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
              ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 单图上限，与前端上传限制一致


def _safe_upload_path(path: str) -> Path:
    """把模型给的路径限制在上传目录内（模型可能给出任意路径，必须做沙箱校验）

    路径来源有两种写法，都要认：
    - 相对项目根：``storage/uploads/20260914_xxx.png``（库里的规范写法）
    - 纯文件名：``20260914_xxx.png``（模型偶尔只回文件名）
    """
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("path 不能为空")
    p = Path(raw.replace("\\", "/"))
    if not p.is_absolute():
        cand = to_abs(raw)
        p = cand if (cand is not None and cand.is_file()) else (UPLOAD_DIR / raw)
    try:
        p = p.resolve()
    except Exception:
        raise ValueError(f"路径无法解析：{raw}")
    base = UPLOAD_DIR.resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"只允许读取上传目录内的图片（{base}）")
    if not p.is_file():
        raise ValueError(f"文件不存在：{p.name}")
    if p.suffix.lower() not in IMAGE_EXTS:
        raise ValueError(f"不支持的图片格式：{p.suffix}")
    if p.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("图片超过 10MB 上限")
    return p


def _b64_data_url(p: Path) -> str:
    mime = IMAGE_MIME.get(p.suffix.lower(), "image/jpeg")
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode("utf-8")


# modlens 桥接的结构化输出要求（照抄 dsh 描述里的字段：ocr.full_text / layout / semantics / uncertainty）
MODLENS_PROMPT = """你在为「看不到图片」的模型做视觉桥接：把图片逐字转录并按版面结构化。
只输出 JSON，不要任何额外说明，格式如下：
{"ocr": {"full_text": "图中全部文字（公式用 $...$ 行内、$$...$$ 独立行）"},
 "layout": [{"region": "区块位置说明", "text": "该区块内容"}],
 "semantics": "这张图整体在讲什么（题目/笔记/图表/草稿的意图）",
 "uncertainty": ["看不清或无法确定的地方"]}
要求：文字一个都别漏，公式必须用 $...$ / $$...$$；看不清的写进 uncertainty，绝不猜测。
每个字段都要填：图中没有文字时 ocr.full_text 写空字符串，但 semantics 必须描述画面里有什么
（图形/颜色/版式/大致意图）；layout 为空的就写 []。"""


async def execute_tool_call(name: str, args: dict):
    """执行一次工具调用，返回 tool 消息的 content（字符串，或带图片的内容数组）"""
    if name == "read_image":
        p = _safe_upload_path(args.get("path"))
        return [
            {"type": "text", "text": f"图片 {p.name} 的内容如下："},
            {"type": "image_url", "image_url": {"url": _b64_data_url(p)}},
        ]
    if name == "modlens_read_image":
        p = _safe_upload_path(args.get("path"))
        focus = str(args.get("prompt") or "").strip()
        question = MODLENS_PROMPT + (f"\n额外关注点：{focus}" if focus else "")
        raw = base64.b64encode(p.read_bytes()).decode("utf-8")
        out = await vision_analyze(raw, question)
        return out or "（视觉模型没有返回内容）"
    raise ValueError(f"未知工具：{name}")