# -*- coding: utf-8 -*-
"""路径统一层：库里只存「相对项目根」的路径，读写时在这里翻译。

为什么要有这一层
----------------
videos 表早期存的是**绝对路径**。项目一旦改名或搬家（例如
``Desktop\\DIFY考研学习平台\\本地文件`` → ``Desktop\\项目\\考研学习平台\\本地文件``），
库里的路径会**集体失效**：视频播不了、字幕读不到、总结读不出来——而文件其实一个都没丢，
只是"门牌号"过期了。换盘符、Windows→Linux 也会踩同一个坑。

现在的约定
----------
::

    库里存    storage/videos/math/20260803_180400_xxx.mp4   （相对项目根，POSIX 分隔符）
    读文件    to_abs("storage/videos/...")  →  当前项目根下的真实路径
    搬家后    什么都不用改：项目根变了，相对路径自动跟着变
    老数据    绝对路径读取时原样兼容；启动时由 core/path_migration.py 统一规范化

对外接口
--------
- ``to_rel(p)``   任意路径 → 相对项目根的 POSIX 字符串（落库用）
- ``to_abs(p)``   存库值 → 绝对 Path（读文件用），空值返回 None
- ``exists(p)``   存库值指向的文件是否存在
- ``resolve(p)``  先按 to_abs 找；找不到再按**文件名**在 storage/ 下重定位（搬家兜底）
- ``relocate(p)`` resolve 的"落库"版本：找到就返回相对路径，找不到返回空串
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from .config import BASE_DIR

P = Union[str, Path, None]

# 文件名兜底搜索的范围（相对 storage/），顺序即优先级
_SEARCH_KINDS = ("videos", "videos_480p", "videos_720p", "uploads", "subtitles", "summaries")


def _norm(p: P) -> str:
    """统一成 POSIX 风格的字符串（Windows 反斜杠 → 正斜杠）"""
    if p is None:
        return ""
    return str(p).strip().replace("\\", "/")


def to_rel(p: P) -> str:
    """任意路径 → 相对项目根的 POSIX 路径（落库用）。

    - 项目内的绝对路径：``C:\\...\\本地文件\\storage\\videos\\a.mp4`` → ``storage/videos/a.mp4``
    - 已经是相对路径：只做分隔符归一
    - 项目外的绝对路径：原样保留（POSIX 化），不丢信息
    """
    s = _norm(p)
    if not s:
        return ""
    path = Path(s)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(BASE_DIR.resolve()).as_posix()
        except Exception:
            return s
    return s


def to_abs(p: P) -> Optional[Path]:
    """存库值 → 绝对路径（读文件用）。空值返回 None，调用方必须先判空。"""
    s = _norm(p)
    if not s:
        return None
    path = Path(s)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def exists(p: P) -> bool:
    """存库值指向的文件是否存在（相对/绝对都认）"""
    a = to_abs(p)
    if a is None:
        return False
    try:
        return a.is_file()
    except OSError:
        return False


def resolve(p: P) -> Optional[Path]:
    """定位文件：先按记录路径找，找不到再按文件名在 storage/ 下兜底重定位。

    兜底是为了对付"文件被挪到别处"（例如项目搬家后仍残留旧记录）：
    只要文件名还在 storage/ 里，就能找回来，不必人工改库。
    """
    a = to_abs(p)
    if a is not None:
        try:
            if a.is_file():
                return a
        except OSError:
            pass

    name = Path(_norm(p)).name
    if not name:
        return None
    storage = BASE_DIR / "storage"
    if not storage.is_dir():
        return None
    for kind in _SEARCH_KINDS:
        base = storage / kind
        if not base.is_dir():
            continue
        try:
            for hit in base.rglob(name):
                if hit.is_file():
                    return hit
        except OSError:
            continue
    return None


def relocate(p: P) -> str:
    """resolve 的落库版本：找到返回相对路径（'' 表示没找到）"""
    hit = resolve(p)
    return to_rel(hit) if hit else ""
