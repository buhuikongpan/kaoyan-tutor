"""LLM 服务（本地直连模式）
- 聊天推理：DeepSeek V4 Flash（thinking 模式，SSE 流式）
- 视觉分析：智谱 GLM-4V（多模态模型）
"""
import json
import httpx
from typing import AsyncIterator, Optional, Tuple

from ..core.config import settings

DEEPSEEK_API = "https://api.deepseek.com/v1/chat/completions"
ZHIPU_API = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

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


def _completion_payload(messages: list, system_prompt: str, stream: bool) -> dict:
    full_messages = [{"role": "system", "content": system_prompt}] + messages
    return {
        "model": "deepseek-v4-flash",
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
    """流式调用 DeepSeek V4 Flash（thinking 模式），逐段产出：

        ("reasoning", "…思考片段…")  —— 思维链，不展示，由调用方累积存入历史
        ("content", "…正文片段…")    —— 最终回答，逐字展示给用户

    调用方负责累积两类文本；HTTP/API 错误会以 RuntimeError 抛出。
    """
    system_prompt = _build_system_prompt(subject, mode, subtitle_context)
    payload = _completion_payload(messages, system_prompt, stream=True)

    headers = {
        "Authorization": f"Bearer {settings.deepseek_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=STREAM_TIMEOUT) as client:
        async with client.stream("POST", DEEPSEEK_API, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(f"DeepSeek API {resp.status_code}: {body[:800]}")
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


async def vision_analyze(
    image_base64: str,
    question: str = "请详细描述这张图片中的内容",
) -> Optional[str]:
    """视觉分析：用智谱 GLM-4V 识别图片内容，返回文字描述"""
    headers = {
        "Authorization": f"Bearer {settings.zhipu_api_key}",
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
        "model": "glm-4v-flash",  # 免费版视觉模型，也可用 glm-4.6v-flash
        "messages": [{"role": "user", "content": content_parts}],
        "temperature": 0.5,
        "max_tokens": 512,
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(ZHIPU_API, json=payload, headers=headers)
            resp.raise_for_status()
            result = resp.json()
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return f"[图片分析失败: {str(e)}]"