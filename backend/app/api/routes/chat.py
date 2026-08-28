"""聊天接口 — 直连 DeepSeek/智谱（本地模式，不依赖 Dify），SSE 流式输出
会话已持久化到 SQLite（chat_sessions + chat_messages），服务重启不丢，
支持按科目/模式管理会话（新建 / 删除 / 重命名 / 历史）。
"""
import json
import base64
import os
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...services.llm_service import chat_completion_stream, vision_analyze
from ...core.model_config import load_model_config
from ...models import Video, Subtitle, ChatSession, ChatMessage
from ..deps import get_db

router = APIRouter(prefix="/api/chat", tags=["聊天"])


def normalize_math(text: str) -> str:
    """公式形态硬约束：把模型偶尔输出的 \(...\) / \[...\] 统一归一化为 $...$ / $$...$$。

    提示词里对模型是软约束；这里在落库前做硬清洗，保证库里和历史
    恢复的消息都是前端能渲染的形态。
    """
    text = re.sub(r'\\\[([\s\S]*?)\\\]', r'$$\1$$', text)
    text = re.sub(r'\\\(([\s\S]*?)\\\)', r'$\1$', text)
    return text

SUBJECT_NAMES = {"math": "数学", "english": "英语", "politics": "政治"}
MODE_NAMES = {"A": "即时问答", "B": "引导输出", "C": "课后问答"}
DEFAULT_NAME = "新对话"
MAX_HISTORY = 40           # 回填给模型的历史消息上限（条）
MAX_REASONING_ROUNDS = 2   # 思维链只回填最近 N 轮 assistant（防 token 膨胀）
# 模式B 字幕上限：模型上下文为 1M token，10 节长课全文（约 15 万字符 ≈ 20 万 token）
# 也只是总量的 2%，此上限纯属防御极端堆积（历史消息 + 思维链叠加），正常不触发
MAX_SUBTITLE_CHARS = 400000


# ---------- DB 读写辅助（统一走调用方传入的 Session） ----------
def _load_messages(db: Session, conv_id: str, limit: int = MAX_HISTORY) -> list:
    """按 conv_id 从 DB 载入历史消息（最近 limit 条）"""
    rows = db.query(ChatMessage).filter(ChatMessage.conv_id == conv_id)\
        .order_by(ChatMessage.seq.asc()).all()
    out = []
    reasoning_left = MAX_REASONING_ROUNDS  # 只给最近几轮 assistant 回填思维链
    for m in rows[-limit:]:
        try:
            content = json.loads(m.content) if m.content else None
        except Exception:
            content = m.content
        # 类型归一化后再回填：
        # 1) 数组 content（带图消息）拍平为纯文本 —— DeepSeek 聊天接口只接受字符串 content
        # 2) json.loads 会把纯数字字符串("2")解析成 int —— 同样会 400，强制转回字符串
        if isinstance(content, list):
            texts = [p.get("text", "") for p in content if isinstance(p, dict)]
            content = "\n".join(t for t in texts if t)
        elif not isinstance(content, str):
            content = str(content)
        item = {"role": m.role, "content": content}
        if m.reasoning and m.role == "assistant" and reasoning_left > 0:
            # 思维链只回填最近少数几轮：历史 + 思维链都占上下文，全量回填
            # 会在长对话中顶爆 token（这也是节省账单的关键）
            item["reasoning_content"] = m.reasoning
            reasoning_left -= 1
        out.append(item)
    return out


def _append_message(
    db: Session,
    conv_id: str, subject: str, mode: str, role: str,
    content, reasoning: str = "", quote: str = "",
) -> None:
    """写入一条消息，并更新会话 updated_at、首条提问自动命名"""
    import datetime
    seq = db.query(ChatMessage).filter(ChatMessage.conv_id == conv_id).count()
    jsonable = content if isinstance(content, (list, dict)) else (content or "")
    stored = json.dumps(jsonable, ensure_ascii=False) if isinstance(jsonable, (list, dict)) else str(jsonable)
    db.add(ChatMessage(conv_id=conv_id, subject=subject, mode=mode,
                       role=role, content=stored, reasoning=reasoning or "",
                       quote=quote or "", seq=seq))
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if sess:
        sess.updated_at = datetime.datetime.utcnow()
        if role == "user" and (not sess.name or sess.name == DEFAULT_NAME):
            text = content if isinstance(content, str) else ""
            sess.name = (text.strip()[:30] or DEFAULT_NAME)
    db.commit()


