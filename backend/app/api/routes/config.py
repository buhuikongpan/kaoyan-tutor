"""配置管理接口：模型 API 配置（设置页读写，保存即生效）+ 状态"""
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...core.config import settings
from ...core.model_config import load_model_config, save_model_config, public_view

router = APIRouter(prefix="/api/config", tags=["配置"])


@router.get("/status")
def get_config_status():
    """返回平台所需 Key 的配置状态（配合 /api/config/model 使用）"""
    return {
        "deepseek": bool(load_model_config()["main"].get("api_key")),
        "zhipu": bool(load_model_config()["vision"].get("api_key")),
        "qwen": bool(load_model_config()["asr"].get("api_key")),
        "auth_enabled": bool(settings.platform_token),  # 访问令牌是否已启用
    }


class MainModelIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    multimodal: bool = False


class VisionModelIn(BaseModel):
    enabled: bool = True
    base_url: str = ""
    api_key: str = ""
    model: str = ""


class AsrModelIn(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    provider: str = ""   # local / qwen
    size: str = ""       # faster-whisper 模型档位：small / medium


class ModelConfigIn(BaseModel):
    main: Optional[MainModelIn] = None
    vision: Optional[VisionModelIn] = None
    asr: Optional[AsrModelIn] = None


@router.get("/model")
def get_model_config():
    """读取模型配置（key 只回显掩码 + has_key 标记，不回传明文）"""
    return public_view(load_model_config())


@router.post("/model")
def post_model_config(data: ModelConfigIn):
    """保存模型配置；api_key 留空表示保留原 key；保存后立即生效（无需重启）"""
    cfg = data.model_dump(exclude_none=True)
    if data.main is None:
        cfg.pop("main", None)
    if data.vision is None:
        cfg.pop("vision", None)
    if data.asr is None:
        cfg.pop("asr", None)
    saved = save_model_config(cfg)
    return {"message": "配置已保存，立即生效", "config": public_view(saved)}


class ListModelsIn(BaseModel):
    base_url: str = ""
    api_key: str = ""


@router.post("/list-models")
async def list_models(data: ListModelsIn):
    """从 OpenAI 兼容端点拉取真实可用的模型列表（GET {base_url}/models）。

    用当前表单填写的地址和 Key 临时请求（不落库）；地址兼容两种写法：
      - base 形式：https://api.deepseek.com/v1
      - 完整端点：https://api.deepseek.com/v1/chat/completions（自动提取 base）
    端点不支持时返回 error，由前端提示用户手动输入模型名。
    """
    base_url = (data.base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(400, "请先填写 API 地址")
    models, error = await _fetch_models(base_url, data.api_key)
    return {"models": models, "error": error}


@router.post("/chat-models")
async def chat_models():
    """对话框模型下拉：用**已保存**的主模型配置（服务端 Key，明文不出服务器）
    拉取可用模型列表，供对话框一键切换会话级模型。
    """
    cfg = load_model_config()["main"]
    base_url = (cfg.get("base_url") or "").strip()
    models, error = await _fetch_models(base_url, cfg.get("api_key") or "")
    return {"models": models, "current": (cfg.get("model") or "").strip(), "error": error}


async def _fetch_models(base_url: str, api_key: str) -> tuple:
    """请求 GET {base_url}/models，返回 (models, error)。base_url 兼容完整端点写法。"""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return [], "未配置 API 地址"
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")].rstrip("/")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
            resp = await client.get(base_url + "/models", headers=headers)
        if resp.status_code != 200:
            return [], f"接口返回 HTTP {resp.status_code}"
        payload = resp.json()
        models = [m.get("id") for m in payload.get("data", []) if m.get("id")]
        return models, ""
    except Exception as e:
        return [], str(e)[:200]