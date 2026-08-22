"""配置管理接口：模型 API 配置（设置页读写，保存即生效）+ 状态"""
from typing import Optional, Dict, Any

from fastapi import APIRouter
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