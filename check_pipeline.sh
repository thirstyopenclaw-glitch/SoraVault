#!/bin/bash
# Quick status check for the vision pipeline
cd "$(dirname "$0")"
source .venv/bin/activate

echo "=== Vision Pipeline Status ==="
if [ -f vision_pipeline.pid ] && kill -0 $(cat vision_pipeline.pid) 2>/dev/null; then
    echo "Status: RUNNING (PID $(cat vision_pipeline.pid))"
else
    echo "Status: STOPPED"
fi

python3 vision_pipeline.py --status

echo ""
echo "Last 5 log lines:"
tail -5 vision_output.log 2>/dev/null || echo "(no log yet)"

echo ""
echo "To stop:  kill $(cat vision_pipeline.pid 2>/dev/null || echo '?')"
echo "To resume: bash run_vision.sh"
