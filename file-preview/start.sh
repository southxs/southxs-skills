#!/bin/bash
# 启动文件预览服务
# 使用前请设置环境变量：
#   export AUTH_USERNAME="your_username"
#   export AUTH_PASSWORD="your_password"
#   export ALLOWED_DIRS="/root,/software"  # 可选，默认 /root,/software

cd "$(dirname "$0")"
pkill -f "file_preview.py" 2>/dev/null || true
sleep 1

if [ -z "$AUTH_USERNAME" ] || [ -z "$AUTH_PASSWORD" ]; then
    echo "⚠️  警告: 未设置 AUTH_USERNAME / AUTH_PASSWORD，鉴权将禁用"
    export AUTH_ENABLED=false
fi

nohup python3 file_preview.py >> /var/log/file-preview.log 2>&1 &
echo "Started PID: $!"
