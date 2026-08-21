"""考研学习平台后端（裸跑版，不依赖 Docker）"""
import os
import shutil
import atexit
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import settings, BASE_DIR
from .core.database import init_db, SessionLocal
from .models import Video
from .api.routes import videos, chat, config, folders

# SQLite 数据库：放在项目 data 目录（避免容器化遗留路径）
DB_PATH = str(BASE_DIR / "data" / "kaoyan.db")
FRONTEND_DIR = str(BASE_DIR / "frontend")

app = FastAPI(title="考研学习平台", version="2.1.0")

# 退出时备份数据库（data/kaoyan.db.bak）
def _backup_db():
    try:
        shutil.copy2(DB_PATH, DB_PATH + ".bak")
        print(f"[ok] 数据库已备份: {DB_PATH}.bak")
    except Exception:
        pass
atexit.register(_backup_db)

# CORS：本地部署（FastAPI 托管前端静态文件 + 前端可配置 API_BASE 指向其他端口）。
# 不再允许 credentials 与 "*" 组合（浏览器会拒绝该组合，属于无效配置）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 鉴权：.env 配置 PLATFORM_TOKEN 后，所有 /api 接口需携带
#   Authorization: Bearer <token>
# 或（仅视频流，因 <video> 标签无法加请求头）：?token=<token>
# 未配置 token 时保持全开放（开箱即用，与旧版行为一致）。
# /api/health 永远匿名放行（健康检查/监控用途）。
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    token = settings.platform_token
    if token and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        header = request.headers.get("authorization", "")
        query_token = request.query_params.get("token", "")
        if header != f"Bearer {token}" and query_token != token:
            return JSONResponse(
                {"detail": "未授权：请在设置中配置访问令牌（PLATFORM_TOKEN）"},
                status_code=401,
            )
    return await call_next(request)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "message": "考研学习平台后端运行中"}

# 挂载路由器
app.include_router(videos.router)
app.include_router(chat.router)
app.include_router(config.router)
app.include_router(folders.router)

# 初始化数据库
init_db()

# 启动时重置卡住的字幕提取状态
try:
    s = SessionLocal()
    stuck = s.query(Video).filter(Video.subtitle_status == "processing").all()
    for v in stuck:
        v.subtitle_status = "pending"
        print(f"  [i] 重置卡住的视频 #{v.id}: {v.title}")
    if stuck:
        s.commit()
    s.close()
except Exception:
    pass

# 创建存储目录
os.makedirs(settings.video_dir, exist_ok=True)
os.makedirs(settings.subtitle_dir, exist_ok=True)
os.makedirs(settings.data_dir, exist_ok=True)

# 前端静态文件
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
