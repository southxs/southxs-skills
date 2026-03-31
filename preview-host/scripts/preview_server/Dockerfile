FROM python:3.12-slim

WORKDIR /app

# 使用清华镜像加速 pip
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir aiohttp markdown

COPY app.py /app/

EXPOSE 8081

ENV PREVIEW_FILES_DIR=/data/files
ENV PREVIEW_PORT=8081

CMD ["python", "app.py"]
