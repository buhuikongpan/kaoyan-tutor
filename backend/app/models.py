"""ORM 模型（SQLAlchemy）"""
import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Float

from .core.database import Base


class Folder(Base):
    """文件夹/章节目录"""
    __tablename__ = "folders"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String(20), index=True, nullable=False)  # math / english / politics
    name = Column(String(255), nullable=False)
    parent_id = Column(Integer, default=0, index=True)  # 0 = 根级
    sort_order = Column(Integer, default=9999)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    subject = Column(String(20), index=True, nullable=False)
    filename = Column(String(255), nullable=False)
    title = Column(String(255), default="")
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, default=0)
    duration = Column(Float, default=0)
    sort_order = Column(Integer, default=9999)
    folder_id = Column(Integer, default=0, index=True)  # 0 = 未分类
    subtitle_path = Column(String(500), default="")
    subtitle_status = Column(String(20), default="pending")
    summary_path = Column(String(500), default="")    # 课程总结 markdown 文件
    summary_status = Column(String(20), default="none")  # none / pending / processing / done / failed
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Subtitle(Base):
    __tablename__ = "subtitles"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, index=True, nullable=False)
    subject = Column(String(20), index=True, nullable=False)
    start_time = Column(Float, default=0)
    end_time = Column(Float, default=0)
    text = Column(Text, default="")
    seq = Column(Integer, default=0)


class ChatSession(Base):
    """对话会话（按科目/模式存储，重启不丢，支持重命名）"""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    conv_id = Column(String(64), unique=True, index=True, nullable=False)  # 平台侧会话 ID
    mode = Column(String(8), index=True, nullable=False)  # A / B / C
    subject = Column(String(20), default="math", index=True)
    name = Column(String(255), default="新对话")  # 会话名（首条提问自动命名 / 可重命名）
    model = Column(String(120), default="")  # 会话级模型（空 = 用全局主模型配置）
    dify_conversation_id = Column(String(64), default="")  # Dify 侧 conversation_id（遗留）
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


class ChatMessage(Base):
    """对话消息（持久化，服务重启不丢）"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conv_id = Column(String(64), index=True, nullable=False)
    subject = Column(String(20), default="math")
    mode = Column(String(8), default="A")
    role = Column(String(16), nullable=False)  # user / assistant
    content = Column(Text, default="")  # 纯文本，或含图片时的 JSON 数组
    reasoning = Column(Text, default="")  # DeepSeek 思维链（下轮回填保持连续性）
    quote = Column(Text, default="")  # 引用原文（用户引用 AI 回答某段进行特别说明）
    seq = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class PlatformConfig(Base):
    """平台运行时配置（模型 API 等，key-value JSON），设置页保存即生效"""
    __tablename__ = "platform_config"

    key = Column(String(64), primary_key=True)  # 如 model_config
    value = Column(Text, default="{}")          # JSON 字符串
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)