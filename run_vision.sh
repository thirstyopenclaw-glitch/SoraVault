#!/bin/bash
# Run the vision analysis pipeline overnight
# Ctrl+C once = graceful stop (saves progress)
# Ctrl+C twice = force quit
# Resume by running again — picks up where it left off
#
# For background/overnight use:
#   nohup bash run_vision.sh > vision_output.log 2>&1 & disown
#
# For interactive (foreground) use:
#   bash run_vision.sh

cd "$(dirname "$0")"
source .venv/bin/activate
set -a; source .env; set +a

echo "=== Sora Vision Pipeline ==="
echo "Press Ctrl+C to pause (progress saves automatically)"
echo ""

# Check status first
python3 vision_pipeline.py --status
echo ""
echo "Starting in 3 seconds..."
sleep 3

# Run — processes all videos, highest engagement first
python3 vision_pipeline.py --min-likes 0

echo ""
echo "Pipeline paused/complete. Run again to resume."
python3 vision_pipeline.py --status
