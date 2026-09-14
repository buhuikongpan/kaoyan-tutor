# -*- coding: utf-8 -*-
"""路径修复工具（通用重定位）：把库里的路径规范化成「相对项目根」，并找回被搬走的文件。

背景：videos / chat_messages 表早期存的是**绝对路径**，项目一改名、一搬家、一换盘，
记录就集体失效（视频播不了、字幕读不到、总结读不出来）——而文件往往一个都没丢。
本项目现在的约定是「库里只存相对项目根的路径」（见 backend/app/core/paths.py）。

本工具做三件事：
  1. 绝对路径且文件还在        → 改成相对路径（存成 storage/videos/math/x.mp4）
  2. 记录失效但文件还在别处    → 按**文件名**在 storage/ 下找回，改成相对路径
  3. 彻底找不到的              → **保留原值**并列出清单（人工确认后再处理，不擅自清空）

历史名字叫 migrate_paths_after_rename.py（当年只为"项目改名"而写），现在搬家 / 换盘 /
云端来回同步都能用。注意：**后端每次启动时会自动做同样的事**（core/path_migration.py），
所以正常情况下不需要手动跑；只有在服务起不来、或想先看一份体检报告时才用它。

用法：
    python scripts/migrate_paths_after_rename.py            # 预演，只看不改
    python scripts/migrate_paths_after_rename.py --apply    # 备份数据库后写库
"""
import datetime
import io
import os
import shutil
import sqlite3
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gb"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "kaoyan.db")
COLS = ("file_path", "subtitle_path", "summary_path", "compressed_path")

# 复用后端同一套解析逻辑，保证"脚本修好的"和"服务读到的"完全一致
sys.path.insert(0, os.path.join(ROOT, "backend"))
try:
    from app.core.paths import resolve, to_rel
    from app.core.path_migration import _fix_chat_text
except Exception as e:  # 依赖缺失/代码半坏时给出明确提示，而不是抛栈
    print("[X] 无法导入后端路径模块（backend/app/core/paths.py）：%s" % e)
    print("    请确认已 pip install -r backend/requirements.txt，且在项目根目录运行。")
    raise SystemExit(1)


def fix_value(raw):
    """→ (新值, 状态)：'' 无需改动 / normalized / relocated / missing"""
    if raw is None:
        return "", ""
    old = str(raw)
    if not old.strip():
        return "", ""
    hit = resolve(old)
    if not hit:
        return old, "missing"
    new = to_rel(hit)
    if new == old:
        return old, ""
    # 记录里的路径本身还能打开 → 只是写法要归一；否则是"失效后按文件名找回"
    try:
        direct_ok = os.path.isfile(old)
    except OSError:
        direct_ok = False
    return new, ("normalized" if direct_ok else "relocated")


def main():
    apply = "--apply" in sys.argv
    if not os.path.exists(DB):
        print("[X] 找不到数据库：%s" % DB)
        return 1

    con = sqlite3.connect(DB, timeout=20)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    cur = con.cursor()

    print("项目根：%s" % ROOT)
    print("数据库：%s" % DB)
    print("模式：%s" % ("写库（会先备份）" if apply else "预演（只读，不改库）"))
    print()

    updates = []   # (table, col, new, id)
    missing = []   # (id, col, 原值)
    stat = {"normalized": 0, "relocated": 0}

    for vid, *vals in cur.execute(
            "SELECT id,%s FROM videos" % ",".join(COLS)).fetchall():
        for col, raw in zip(COLS, vals):
            if not raw:
                continue
            new, state = fix_value(raw)
            if state in stat:
                stat[state] += 1
                updates.append(("videos", col, new, vid))
            elif state == "missing":
                missing.append((vid, col, raw))

    chat_updates = []
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "chat_messages" in tables:
        for mid, content, tool_calls in cur.execute(
                "SELECT id,content,tool_calls FROM chat_messages").fetchall():
            for col, raw in (("content", content), ("tool_calls", tool_calls)):
                if not raw or "storage" not in raw.replace("\\", "/"):
                    continue
                new, n = _fix_chat_text(raw)
                if n:
                    chat_updates.append(("chat_messages", col, new, mid))

    print("== 体检结果 ==")
    print("  视频/字幕/总结：绝对转相对 %d 处，失效重定位 %d 处"
          % (stat["normalized"], stat["relocated"]))
    print("  聊天图片路径：  %d 处" % len(chat_updates))
    print("  找不到的文件：  %d 处" % len(missing))
    for vid, col, raw in missing[:20]:
        print("     [缺] 视频 #%s 的 %s：%s" % (vid, col, raw))
    if len(missing) > 20:
        print("     ...（其余 %d 处省略）" % (len(missing) - 20))
    print()

    if not apply:
        if updates or chat_updates:
            print("[i] 预演结束：有 %d 处可修复，加 --apply 执行。"
                  % (len(updates) + len(chat_updates)))
        else:
            print("[i] 预演结束：路径都是规范的，无需修复。")
        con.close()
        return 0

    if not updates and not chat_updates:
        print("[i] 无需修复，未改动数据库。")
        con.close()
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB + ".bak_before_path_repair_" + stamp
    shutil.copy2(DB, bak)
    print("[ok] 已备份数据库：%s" % os.path.basename(bak))

    for table, col, new, rid in updates + chat_updates:
        cur.execute("UPDATE %s SET %s=? WHERE id=?" % (table, col), (new, rid))
    con.commit()
    print("[done] 已修复 %d 处路径（找不到的 %d 处保持原样）"
          % (len(updates) + len(chat_updates), len(missing)))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
