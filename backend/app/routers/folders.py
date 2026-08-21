"""文件夹管理 — 树形目录结构"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from ..database import get_db, Folder, Video

router = APIRouter(prefix="/api/folders", tags=["文件夹"])


@router.get("/tree")
def get_folder_tree(subject: str = Query(""), db: Session = Depends(get_db)):
    """获取文件夹树 + 各文件夹下的视频"""
    folders = db.query(Folder).filter(Folder.subject == subject).order_by(Folder.sort_order).all()
    videos = db.query(Video).filter(Video.subject == subject).order_by(Video.sort_order).all()

    # 构建文件夹树
    folder_map = {}
    for f in folders:
        folder_map[f.id] = {
            "id": f.id,
            "name": f.name,
            "subject": f.subject,
            "parent_id": f.parent_id,
            "sort_order": f.sort_order,
            "children": [],
            "videos": [],
        }

    # 视频归类到文件夹
    folder_roots = []
    for v in videos:
        vinfo = {
            "id": v.id,
            "filename": v.filename,
            "title": v.title,
            "file_size": v.file_size,
            "duration": v.duration,
            "sort_order": v.sort_order,
            "subtitle_status": v.subtitle_status,
        }
        if v.folder_id and v.folder_id in folder_map:
            folder_map[v.folder_id]["videos"].append(vinfo)
        else:
            folder_roots.append(vinfo)

    # 构建树层级
    tree_roots = []
    for f_id, fdata in folder_map.items():
        if fdata["parent_id"] == 0:
            tree_roots.append(fdata)
        elif fdata["parent_id"] in folder_map:
            folder_map[fdata["parent_id"]]["children"].append(fdata)

    return {"folders": tree_roots, "uncategorized": folder_roots}


class FolderCreate(BaseModel):
    subject: str
    name: str
    parent_id: int = 0


@router.post("/create")
def create_folder(data: FolderCreate, db: Session = Depends(get_db)):
    """创建文件夹"""
    folder = Folder(subject=data.subject, name=data.name, parent_id=data.parent_id)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name}


class FolderRename(BaseModel):
    name: str


@router.put("/{folder_id}/rename")
def rename_folder(folder_id: int, data: FolderRename, db: Session = Depends(get_db)):
    """重命名文件夹"""
    folder = db.query(Folder).filter(Folder.id == folder_id).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    folder.name = data.name
    db.commit()
    return {"message": "重命名成功"}


@router.delete("/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db)):
    """删除文件夹（视频移回未分类）"""
    folder = db.query(Folder).filter(Folder.id == folder_id).first()
    if not folder:
        raise HTTPException(404, "文件夹不存在")
    # 子文件夹的视频也移回未分类
    child_ids = [f.id for f in db.query(Folder).filter(Folder.parent_id == folder_id).all()]
    all_ids = [folder_id] + child_ids
    db.query(Video).filter(Video.folder_id.in_(all_ids)).update({"folder_id": 0})
    # 删除子文件夹和自身
    db.query(Folder).filter(Folder.parent_id == folder_id).delete()
    db.delete(folder)
    db.commit()
    return {"message": "已删除"}


@router.put("/{video_id}/move")
def move_video(video_id: int, folder_id: int = Query(...), db: Session = Depends(get_db)):
    """移动视频到文件夹（folder_id=0 为未分类）"""
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(404, "视频不存在")
    video.folder_id = folder_id
    db.commit()
    return {"message": "移动成功"}
