#!/usr/bin/env bash
set -euo pipefail

# ─── 配置 ───
DASHBOARD_DIR="$HOME/AstrBot/dashboard"
VERSION_FILE="$HOME/AstrBot/data/dist/version"
BACKUP_DIR="$HOME/backups/ldmbot_dashboard"

# ─── 读取版本号 ───
VERSION="$(cat "$VERSION_FILE" 2>/dev/null || echo 'unknown')"
STAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_NAME="${VERSION}_${STAMP}.zip"
ARCHIVE_PATH="${BACKUP_DIR}/${ARCHIVE_NAME}"

# ─── 创建备份目录 ───
mkdir -p "$BACKUP_DIR"

echo "版本: $VERSION"
echo "日期: $STAMP"
echo "输出: $ARCHIVE_PATH"
echo

# ─── 打包（排除可再生目录/文件）───
cd "$DASHBOARD_DIR"
zip -r -q "$ARCHIVE_PATH" . \
    -x 'node_modules/*' \
    -x 'dist/*' \
    -x 'data/*' \
    -x 'sorted_files.txt' \
    -x '.DS_Store'

# ─── 结果 ───
SIZE="$(du -h "$ARCHIVE_PATH" | cut -f1)"
COUNT="$(unzip -l "$ARCHIVE_PATH" | tail -1 | awk '{print $2}')"

echo "备份完成"
echo "  文件: $ARCHIVE_PATH"
echo "  大小: $SIZE"
echo "  条目: $COUNT 个文件"
