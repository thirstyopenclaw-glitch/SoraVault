#!/bin/bash
# Batch watermark removal for all Sora videos
# Uses SoraWatermarkCleaner — run from its directory
#
# Usage: bash batch_watermark.sh
# Ctrl+C to stop — already-processed files are saved

CLEANER="$HOME/Desktop/SoraBackup/SoraWatermarkCleaner"
OUTPUT="$HOME/Desktop/SoraBackup/cleaned"

mkdir -p "$OUTPUT/v2_feed" "$OUTPUT/v2_draft"

echo "=== Sora Watermark Batch Removal ==="
echo "Output: $OUTPUT"
echo ""

# Check if cleaner has a venv
if [ ! -d "$CLEANER/.venv" ]; then
    echo "Setting up SoraWatermarkCleaner venv..."
    cd "$CLEANER" && python3 -m venv .venv && source .venv/bin/activate
    pip install -e . 2>&1 | tail -3
else
    cd "$CLEANER" && source .venv/bin/activate
fi

echo ""
echo "Processing v2_feed videos..."
python3 cli.py -i "$HOME/Desktop/SoraBackup/v2_feed/videos" -o "$OUTPUT/v2_feed" --quiet

echo ""
echo "Processing v2_draft videos..."
python3 cli.py -i "$HOME/Desktop/SoraBackup/v2_draft/videos" -o "$OUTPUT/v2_draft" --quiet

echo ""
echo "Done. Cleaned videos in: $OUTPUT"
ls "$OUTPUT/v2_feed" | wc -l | xargs echo "v2_feed cleaned:"
ls "$OUTPUT/v2_draft" | wc -l | xargs echo "v2_draft cleaned:"
