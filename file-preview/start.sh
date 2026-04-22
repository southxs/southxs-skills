#!/bin/bash
cd /software/file-preview
pkill -f "file_preview.py" 2>/dev/null || true
sleep 1
nohup python3 file_preview.py >> /var/log/file-preview.log 2>&1 &
echo "Started PID: $!"