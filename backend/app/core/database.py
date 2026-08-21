"""数据库：引擎 / 会话工厂 / 声明基类 / 初始化迁移"""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from .config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db():
    """建表 + 老库增量迁移（模型在函数内延迟导入，避免与 models.py 循环依赖）"""
    from .. import models  # noqa: F401  确保所有模型注册到 Base.metadata
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """老库增量迁移：给 chat_sessions 补 name 列（若已存在则跳过）"""
    import sqlalchemy as sa
    insp = sa.inspect(engine)
    tables = insp.get_table_names()
    if "chat_sessions" in tables:
        cols = {c["name"] for c in insp.get_columns("chat_sessions")}
        if "name" not in cols:
            with engine.begin() as conn:
                conn.execute(sa.text("ALTER TABLE chat_sessions ADD COLUMN name TEXT"))
            print("[migrate] chat_sessions 已添加 name 列")
    if "chat_messages" not in tables:
        print("[migrate] 将创建 chat_messages 表")