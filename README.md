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
`scripts\考研学习平台.bat`（自动检查依赖并启动；服务已在运行时会询问是否重启）

---

## 使用

1. **上传视频**：选择科目 → 上传 MP4 → 自动提取字幕（本地 Whisper 或千问 ASR，几分钟）
   也可以直接把视频放进 `storage/videos/<科目>/`，再点侧栏 `🔎` 重新扫描即可出现在列表
2. **学习**：选视频播放（自动**从上次播放位置续播**）→ 切换 Agent：
   - **A 即时问答**：边看边问（自动带**整讲字幕全文 + 当前播放位置**，agent 完整知道你学到哪），可发图片（草稿/笔记截图）
   - **B 引导输出**：让 AI 出题/追问，答错给提示（勾选视频后，优先用**课程总结**，更结构化）
   - **C 课后问答**：自由提问
3. **🎤 语音输入**：输入框左侧麦克风按钮 → 说话 → 再点停止，自动识别成文字填入输入框（复用设置页的 ASR 引擎：本地 Whisper 免费离线 / 千问云），改完回车发送
4. **字幕文件**：自动存 `storage/subtitles/`，可点 📄 下载

## 课程总结（自动生成 + 一键补生成）

字幕提取完成后，后端自动为每节课生成一份**结构化课程总结**（分段提取 → 合并防漏，保信息密度）：

- 内容五段式：**知识点（带时间戳）/ 核心公式 / 例题与题型 / 方法技巧 / 易错点**
- 视频树中 `📄` 可查看/下载 markdown；无总结的字幕已就绪视频显示 `📄生成`（单节手动生成），失败显示 `📄重试`
- **存量视频一键补齐**：侧栏 `📑` 按钮把「字幕已完成但无总结」的视频全部排队串行生成（约 10~60 秒/个，后台执行不阻塞页面）
- 模式 B 引导输出优先使用勾选视频的总结（高密度、跨多节不撑爆上下文）；
  尚无总结的视频自动回退字幕全文
- 一份总结仅 1~2 千 token，是全文的 1/10，适合作为长期学习画像

> 模型上下文为 1M token：单节全文（约 1.5 万 token）与多节总结都毫无压力，
> 截断阈值仅作极端防御。
> ⚡ 设置页勾选「模式A 精简上下文」后，即时问答只发「课程总结 + 播放位置前后 ±10 分钟字幕」，
> 回答更快更省（默认不勾选，保持全文上下文）。

---

## 模型 API 设置（⚙️ 设置）

页面右上角 ⚙️ 可配置全部模型（**OpenAI 兼容**，保存即生效，无需重启）：

- **🧠 主模型**：API 地址 + Key + 模型名（聊天/归纳总结用），可勾选「支持多模态」
- **👁️ 视觉辅助**：启停开关 + 地址 + Key + 模型名（默认智谱 GLM-4V）
- **🎬 语音识别 ASR**：地址 + Key + 模型名（默认千问 qwen3-asr-flash）
- **🔐 访问令牌**：PLATFORM_TOKEN 的本地保存

**图片发送逻辑**：
- 主模型勾了「多模态」→ 图片按 OpenAI 格式直接发给主模型
- 没勾 → 走视觉辅助模型分析成文字再给主模型；视觉辅助关闭且主模型不支持 → 提示

配置存服务器数据库（`platform_config` 表），`.env` 中的同名 Key 作为首次启动兜底；
Key 回显只显示掩码，不泄露明文。

---

## 安全（访问令牌）

平台默认**不鉴权**（`PLATFORM_TOKEN` 留空），局域网内任何设备都能访问。
多人共用或多设备使用时，建议：

1. 在 `.env` 设置 `PLATFORM_TOKEN=你的随机令牌`
2. 重启服务
3. 页面右上角 ⚙️ 设置 → 填写令牌 → 保存

之后所有 `/api` 接口都需要令牌（`Authorization: Bearer`；视频流因 `<video>` 标签无法带请求头，自动走 `?token=` 查询参数）。模型 API Key 存于服务器数据库，接口受令牌保护。

---

## 项目结构

```
DIFY考研学习平台/
├── backend/                   # FastAPI 后端（裸跑）
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py            # 入口（含鉴权中间件）
│   │   ├── models.py          # ORM 模型（SQLAlchemy）
│   │   ├── core/
│   │   │   ├── config.py      # 配置（settings + 项目根计算）
│   │   │   └── database.py    # 引擎 / 会话 / 建表迁移
│   │   ├── api/
│   │   │   ├── deps.py        # 公共依赖（get_db）
│   │   │   └── routes/
│   │   │       ├── videos.py  # 视频/字幕(ASR)
│   │   │       ├── chat.py    # 聊天（SSE 流式，直连 DeepSeek/智谱）
│   │   │       ├── folders.py # 文件夹
│   │   │       └── config.py  # 配置状态（只读）
│   │   ├── schemas/           # Pydantic 请求模型
│   │   └── services/
│   │       ├── llm_service.py # DeepSeek 流式聊天 + 智谱看图
│   │       └── asr_service.py # 千问 ASR
│   └── tests/                 # 单元测试（unittest，无 pytest 依赖）
├── frontend/                  # 前端页面（原生 JS + KaTeX）
│   ├── index.html
│   ├── css/  js/  vendor/
├── scripts/考研学习平台.bat          # 一键启动
├── docs/                      # 项目文档（Agent 提示词、备忘）
├── storage/                   # 运行时数据：videos/（视频）subtitles/（字幕）summaries/（课程总结）
├── data/                      # SQLite + 日志（运行时数据）
├── .env / .env.example        # API Key + PLATFORM_TOKEN 配置
├── .gitignore
└── README.md
```

### 运行单元测试

```bash
cd backend
python -m unittest discover -s tests -v
```

- `test_smoke`：基础路由与参数校验
- `test_stream_mock`：MockTransport 内存模拟 DeepSeek 的 SSE 响应，验证流式事件序列与落库
- `test_summary`：MockTransport 模拟「分段提取 → 合并」两轮调用，验证课程总结生成与状态流转

全部测试**不访问外网、不消耗 API 配额**；各 mock 模块通过 setUpModule/tearDownModule
隔离全局替换，可同进程并行。

---

## 常见问题

- **平台打不开**：双击 `scripts\考研学习平台.bat`（弹窗开着即运行，关窗即停）
- **服务更新后想重启**：再双击一次脚本，按提示输入 Y 即结束旧进程并重启
- **看图报错 429**：智谱免费模型限流，等 1-2 分钟重试
- **字幕提取失败**：检查 `QWEN_API_KEY` 与网络；若设置页识别引擎是「本地 Whisper」，需先 `pip install faster-whisper`（或改用千问云）
- **接口提示"未授权"**：服务器 `.env` 已配 `PLATFORM_TOKEN`，请在 ⚙️ 设置中填写
- **starlette 报错**：确认 `pip install starlette==1.6.0`（fastapi 0.141.1 配套版本）