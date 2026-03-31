---
name: remote-file-preview
description: 远程文件预览服务。当用户说"预览"、"分享链接"、"上传到服务器"、"给我看看这个文件"、"分享这个"时触发。完整流程：服务器初始化（目录/NPM/预览服务）→ 文件上传 → HTTPS预览链接生成。
---

# Preview Host

文件预览分享服务，支持 Markdown（代码高亮）、HTML、文本、图片等格式的预览与下载。

## 触发条件

用户请求上传或预览任意文件/目录时触发：
- "预览 xxx 文件"
- "上传到服务器"
- "给我看看这个"
- "分享链接"
- "上传项目"

## 架构

```
用户请求 → 上传文件 → /software/southxs-preview/files/
              ↓
         预览服务(8081) ← NPM(SSL证书) ← DNS(A记录)
              ↓
         https://preview.你的域名.com/文件路径
```

## 前置要求

### 1. 安装依赖

```bash
# 本地
apt install rsync openssh-client docker.io docker-compose

# Python (Linux/macOS 默认有)
```

### 2. 配置环境变量

在 `~/.openclaw/openclaw.json` 的 `skills.entries` 下添加：

```json
"preview-host": {
  "enabled": true,
  "env": {
    "PREVIEW_HOST": "服务器IP",
    "PREVIEW_SSH_USER": "root",
    "PREVIEW_SSH_KEY": "/path/to/id_rsa",
    "DNSPOD_DOMAIN": "你的域名.com",
    "DNSPOD_SUB_DOMAIN": "preview",
    "TENCENTCLOUD_SECRET_ID": "腾讯云SecretId",
    "TENCENTCLOUD_SECRET_KEY": "腾讯云SecretKey",
    "PREVIEW_NPM_URL": "http://服务器IP:81",
    "PREVIEW_NPM_USER": "admin@你的域名.com",
    "PREVIEW_NPM_PASS": "你的NPM密码"
  }
}
```

**必须的环境变量：**
- `PREVIEW_HOST` - 服务器 IP
- `PREVIEW_SSH_USER` - SSH 用户（建议 root）
- `DNSPOD_DOMAIN` - 域名（如 `example.com`）
- `DNSPOD_SUB_DOMAIN` - 二级域名（如 `preview`，完整域名为 `preview.example.com`）

**可选的环境变量：**
- `PREVIEW_SSH_KEY` - SSH 私钥路径（默认 `~/.ssh/id_rsa`）
- `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` - DNSPod API 密钥（用于自动 DNS 配置）
- `PREVIEW_NPM_URL` / `PREVIEW_NPM_USER` / `PREVIEW_NPM_PASS` - NPM 管理凭证（用于自动代理配置）

## 工作流程

### 阶段1：服务器初始化（首次使用）

执行 `scripts/setup.py`，自动完成：
1. SSH 连接测试
2. DNS 解析检查与配置（腾讯云 DNSPod API）
3. 创建目录结构 `/software/southxs-preview/`
4. Nginx Proxy Manager 部署（如未安装）
5. 预览服务（Python aiohttp）部署
6. NPM 代理 + Let's Encrypt SSL 证书配置

```bash
python3 scripts/setup.py
```

### 阶段2：文件上传与预览

#### 快速预览

```
用户：预览一下这个文件
西洲：scp上传 → 返回预览链接
```

#### 上传单个文件

```bash
python3 scripts/preview_host.py upload /本地路径/文件.txt [子目录]
```

#### 上传整个目录

```bash
python3 scripts/preview_host.py upload /本地路径/目录/ [远程子目录]
```

## 支持的预览格式

| 格式 | 预览方式 |
|------|---------|
| `.md` `.markdown` | Markdown 渲染 + 代码高亮 |
| `.html` `.htm` | 直接渲染 |
| `.txt` `.log` `.conf` | 文本高亮 |
| `.py` `.sh` `.js` `.go` `.rs` | 代码高亮 |
| `.json` `.yaml` `.xml` `.csv` | 结构化文本高亮 |
| `.png` `.jpg` `.gif` `.webp` `.svg` | 图片预览 |
| 其他 | 文件下载 |

## 脚本说明

| 脚本 | 用途 |
|------|------|
| `scripts/setup.py` | 服务器初始化（首次运行） |
| `scripts/preview_host.py` | 文件上传与链接生成 |
| `scripts/preview_server/app.py` | 预览服务源码 |
| `scripts/preview_server/Dockerfile` | 预览服务镜像构建文件 |
| `scripts/preview_server/docker-compose.yml` | NPM + 预览服务编排 |

## 详细文档

- [服务器部署指南](references/setup.md)
