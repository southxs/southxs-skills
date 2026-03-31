# Preview Host - 服务器部署指南

## 目录结构

```
/software/southxs-preview/
├── app/
│   ├── npm/                  # Nginx Proxy Manager
│   │   ├── docker-compose.yml
│   │   ├── data/             # NPM 数据持久化
│   │   └── letsencrypt/      # SSL 证书
│   └── preview/              # 预览服务
│       ├── app.py
│       ├── Dockerfile
│       ├── docker-compose.yml
│       └── files/            # 文件存储（软链接到 ../files）
├── files/                    # 文件存储根目录
└── scripts/                  # 工具脚本
```

## 环境要求

- Ubuntu 20.04+ / Debian 11+
- Docker + Docker Compose
- Python 3.11+
- SSH 无密码登录（密钥方式）
- 域名已备案（如使用国内服务器）

## 快速开始

### 1. 配置环境变量

```bash
export PREVIEW_HOST="你的服务器IP"
export PREVIEW_SSH_USER="root"
export PREVIEW_SSH_KEY="/path/to/your/private/key"
export DNSPOD_DOMAIN="yourdomain.com"
export DNSPOD_SUB_DOMAIN="preview"
export TENCENTCLOUD_SECRET_ID="你的SecretId"
export TENCENTCLOUD_SECRET_KEY="你的SecretKey"
export PREVIEW_NPM_URL="http://服务器IP:81"
export PREVIEW_NPM_USER="admin@yourdomain.com"
export PREVIEW_NPM_PASS="你的NPM密码"
```

### 2. 运行初始化脚本

```bash
python3 scripts/setup.py
```

脚本会自动完成：
- ✅ SSH 连接测试
- ✅ DNS 记录配置（腾讯云 DNSPod）
- ✅ 目录结构创建
- ✅ Nginx Proxy Manager 部署
- ✅ 预览服务部署
- ✅ NPM 代理 + SSL 证书配置

### 3. 上传文件

```bash
# 单文件
python3 scripts/preview_host.py upload /path/to/file.txt

# 目录
python3 scripts/preview_host.py upload /path/to/dir/

# 指定子目录
python3 scripts/preview_host.py upload /path/to/file.txt projects/
```

## 手动部署（可选）

如果自动部署失败，可以手动按以下步骤操作：

### 1. 创建目录

```bash
ssh root@服务器IP
mkdir -p /software/southxs-preview/{app/{npm,preview},files}
```

### 2. 部署 Nginx Proxy Manager

```bash
# /software/southxs-preview/app/npm/docker-compose.yml
version: "3.8"
services:
  npm:
    image: jc21/nginx-proxy-manager:latest
    container_name: npm
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./data:/data
      - ./letsencrypt:/etc/letsencrypt
    environment:
      - TZ=Asia/Shanghai
```

```bash
cd /software/southxs-preview/app/npm
docker-compose up -d
```

默认 NPM 登录信息：`admin@example.com` / `changeme`（首次登录后强制修改）

### 3. 部署预览服务

```bash
# /software/southxs-preview/app/preview/Dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir aiohttp markdown pygments
COPY app.py /app/
EXPOSE 8081
ENV PREVIEW_FILES_DIR=/data/files
CMD ["python3", "app.py"]
```

```bash
cd /software/southxs-preview/app/preview
docker build -t southxs-preview .
docker run -d \
  --name southxs-preview \
  --restart unless-stopped \
  -p 127.0.0.1:8081:8081 \
  -v /software/southxs-preview/files:/data/files \
  southxs-preview
```

### 4. NPM 配置代理

1. 访问 `http://服务器IP:81`
2. 登录 NPM 管理面板
3. 添加代理主机：
   - Domain Names: `preview.yourdomain.com`
   - Forward Hostname/IP: `127.0.0.1`
   - Forward Port: `8081`
4. SSL Certificate → Let's Encrypt → 申请证书
5. 保存

### 5. 配置 DNS

在腾讯云 DNSPod 控制台添加：

| 主机记录 | 记录类型 | 记录值 |
|---------|---------|--------|
| preview | A | 服务器IP |

## 安全建议

1. **SSH** - 禁用密码登录，仅允许密钥
2. **NPM** - 首次登录后立即修改默认密码
3. **防火墙** - 仅开放 80/443 和 SSH 端口
4. **腾讯云 AK/SK** - 权限最小化，仅开放 DNSPod 相关权限
5. **定期备份** - NPM 数据目录定期备份

## 故障排查

### NPM 无法启动

```bash
docker logs npm
docker-compose logs npm
```

### 预览服务无法访问

```bash
curl http://127.0.0.1:8081
docker logs southxs-preview
```

### SSL 证书申请失败

- 确认域名已解析到服务器 IP
- 确认 80/443 端口已开放
- 检查 DNS 生效时间（通常 5-30 分钟）
