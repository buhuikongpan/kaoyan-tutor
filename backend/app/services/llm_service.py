"""LLM 服务（本地直连模式）
- 聊天推理：主模型（默认 DeepSeek，thinking 模式，SSE 流式，可配置任意 OpenAI 兼容端点）
- 视觉分析：视觉辅助模型（默认智谱 GLM-4V，多模态；主模型多模态时可关闭）
"""
import json
import httpx
from typing import AsyncIterator, Optional, Tuple

from ..core.config import settings
from ..core.model_config import load_model_config, get_endpoint

# 流式整体超时 300s（thinking 模式下一次完整回答可能长达 1-2 分钟）
STREAM_TIMEOUT = httpx.Timeout(300.0, connect=15.0)

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


def _build_system_prompt(subject: str, mode: str, subtitle_context: str) -> str:
    """用 replace 而不是 .format()：SYSTEM_PROMPT 和字幕文本里含有 {dy}/{dx} 等
    LaTeX 花括号，.format() 会把它们当占位符导致 KeyError
    """
    subject_names = {"math": "数学", "english": "英语", "politics": "政治"}
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
                        model: str) -> dict:
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    return {
        "model": model,
        "messages": full_messages,
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",  # 思考想透再写正文，避免在回答里即兴发挥
        "temperature": 0.3,          # 数学严谨场景，降低发散
        "max_tokens": 16384,
        "stream": stream,
    }


async def chat_completion_stream(
    messages: list,
    subject: str = "math",
    mode: str = "A",
    subtitle_context: str = "",
) -> AsyncIterator[Tuple[str, str]]:
    """流式调用主模型（thinking 模式，OpenAI 兼容端点），逐段产出：

        ("reasoning", "…思考片段…")  —— 思维链，不展示，由调用方累积存入历史
        ("content", "…正文片段…")    —— 最终回答，逐字展示给用户

    调用方负责累积两类文本；HTTP/API 错误会以 RuntimeError 抛出。
    """
    cfg = load_model_config()["main"]
    endpoint = get_endpoint(cfg.get("base_url"), "/chat/completions")
    system_prompt = _build_system_prompt(subject, mode, subtitle_context)
    payload = _completion_payload(messages, system_prompt, stream=True,
                                  model=cfg.get("model") or "deepseek-v4-flash")

    headers = {
        "Authorization": f"Bearer {cfg.get('api_key') or settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        async with client.stream("POST", endpoint, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
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
                c = delta.get("content")
                if c:
                    yield "content", c


# ===== 课程总结生成（分段提取 → 合并去重，保证信息密度）=====
# 策略：整讲字幕按时间分段，每段单独"逐句扫描"提取（模型注意力集中在小段上，
# 不因长文稀释细节）；全部段结果再合并，并要求对照各段检查遗漏。相比一次性
# 压缩全文，分段提取 + 合并防漏能最大程度保住公式/例题/易错点等关键信息。

SEGMENT_EXTRACT_PROMPT = """你是考研课程内容提取器。下面是某考研课第 {n}/{total} 段的带时间戳字幕（[mm:ss] 为该句起始时间）。

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