def _ensure_session(db: Session, conv_id: str, subject: str, mode: str) -> None:
    """确保会话行存在（老流程/直接发送时自动建）"""
    if not db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first():
        db.add(ChatSession(conv_id=conv_id, subject=subject, mode=mode, name=DEFAULT_NAME))
        db.commit()


def _truncate(text: str, limit: int = MAX_SUBTITLE_CHARS) -> str:
    """超长上下文裁剪：保留开头和结尾（正文精华通常在首尾），中间省略"""
    if len(text) <= limit:
        return text
    head = limit * 3 // 5
    tail = limit - head
    return text[:head] + "\n…（中间省略）…\n" + text[-tail:]


# ---------- 会话管理 API ----------
@router.get("/conversations")
def list_conversations(subject: str = Query("math"), db: Session = Depends(get_db)):
    """当前科目的所有会话，按 A/B/C 分组返回（一条 group-by 查计数，避免 N+1）"""
    rows = db.query(ChatSession).filter(ChatSession.subject == subject)\
        .order_by(ChatSession.updated_at.desc()).all()
    counts = dict(
        db.query(ChatMessage.conv_id, func.count(ChatMessage.id))
        .group_by(ChatMessage.conv_id).all()
    )
    groups = {"A": [], "B": [], "C": []}
    for s in rows:
        groups.setdefault(s.mode, []).append({
            "conversation_id": s.conv_id,
            "name": s.name or DEFAULT_NAME,
            "mode": s.mode,
            "subject": s.subject,
            "model": s.model or "",  # 会话级模型（空 = 全局）
            "message_count": counts.get(s.conv_id, 0),
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        })
    return groups


@router.post("/conversations")
def create_conversation(subject: str = Query("math"), mode: str = Query("A"),
                        db: Session = Depends(get_db)):
    """新建会话"""
    if mode not in MODE_NAMES:
        raise HTTPException(400, f"不支持的 Agent 模式: {mode}")
    if subject not in SUBJECT_NAMES:
        raise HTTPException(400, f"不支持的科目: {subject}")
    conv_id = f"conv_{uuid.uuid4().hex[:12]}"
    db.add(ChatSession(conv_id=conv_id, subject=subject, mode=mode, name=DEFAULT_NAME))
    db.commit()
    return {"conversation_id": conv_id, "name": DEFAULT_NAME, "mode": mode, "subject": subject}


@router.patch("/conversations/{conv_id}")
def rename_conversation(conv_id: str, name: str = Query(...), db: Session = Depends(get_db)):
    """重命名会话"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    sess.name = name[:255]
    db.commit()
    return {"conversation_id": conv_id, "name": sess.name}


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, db: Session = Depends(get_db)):
    """删除会话及其全部消息"""
    db.query(ChatMessage).filter(ChatMessage.conv_id == conv_id).delete()
    db.query(ChatSession).filter(ChatSession.conv_id == conv_id).delete()
    db.commit()
    return {"message": "已删除"}


@router.get("/{conv_id}/history")
def get_history(conv_id: str, db: Session = Depends(get_db)):
    """返回会话历史（用于前端重渲染）"""
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    msgs = db.query(ChatMessage).filter(ChatMessage.conv_id == conv_id)\
        .order_by(ChatMessage.seq.asc()).all()
    items = []
    for m in msgs:
        content = m.content
        try:
            parsed = json.loads(content) if content else ""
            content = parsed
        except Exception:
            pass
        base = {"role": m.role, "reasoning": m.reasoning or "", "quote": m.quote or ""}
        if isinstance(content, list):  # 含图片的 user 消息
            texts = [p.get("text", "") for p in content if isinstance(p, dict)]
            items.append({**base, "content": "\n".join(texts), "has_image": True})
        else:
            items.append({**base, "content": content or "", "has_image": False})
    return {
        "conversation_id": conv_id,
        "name": sess.name if sess else DEFAULT_NAME,
        "mode": sess.mode if sess else "A",
        "model": sess.model if sess and sess.model else "",
        "messages": items,
    }


@router.post("/conversations/{conv_id}/model")
def set_conversation_model(conv_id: str, model: str = Query(...),
                           db: Session = Depends(get_db)):
    """会话级模型切换：只改当前会话用的模型（不碰全局设置），发送即生效"""
    model = (model or "").strip()[:120]
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    sess.model = model
    db.commit()
    return {"conversation_id": conv_id, "model": sess.model}


# ---------- 聊天（SSE 流式） ----------
def _sse(payload: dict) -> str:
    """序列化一条 SSE 事件：data: {json}\n\n"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/send-stream")
