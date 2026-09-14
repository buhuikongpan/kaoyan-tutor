"""聊天接口 — 直连 DeepSeek/智谱（本地直连模式），SSE 流式输出
会话已持久化到 SQLite（chat_sessions + chat_messages），服务重启不丢，
支持按科目/模式管理会话（新建 / 删除 / 重命名 / 历史）。
"""
import json
import base64
import datetime
import os
import re
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Query, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ...services.llm_service import (
    chat_completion_stream, execute_tool_call, UPLOAD_DIR,
)
from ...core.paths import resolve, to_rel
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

SUBJECT_NAMES = {"math": "数学", "english": "英语", "politics": "政治", "zhuanye": "专业课"}
MODE_NAMES = {"A": "即时问答", "B": "引导输出", "C": "课后问答"}
DEFAULT_NAME = "新对话"
# ask 工具（照抄 dsh 的 ask_user_question）：模型可暂停并向前端提问
ASK_TOOL_NAME = "ask_user_question"
MAX_ASK_QUESTIONS = 3    # 每轮最多回传 3 个问题（工具 schema 无硬上限，服务端防御）
MAX_TOOL_ROUNDS = 3      # 一次请求内最多几轮「工具调用 → 拿结果 → 继续作答」
MAX_HISTORY = 40           # 回填给模型的历史消息上限（条）
MAX_REASONING_ROUNDS = 2   # 思维链只回填最近 N 轮 assistant（防 token 膨胀）
# 模式B 字幕上限：模型上下文为 1M token，10 节长课全文（约 15 万字符 ≈ 20 万 token）
# 也只是总量的 2%，此上限纯属防御极端堆积（历史消息 + 思维链叠加），正常不触发
MAX_SUBTITLE_CHARS = 400000


def _parse_ask_questions(call: dict) -> list:
    """解析 ask_user_question 的工具参数，顺便对提问文本做公式形态硬清洗。

    模型生成的 question/header/options 文本同样受「公式铁律」约束，
    这里在发给前端前统一成 $...$ / $$...$$，避免前端渲染出 \(...\) 原文。
    """
    try:
        args = json.loads((call.get("function") or {}).get("arguments") or "{}")
    except Exception:
        return []
    out = []
    for q in (args.get("questions") or [])[:MAX_ASK_QUESTIONS]:
        if not isinstance(q, dict):
            continue
        options = []
        for o in (q.get("options") or [])[:6]:
            if isinstance(o, dict) and o.get("label"):
                options.append({
                    "label": normalize_math(str(o["label"])),
                    "description": normalize_math(str(o.get("description") or "")),
                })
        out.append({
            "id": str(q.get("id") or f"q{len(out) + 1}"),
            "question": normalize_math(str(q.get("question") or "")),
            "header": normalize_math(str(q.get("header") or "")),
            "options": options,
            "multi_select": bool(q.get("multi_select")),
        })
    return out


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
        # 工具结果消息：必须紧跟带 tool_calls 的 assistant，孤立则丢弃（防历史截断后 400）
        if m.role == "tool":
            prev = out[-1] if out else None
            if not (prev and prev.get("role") == "assistant" and prev.get("tool_calls")):
                continue
            raw = m.content or ""
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            # 数组形式的工具结果（read_image 返回的图片内容块）要还原成数组；
            # 字符串/对象（如 ask 的 answers）保持原样回填
            out.append({"role": "tool", "tool_call_id": m.tool_call_id or "",
                        "content": parsed if isinstance(parsed, list) else raw})
            continue
        item = {"role": m.role, "content": content}
        if m.role == "assistant" and m.tool_calls:
            try:
                item["tool_calls"] = json.loads(m.tool_calls)
            except Exception:
                pass
        if m.reasoning and m.role == "assistant" and reasoning_left > 0:
            # 思维链只回填最近少数几轮：历史 + 思维链都占上下文，全量回填
            # 会在长对话中顶爆 token（这也是节省账单的关键）
            item["reasoning_content"] = m.reasoning
            reasoning_left -= 1
        out.append(item)

    # 兜底：带 tool_calls 的 assistant 后面必须跟齐对应的 tool 结果，否则 API 报 400。
    # 注意区分两种情形：
    #   1) 它已经是历史最后一条 —— "正在等待用户作答"，调用方马上会补 tool 消息，必须保留；
    #   2) 后面跟了别的消息却没有 tool 结果 —— 历史断裂（例如用户跳过提问直接打字），摘掉 tool_calls。
    cleaned = []
    for i, item in enumerate(out):
        if item.get("role") == "assistant" and item.get("tool_calls"):
            wanted = {tc.get("id") for tc in item["tool_calls"]}
            got = set()
            j = i + 1
            while j < len(out) and out[j].get("role") == "tool":
                got.add(out[j].get("tool_call_id"))
                j += 1
            if j < len(out) and not wanted.issubset(got):
                item = {k: v for k, v in item.items() if k != "tool_calls"}
        cleaned.append(item)

    # 图片型工具结果（read_image）非常占 token：只保留最近一条原样，
    # 更早的换成占位文字，避免长对话里反复重发整张图的 base64。
    visual_left = 1
    for item in reversed(cleaned):
        if item.get("role") == "tool" and isinstance(item.get("content"), list):
            if visual_left > 0:
                visual_left -= 1
            else:
                item["content"] = "[该图片此前已查看过，内容见当轮对话；如需重看可再次调用工具]"
    return cleaned


