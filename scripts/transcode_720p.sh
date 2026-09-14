#!/bin/bash
# 批量转码：storage/videos -> storage/videos_720p
# 参数：720p@15fps @ CRF27 @ maxrate500k，音频 aac 96k，faststart
# 带断点续跑：输出文件已存在且非空则跳过
set -u
cd "$(dirname "$0")/.." || exit 1

SRC_ROOT="storage/videos"
OUT_ROOT="storage/videos_720p"
LOG="storage/sample/transcode.log"

mkdir -p "$OUT_ROOT"
: > "$LOG"

count=0; fail=0; skip=0
while IFS= read -r -d '' f; do
  rel="${f#"$SRC_ROOT"/}"
  out="$OUT_ROOT/$rel"
  mkdir -p "$(dirname "$out")"

  if [ -s "$out" ]; then
    echo "[跳过] $rel" | tee -a "$LOG"
    skip=$((skip+1)); continue
  fi

  count=$((count+1))
  echo "[$count] $rel" | tee -a "$LOG"
  if ffmpeg -y -v error -i "$f" \
      -vf "fps=15" \
      -c:v libx264 -crf 27 -preset medium -maxrate 500k -bufsize 1000k \
      -c:a aac -b:a 96k -movflags +faststart \
      "$out" 2>>"$LOG"; then
    echo "    OK $(du -h "$out" | cut -f1) / $(du -h "$f" | cut -f1)" | tee -a "$LOG"
  else
    echo "    FAIL $rel" | tee -a "$LOG"
    fail=$((fail+1))
  fi
done < <(find "$SRC_ROOT" -name "*.mp4" -print0)

echo "==================================" | tee -a "$LOG"
echo "本次处理 $count 个（跳过 $skip / 失败 $fail）" | tee -a "$LOG"
echo "输出总大小: $(du -sh "$OUT_ROOT" 2>/dev/null | cut -f1)" | tee -a "$LOG"