#!/bin/bash
# Sora Library → Google Drive backup via rclone
# Setup: brew install rclone && rclone config (add "gdrive" remote)

REMOTE="gdrive:SoraBackup"
LOCAL="$HOME/Desktop/SoraBackup"

echo "=== Sora Google Drive Backup ==="
echo "Local:  $LOCAL"
echo "Remote: $REMOTE"
echo ""

# Check rclone is installed
if ! command -v rclone &> /dev/null; then
    echo "rclone not installed. Run: brew install rclone"
    exit 1
fi

# Check remote is configured
if ! rclone listremotes | grep -q "gdrive:"; then
    echo "Google Drive remote not configured. Run: rclone config"
    echo "  - Choose 'New remote', name it 'gdrive', type 'drive'"
    exit 1
fi

echo "Syncing videos and prompts..."
rclone sync "$LOCAL" "$REMOTE" \
    --progress \
    --transfers 4 \
    --checkers 8 \
    --exclude ".DS_Store" \
    --exclude "__pycache__/**" \
    --exclude "*.pyc" \
    --exclude "SoraWatermarkCleaner/**" \
    --log-level INFO

echo ""
echo "Backup complete."
rclone size "$REMOTE"
