# 📚 考研学习平台（本地直连版）

支持 **数学、英语、政治** 三科目的考研学习平台。AI 能力**本地直连**：
- 💬 **聊天**：DeepSeek（deepseek-v4-flash，thinking 模式，**SSE 流式输出**）
- 🖼️ **看图**：智谱 GLM-4V（glm-4v-flash）
- 🎬 **字幕**：千问 ASR（qwen3-asr-flash）

三个 Agent 模式：**A 即时问答**（看视频随时提问，自动带字幕）/ **B 引导输出**（AI 出题引导）/ **C 课后问答**（自由提问）。

> 曾迁移到 Dify 又迁回（Dify 界面/依赖过于笨重）。本版不依赖 Dify，纯本地直连。

---

## 快速启动

### 环境要求
- Python 3.11+
- ffmpeg（`winget install ffmpeg`）

### 步骤

```bash
# 1. 安装依赖
cd backend
pip install -r requirements.txt

# 2. 配置 .env（复制模板填入 Key）
copy ..\.env.example ..\.env
#    DEEPSEEK_API_KEY = 聊天（必填）
#    ZHIPU_API_KEY    = 看图
#    QWEN_API_KEY     = 字幕识别
#    PLATFORM_TOKEN   = （可选）访问令牌，开启后接口需鉴权

# 3. 启动
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. 打开 http://localhost:8000
```

### 或双击脚本
`scripts\start.bat`（自动检查依赖并启动）

---

## 使用

1. **上传视频**：选择科目 → 上传 MP4 → 自动提取字幕（千问 ASR，几分钟）
2. **学习**：选视频播放 → 切换 Agent：
   - **A 即时问答**：边看边问（自动带**当前播放位置附近**的字幕），可发图片（草稿/笔记截图）
   - **B 引导输出**：让 AI 出题/追问，答错给提示
   - **C 课后问答**：自由提问
3. **字幕文件**：自动存 `storage/subtitles/`，可点 📄 下载

---

## 安全（访问令牌）

平台默认**不鉴权**（`PLATFORM_TOKEN` 留空），局域网内任何设备都能访问。
多人共用或多设备使用时，建议：

1. 在 `.env` 设置 `PLATFORM_TOKEN=你的随机令牌`
2. 重启服务
3. 页面右上角 ⚙️ 设置 → 填写令牌 → 保存

之后所有 `/api` 接口都需要令牌（`Authorization: Bearer`；视频流因 `<video>` 标签无法带请求头，自动走 `?token=` 查询参数）。密钥只存放在服务器 `.env`，前端网页无法读写。

---

## 项目结构

```
DIFY考研学习平台/
├── .env / .env.example        # API Key + PLATFORM_TOKEN 配置
├── backend/                   # FastAPI 后端（裸跑）
│   └── app/
│       ├── main.py            # 入口（含鉴权中间件）
│       ├── config.py          # 配置
│       ├── database.py        # SQLite
│       ├── routers/
│       │   ├── videos.py      # 视频/字幕(ASR)
│       │   ├── chat.py        # 聊天（SSE 流式，直连 DeepSeek/智谱）
│       │   ├── folders.py     # 文件夹
│       │   └── config.py      # 配置状态（只读）
│       └── services/
│           ├── llm_service.py # DeepSeek 流式聊天 + 智谱看图
│           └── asr_service.py # 千问 ASR
├── frontend/                  # 前端页面（原生 JS + KaTeX）
├── storage/                   # 视频/字幕
├── data/                      # SQLite + 日志
└── scripts/start.bat          # 一键启动
```

---

## 常见问题

- **平台打不开**：双击 `scripts\start.bat`（弹窗开着即运行，关窗即停）
- **看图报错 429**：智谱免费模型限流，等 1-2 分钟重试
- **字幕提取失败**：检查 `QWEN_API_KEY` 与网络
- **接口提示"未授权"**：服务器 `.env` 已配 `PLATFORM_TOKEN`，请在 ⚙️ 设置中填写
- **starlette 报错**：确认 `pip install starlette==1.6.0`（fastapi 0.141.1 配套版本）