def _ensure_session(db: Session, conv_id: str, subject: str, mode: str):
    """确保会话行存在（老流程/直接发送时自动建），返回会话行（新建则 commit）。

    调用方通常紧接着调 _append_message：把返回的行传给它作 _sess，
    复用查询，避免同一发送流程里 session 被查两次。
    """
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        sess = ChatSession(conv_id=conv_id, subject=subject, mode=mode, name=DEFAULT_NAME)
        db.add(sess)
        db.commit()
    return sess


def _append_message(
    db: Session,
    conv_id: str, subject: str, mode: str, role: str,
    content, reasoning: str = "", quote: str = "",
    tool_calls: str = "", tool_call_id: str = "",
    _sess=None,
) -> None:
    """写入一条消息，并更新会话 updated_at、首条提问自动命名。

    _sess: 可选，由 _ensure_session 返回的已加载会话行（避免重复查询）。
    """
    import datetime
    # seq 用 max(seq)+1，避免 count(*) 全表扫描行集（两者复杂度同级，但 max 对稀疏
    # seq 更精确，且避免 count 在超大表上的行级聚合）
    top = db.query(func.max(ChatMessage.seq)).filter(ChatMessage.conv_id == conv_id).scalar()
    seq = (top + 1) if top is not None else 0
    jsonable = content if isinstance(content, (list, dict)) else (content or "")
    stored = json.dumps(jsonable, ensure_ascii=False) if isinstance(jsonable, (list, dict)) else str(jsonable)
    db.add(ChatMessage(conv_id=conv_id, subject=subject, mode=mode,
                       role=role, content=stored, reasoning=reasoning or "",
                       quote=quote or "", tool_calls=tool_calls or "",
                       tool_call_id=tool_call_id or "", seq=seq))
    sess = _sess if _sess is not None else db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if sess:
        sess.updated_at = datetime.datetime.utcnow()
        if role == "user" and (not sess.name or sess.name == DEFAULT_NAME):
            text = content if isinstance(content, str) else ""
            sess.name = (text.strip()[:30] or DEFAULT_NAME)
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
        .filter(ChatMessage.subject == subject)  # 只统计当前科目，缩小聚合范围
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
            "effort": s.effort or "",  # 会话级思考强度（空 = 全局默认 high）
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
        base = {"role": m.role, "reasoning": m.reasoning or "", "quote": m.quote or "",
                "tool_call_id": m.tool_call_id or ""}
        # ask 工具的回答轮：content 是 {"answers":[...]}，单独解析给前端渲染
        if m.role == "tool":
            try:
                answers = (json.loads(m.content) or {}).get("answers") or []
            except Exception:
                answers = []
            items.append({**base, "content": "", "answers": answers,
                          "has_image": False, "images": []})
            continue
        content = m.content
        try:
            parsed = json.loads(content) if content else ""
            content = parsed
        except Exception:
            pass
        if isinstance(content, list):  # 含图片的 user 消息
            texts = [p.get("text", "") for p in content if isinstance(p, dict)]
            imgs = [p.get("image_url", {}).get("url", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "image_url"]
            items.append({**base, "content": "\n".join(texts),
                          "has_image": bool(imgs), "images": imgs})
        else:
            items.append({**base, "content": content or "", "has_image": False, "images": []})
        # assistant 要求调用 ask 工具时，把问题一并返回（前端渲染历史卡片）
        if m.role == "assistant" and m.tool_calls and items:
            try:
                for c in json.loads(m.tool_calls):
                    if (c.get("function") or {}).get("name") == ASK_TOOL_NAME:
                        items[-1]["ask_questions"] = _parse_ask_questions(c)
                        break
            except Exception:
                pass
    return {
        "conversation_id": conv_id,
        "name": sess.name if sess else DEFAULT_NAME,
        "mode": sess.mode if sess else "A",
        "model": sess.model if sess and sess.model else "",
        "effort": sess.effort if sess and sess.effort else "",
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


@router.post("/conversations/{conv_id}/effort")
def set_conversation_effort(conv_id: str, effort: str = Query(...),
                            db: Session = Depends(get_db)):
    """会话级思考强度切换：只改当前会话（不碰全局设置），发送即生效"""
    e = (effort or "").strip()[:20]
    if e and e not in ("low", "medium", "high", "xhigh", "max"):
        raise HTTPException(400, f"不支持的思考强度: {effort}")
    sess = db.query(ChatSession).filter(ChatSession.conv_id == conv_id).first()
    if not sess:
        raise HTTPException(404, "会话不存在")
    sess.effort = e
    db.commit()
    return {"conversation_id": conv_id, "effort": sess.effort}


# ---------- 聊天（SSE 流式） ----------
def _sse(payload: dict) -> str:
    """序列化一条 SSE 事件：data: {json}\n\n"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/send-stream")
async def send_message_stream(
    query: str = Form(""),
    subject: str = Form("math"),
    mode: str = Form("A"),
    subtitle_context: str = Form(""),
    conversation_id: str = Form(""),
    quote: str = Form(""),  # 引用内容（JSON 字符串 {"text": "..."}，用户引用 AI 回答某段特别说明）
    image: List[UploadFile] = File(None),  # 支持多图（前端同名多字段 append，最多 4 张）
    watched_video_ids: str = Form(""),  # 模式B：勾选的视频ID（JSON 数组字符串）
    # ask 工具回答（JSON 字符串 {"tool_call_id": "...", "answers": [{"id","selected","custom"}]}）：
    # 非空表示这一轮是"回答 AI 的提问"，后端把它作为 tool 结果续上继续推理
    ask_answers: str = Form(""),
    db: Session = Depends(get_db),
):
    """发送消息（可选多张图片），SSE 流式返回 AI 回答。

    事件序列：
      data: {"type":"reasoning","text":"思维链片段"} （可多次，正文前或与正文交替）
      data: {"type":"delta","text":"正文片段"}       （可多次）
      data: {"type":"ask","tool_call_id":"...","questions":[...],"text":"前言"} （模型要求提问）
      data: {"type":"done","conversation_id":...}    （正常结束）
      data: {"type":"error","detail":"..."}          （出错时替代 done）
    """
    if subject not in SUBJECT_NAMES:
        raise HTTPException(400, f"不支持的科目: {subject}")
    if mode not in MODE_NAMES:
        raise HTTPException(400, f"不支持的 Agent 模式: {mode}")
    if not (query or "").strip() and not image and not (ask_answers or "").strip():
        raise HTTPException(400, "消息不能为空")

    # 引用解析：前端传 {"text": "原文"}，取 text 截断（防上下文膨胀）
    quote_text = ""
    if quote and quote.strip():
        try:
            qobj = json.loads(quote)
            quote_text = str(qobj.get("text") or "").strip()[:500]
        except Exception:
            quote_text = quote.strip()[:500]
    quote_prefix = f"[用户引用了你的回答：「{quote_text}」]\n" if quote_text else ""

    # 图片 → 落盘（工具化看图：图片不再直接塞进上下文，只把本地路径告诉模型，
    # 由模型调用 read_image / modlens_read_image 取图）
    if len(image or []) > 4:
        raise HTTPException(400, "一次最多上传 4 张图片")
    image_paths: List[str] = []
    if image:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for f in image[:4]:  # 服务端防御上限
            data = await f.read()
            if not data:
                continue
            ext = (f.filename or "").split(".")[-1].lower()
            if ext not in ("jpg", "jpeg", "png", "webp", "gif", "bmp"):
                ext = "jpeg"
            name = f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.{ext}"
            path = UPLOAD_DIR / name
            path.write_bytes(data)
            image_paths.append(to_rel(path))

    wids = []
    if watched_video_ids and watched_video_ids.strip():
        try:
            wids = [int(x) for x in watched_video_ids.strip().strip("[]").split(",") if x.strip()]
        except ValueError:
            wids = []

    conv_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    # 确保会话行存在并取其当前值（新建+旧行一次查询搞定，供后续复用）
    sess_row = _ensure_session(db, conv_id, subject, mode)

    # 会话级模型：会话行有 model 则用它，否则用全局主模型（llm_service 内部兜底）
    conv_model = sess_row.model.strip() if sess_row and sess_row.model else ""
    conv_effort = sess_row.effort.strip() if sess_row and sess_row.effort else ""

    # 从 DB 载入历史（含 reasoning 回填限制），保证重启后上下文不丢
    history = _load_messages(db, conv_id)

    # ---- ask 工具的回答轮：把用户作答作为 tool 结果续上（不再追加 user 消息）----
    ask_round = False
    if ask_answers and ask_answers.strip():
        ask_round = True
        try:
            ask_obj = json.loads(ask_answers)
        except Exception:
            raise HTTPException(400, "ask_answers 不是合法 JSON")
        ask_tcid = str(ask_obj.get("tool_call_id") or "")
        ask_list = ask_obj.get("answers") or []
        if not ask_tcid or not isinstance(ask_list, list):
            raise HTTPException(400, "ask_answers 缺少 tool_call_id 或 answers")
        tool_content = json.dumps({"answers": ask_list}, ensure_ascii=False)
        _append_message(db, conv_id, subject, mode, "tool", tool_content,
                        tool_call_id=ask_tcid)
        history.append({"role": "tool", "tool_call_id": ask_tcid,
                        "content": tool_content})

    # ---- 构建用户消息（文本 + 图片路径提示 + 引用）----
    if not ask_round:
        img_note = ""
        if image_paths:
            img_note = ("\n[用户上传了 %d 张图片，本地路径：%s]\n"
                        "（图片内容不在上下文里：先调用 read_image 或 modlens_read_image "
                        "查看图片，再结合图片回答）"
                        % (len(image_paths), "、".join(image_paths)))
        user_content = quote_prefix + query + img_note
        _append_message(db, conv_id, subject, mode, "user", user_content,
                        quote=quote_text, _sess=sess_row)
        history.append({"role": "user", "content": user_content})

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
                if v.summary_status == "done" and v.summary_path:
                    sm = resolve(v.summary_path)
                    if sm:
                        try:
                            summary = sm.read_text(encoding="utf-8")
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
        try:
            round_no = 0
            while round_no < MAX_TOOL_ROUNDS:
                round_no += 1
                reasoning_acc = []
                content_acc = []
                tool_calls_json = ""
                async for kind, text in chat_completion_stream(
                    messages=history,
                    subject=subject,
                    mode=mode,
                    subtitle_context=subtitle_context,
                    model_override=conv_model,
                    effort=conv_effort,
                ):
                    if kind == "reasoning":
                        reasoning_acc.append(text)
                        # 思维链实时推给前端做"深度思考中"展示（独立于正文，不混入回答）
                        yield _sse({"type": "reasoning", "text": text})
                    elif kind == "tool_calls":
                        tool_calls_json = text  # 完整参数在流末尾一次性给出
                    else:
                        content_acc.append(text)
                        yield _sse({"type": "delta", "text": text})

                answer = "".join(content_acc)
                reasoning = "".join(reasoning_acc)

                # ---- 没有工具调用：这就是最终回答 ----
                if not tool_calls_json:
                    if not answer:
                        yield _sse({"type": "error", "detail": "AI 返回为空"})
                        return
                    # 公式形态硬约束：落库前统一为 $ 格式（前端渲染的形态）
                    answer = normalize_math(answer)
                    # 只有在完整拿到回答后才写入历史（中途断流不落库，前端可重发）
                    _append_message(db, conv_id, subject, mode, "assistant", answer,
                                    reasoning=reasoning)
                    yield _sse({"type": "done", "conversation_id": conv_id})
                    return

                try:
                    calls = json.loads(tool_calls_json)
                except Exception:
                    calls = []
                if not calls:
                    yield _sse({"type": "error", "detail": "工具调用参数解析失败"})
                    return

                # ---- ask 工具：要等用户作答，本轮到此为止（前端提交后再续）----
                ask_calls = [c for c in calls
                             if (c.get("function") or {}).get("name") == ASK_TOOL_NAME]
                if ask_calls:
                    call = ask_calls[0]
                    questions = _parse_ask_questions(call)
                    # 同轮的其他工具调用拿不到结果，落库时只保留 ask，
                    # 否则历史里 tool_calls 与 tool 结果对不上（API 400）
                    only_ask = json.dumps([call], ensure_ascii=False)
                    answer = normalize_math(answer)
                    _append_message(db, conv_id, subject, mode, "assistant", answer,
                                    reasoning=reasoning, tool_calls=only_ask)
                    yield _sse({"type": "ask", "tool_call_id": call.get("id") or "",
                                "questions": questions, "text": answer})
                    yield _sse({"type": "done", "conversation_id": conv_id})
                    return

                # ---- 视觉工具：服务端自己就能执行，执行完带着结果继续让模型作答 ----
                if round_no >= MAX_TOOL_ROUNDS:
                    yield _sse({"type": "error", "detail": "工具调用层数过深，已中止"})
                    return
                _append_message(db, conv_id, subject, mode, "assistant",
                                normalize_math(answer), reasoning=reasoning,
                                tool_calls=tool_calls_json)
                assistant_msg = {"role": "assistant", "content": answer, "tool_calls": calls}
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning
                history.append(assistant_msg)
                for call in calls:
                    fn = call.get("function") or {}
                    tool_name = fn.get("name") or ""
                    try:
                        tool_args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        tool_args = {}
                    yield _sse({"type": "tool", "name": tool_name, "status": "running"})
                    try:
                        result = await execute_tool_call(tool_name, tool_args)
                        failed = False
                    except Exception as e:
                        result = f"[工具执行失败：{e}]"
                        failed = True
                    stored = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                    _append_message(db, conv_id, subject, mode, "tool", stored,
                                    tool_call_id=call.get("id") or "")
                    history.append({"role": "tool", "tool_call_id": call.get("id") or "",
                                    "content": result})
                    yield _sse({"type": "tool", "name": tool_name,
                                "status": "failed" if failed else "done"})
            yield _sse({"type": "error", "detail": "工具调用层数过深，已中止"})
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