async def send_message_stream(
    query: str = Form(...),
    subject: str = Form("math"),
    mode: str = Form("A"),
    subtitle_context: str = Form(""),
    conversation_id: str = Form(""),
    quote: str = Form(""),  # 引用内容（JSON 字符串 {"text": "..."}，用户引用 AI 回答某段特别说明）
    image: UploadFile = File(None),
    watched_video_ids: str = Form(""),  # 模式B：勾选的视频ID（JSON 数组字符串）
    db: Session = Depends(get_db),
):
    """发送消息（可选图片），SSE 流式返回 AI 回答。

    事件序列：
      data: {"type":"reasoning","text":"思维链片段"} （可多次，正文前或与正文交替）
      data: {"type":"delta","text":"正文片段"}       （可多次）
      data: {"type":"done","conversation_id":...}    （正常结束）
      data: {"type":"error","detail":"..."}          （出错时替代 done）
    """
    if subject not in SUBJECT_NAMES:
        raise HTTPException(400, f"不支持的科目: {subject}")
    if mode not in MODE_NAMES:
        raise HTTPException(400, f"不支持的 Agent 模式: {mode}")

    # 引用解析：前端传 {"text": "原文"}，取 text 截断（防上下文膨胀）
    quote_text = ""
    if quote and quote.strip():
        try:
            qobj = json.loads(quote)
            quote_text = str(qobj.get("text") or "").strip()[:500]
        except Exception:
            quote_text = quote.strip()[:500]
    quote_prefix = f"[用户引用了你的回答：「{quote_text}」]\n" if quote_text else ""

    # 图片 → base64（沿用旧 send-with-image 的处理）
    image_base64 = ""
    if image:
        data = await image.read()
        image_base64 = base64.b64encode(data).decode("utf-8")
        ext = (image.filename or "").split(".")[-1].lower() or "jpeg"
        image_base64 = f"data:image/{ext};base64,{image_base64}"

    wids = []
    if watched_video_ids and watched_video_ids.strip():
        try:
            wids = [int(x) for x in watched_video_ids.strip().strip("[]").split(",") if x.strip()]
        except ValueError:
            wids = []

    conv_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    _ensure_session(db, conv_id, subject, mode)

    # 会话级模型：会话行有 model 则用它，否则用全局主模型（llm_service 内部兜底）
    sess_row = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    conv_model = sess_row.model.strip() if sess_row and sess_row.model else ""

    # 从 DB 载入历史（含 reasoning 回填限制），保证重启后上下文不丢
    history = _load_messages(db, conv_id)

    # ---- 构建用户消息（文本 + 可选图片 + 引用） ----
    user_content: str
    multimodal_content = None  # 主模型多模态时直接传图片数组
    if image_base64:
        cfg = load_model_config()
        if cfg["main"].get("multimodal"):
            # 主模型本身多模态：图片按 OpenAI 格式直接发给主模型
            multimodal_content = [
                {"type": "text", "text": quote_prefix + query},
                {"type": "image_url", "image_url": {"url": image_base64}},
            ]
            user_content = quote_prefix + query + "\n[用户上传了图片]"
        elif cfg["vision"].get("enabled"):
            # 视觉辅助模型（默认智谱）：先分析成文字描述再给主模型
            try:
                raw = image_base64.split(",", 1)[1]
                q = f"这是一张考研学习相关的图片，用户的问题是：{query}。请详细描述图片内容，特别是任何文字、公式、图表。"
                vision_result = await vision_analyze(raw, q)
                if vision_result and not vision_result.startswith("[图片已收到"):
                    image_description = f"\n[用户上传了图片，AI视觉分析结果：{vision_result}]"
                else:
                    image_description = "\n[用户上传了图片]"
            except Exception as e:
                image_description = f"\n[用户上传了图片，分析异常: {str(e)}]"
            user_content = quote_prefix + query + image_description
        else:
            user_content = quote_prefix + query + "\n[用户上传了图片，但主模型不支持图片且视觉辅助已关闭]"
    else:
        user_content = quote_prefix + query

    _append_message(db, conv_id, subject, mode, "user", multimodal_content or user_content, quote=quote_text)
    history.append({"role": "user", "content": multimodal_content or user_content})

    # 模式B：勾选视频的上下文——优先用「课程总结」（高密度、跨多节不爆上下文），
    # 没有总结的视频回退字幕全文；总量仍由 _truncate 兜底（1M 上下文下基本不触发）
    if mode == 'B' and wids:
        videos = db.query(Video).filter(
            Video.id.in_(wids), Video.subject == subject).all()
        if videos:
            parts = []
            for v in videos:
                header = f'\n--- 视频 {v.id}: {v.title or v.filename} ---'
                summary = ""
                if v.summary_status == "done" and v.summary_path and os.path.exists(v.summary_path):
                    try:
                        summary = Path(v.summary_path).read_text(encoding="utf-8")
                    except Exception:
                        summary = ""
                if summary.strip():
                    parts.append(f"{header}（课程总结）\n{summary.strip()}")
                else:
                    subs = db.query(Subtitle).filter(Subtitle.video_id == v.id)\
                        .order_by(Subtitle.seq).all()
                    if subs:
                        parts.append(f"{header}（无总结，附字幕全文）\n"
                                     + "\n".join(s.text for s in subs if s.text))
            if parts:
                subtitle_context = _truncate('\n'.join(parts))

    async def event_gen():
        reasoning_acc = []
        content_acc = []
        try:
            async for kind, text in chat_completion_stream(
                messages=history,
                subject=subject,
                mode=mode,
                subtitle_context=subtitle_context,
                model_override=conv_model,
            ):
                if kind == "reasoning":
                    reasoning_acc.append(text)
                    # 思维链实时推给前端做"深度思考中"展示（独立于正文，不混入回答）
                    yield _sse({"type": "reasoning", "text": text})
                else:
                    content_acc.append(text)
                    yield _sse({"type": "delta", "text": text})

            answer = "".join(content_acc)
            reasoning = "".join(reasoning_acc)
            if not answer:
                yield _sse({"type": "error", "detail": "AI 返回为空"})
                return
            # 公式形态硬约束：落库前统一为 $ 格式（前端渲染的形态）
            answer = normalize_math(answer)
            # 只有在完整拿到回答后才写入历史（中途断流不落库，前端可重发）
            _append_message(db, conv_id, subject, mode, "assistant", answer, reasoning=reasoning)
            yield _sse({"type": "done", "conversation_id": conv_id})
        except HTTPException as e:
            yield _sse({"type": "error", "detail": e.detail})
        except Exception as e:
            import httpx
            detail = f"AI 调用失败: {str(e)}"
            resp_body = getattr(e, "response", None) or getattr(e, "message", None)
            if isinstance(e, httpx.HTTPStatusError):
                resp_body = e.response
            if resp_body is not None and hasattr(resp_body, "text"):
                try:
                    body = resp_body.text[:800]
                    if body:
                        detail += f" | 接口原始响应: {body}"
                except Exception:
                    pass
            yield _sse({"type": "error", "detail": detail})

    return StreamingResponse(event_gen(), media_type="text/event-stream")