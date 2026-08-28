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
    """老库增量迁移（缺列则补，已存在则跳过）"""
    import sqlalchemy as sa
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())

    def add_column(table: str, col: str, ddl: str):
        if table in tables and col not in {c["name"] for c in insp.get_columns(table)}:
            with engine.begin() as conn:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            print(f"[migrate] {table} 已添加 {col} 列")

    # 历史版本逐个补列
    add_column("chat_sessions", "name", "TEXT")
    add_column("chat_sessions", "model", "VARCHAR(120) DEFAULT ''")
    add_column("videos", "summary_status", "VARCHAR(20) DEFAULT 'none'")
    add_column("videos", "summary_path", "VARCHAR(500) DEFAULT ''")
    add_column("chat_messages", "quote", "TEXT")
    if "chat_messages" not in tables:
        print("[migrate] 将创建 chat_messages 表")