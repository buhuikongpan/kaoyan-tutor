"""文件夹相关 Pydantic 请求模型"""
from pydantic import BaseModel


class FolderCreate(BaseModel):
    subject: str
    name: str
    parent_id: int = 0


class FolderRename(BaseModel):
    name: str