"""配置管理接口（只读状态）"""
from fastapi import APIRouter

from ...core.config import settings

router = APIRouter(prefix="/api/config", tags=["配置"])


@router.get("/status")
def get_config_status():
    """返回平台所需 Key 的配置状态（只回显是否已配置，永不回显 Key 值本身）。

    密钥一律只放在服务器的 .env 文件中，前端不再具备写入能力。
    """
    return {
        "deepseek": bool(settings.deepseek_api_key),   # 聊天
        "zhipu": bool(settings.zhipu_api_key),         # 看图
        "qwen": bool(settings.qwen_api_key),           # 字幕 ASR
        "auth_enabled": bool(settings.platform_token), # 访问令牌是否已启用
    }