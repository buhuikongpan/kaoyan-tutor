"""模型 API 运行时配置（设置页保存即生效，.env 作为兜底）

结构：
{
  "main":   {"base_url", "api_key", "model", "multimodal"},
  "vision": {"enabled", "base_url", "api_key", "model"},
  "asr":    {"base_url", "api_key", "model"},
}
存储于 platform_config 表（key="model_config"）。
"""
import json
import datetime
from typing import Optional

from .database import SessionLocal
from .config import settings
from ..models import PlatformConfig


def _default_config() -> dict:
    """.env 兜底默认值（首次使用 / DB 无记录时）"""
    return {
        "main": {
            "base_url": "https://api.deepseek.com/v1",
            "api_key": settings.deepseek_api_key or "",
            "model": "deepseek-v4-flash",
            "multimodal": False,   # 主模型是否支持直接发图（多模态）
        },
        "vision": {
            "enabled": True,
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": settings.zhipu_api_key or "",
            "model": "glm-4v-flash",
        },
        "asr": {
            "base_url": "https://dashscope.aliyuncs.com",
            "api_key": settings.qwen_api_key or "",
            "model": "qwen3-asr-flash",
            # 识别引擎：local = 本地 faster-whisper（免费/离线）；qwen = 千问云 API
            "provider": "local",
            # provider=local 时的模型档位：small / medium
            "size": "small",
        },
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """字典深合并：override 里非空字段覆盖 base，嵌套 dict 递归合并"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_model_config() -> dict:
    """读取模型配置：DB 记录叠加 .env 默认（DB 优先，缺字段自动补齐）"""
    db = SessionLocal()
    try:
        row = db.query(PlatformConfig).filter(PlatformConfig.key == "model_config").first()
        saved = json.loads(row.value) if row and row.value else {}
    except Exception:
        saved = {}
    finally:
        db.close()
    return _deep_merge(_default_config(), saved)


def save_model_config(cfg: dict) -> dict:
    """保存模型配置：api_key 传空字符串表示"不修改原 key"；返回合并后的完整配置"""
    current = load_model_config()
    # 保留旧 key：前端不回传明文，空 key 时不覆盖
    for section, fields in (("main", ("api_key",)), ("vision", ("api_key",)), ("asr", ("api_key",))):
        if section in cfg and isinstance(cfg[section], dict):
            if not cfg[section].get("api_key"):
                cfg[section]["api_key"] = current.get(section, {}).get("api_key", "")
    merged = _deep_merge(current, cfg)
    db = SessionLocal()
    try:
        row = db.query(PlatformConfig).filter(PlatformConfig.key == "model_config").first()
        if row:
            row.value = json.dumps(merged, ensure_ascii=False)
            row.updated_at = datetime.datetime.utcnow()
        else:
            db.add(PlatformConfig(key="model_config",
                                  value=json.dumps(merged, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    return merged


def get_endpoint(base_url: str, path: str) -> str:
    """拼接完整端点：兼容用户输入 base（.../v1）或完整端点（.../chat/completions）"""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        return path
    if base_url.endswith(path):
        return base_url
    return base_url + path


def mask_key(key: str) -> str:
    """掩码 key 用于回显：只显示首尾（不泄露明文）"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def public_view(cfg: dict) -> dict:
    """供 GET /api/config/model 回显：key 只显示掩码 + has_key 标记"""
    out = {}
    for section, fields in (("main", ("base_url", "model", "multimodal")),
                            ("vision", ("enabled", "base_url", "model")),
                            ("asr", ("base_url", "model", "provider", "size"))):
        s = cfg.get(section, {})
        item = {f: s.get(f) for f in fields}
        item["has_key"] = bool(s.get("api_key"))
        out[section] = item
    return out