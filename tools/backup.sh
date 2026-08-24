#!/usr/bin/env bash
# tools/backup.sh — One-click backup tool for Helga database & user data.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${ROOT_DIR}/data"
BACKUP_DIR="${ROOT_DIR}/data/backups"

TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
OUT_FILE="${BACKUP_DIR}/helga_backup_${TIMESTAMP}.tar.gz"

mkdir -p "${BACKUP_DIR}"

echo "=== HELGA DATABASE & USER PROGRESS BACKUP ==="
echo "Backing up data directory: ${DATA_DIR}"
echo "Target archive: ${OUT_FILE}"

# Execute SQLite backup lock-safe copy if database exists
if [ -f "${DATA_DIR}/helga.db" ]; then
    echo "[1/2] Creating consistent SQLite database backup snapshot..."
    sqlite3 "${DATA_DIR}/helga.db" ".backup '${DATA_DIR}/helga_snapshot.db'"
fi

# Package database, snapshot, and uploaded material metadata
echo "[2/2] Archiving data directory..."
tar -czf "${OUT_FILE}" \
    -C "${ROOT_DIR}" \
    --exclude="data/backups" \
    --exclude="data/hf_cache" \
    --exclude="data/tts_cache" \
    data/helga.db \
    data/helga_snapshot.db \
    data/courses \
    data/uploads 2>/dev/null || true

# Cleanup temporary snapshot
rm -f "${DATA_DIR}/helga_snapshot.db"

echo "=== BACKUP COMPLETE ==="
echo "Saved: ${OUT_FILE} ($(du -h "${OUT_FILE}" | cut -f1))"
