---
name: remote-file-preview
description: 远程文件预览服务。当用户说"预览"、"分享链接"、"上传到服务器"、"给我看看这个文件"、"分享这个"时触发。完整流程：检查服务状态 → 文件上传 → HTTPS预览链接生成。
---

# Remote File Preview

将本地文件上传至远程服务器，借助服务器的预览能力生成 HTTPS 分享链接。

## 触发关键词

"预览"、"分享链接"、"上传到服务器"、"给我看看这个"、"分享这个"、"上传项目"

## 架构

```
用户请求 → 检查服务状态 → SCP 上传 → SQLite 元数据
                                ↓
              预览服务(8081) ← NPM(SSL) ← DNS(A记录)
                                ↓
              https://预览域名.com/f/{random_name}
```

## 新增特性（v2）

| 特性 | 说明 |
|------|------|
| 🔑 随机文件名 | UUID 命名，防猜测，原文件名不暴露 |
| 🔒 密码保护 | 上传时指定密码，访问时需输入 |
| ⏰ 过期清理 | 支持设置 N 天后自动过期，服务器定时清理 |
| 📋 文件管理 | `/admin/list` 查看所有文件，`/admin/delete/{id}` 删除 |
| 🚫 上传限流 | 每 IP 每分钟最多 10 次上传 |

## 工作流程

### 流程 1：检查服务状态

上传前自动检查以下服务是否就绪：

| 服务 | 检查方式 | 缺失时行为 |
|------|---------|-----------|
| SSH 连接 | `ssh 连接测试` | 报告错误 |
| Docker | `docker ps` | 报告错误 |
| Nginx Proxy Manager | `HTTP 探测 :81` | 报告错误 |
| 预览服务 | `HTTP 探测 :8081` | 调用 setup 初始化 |
| DNS/SSL | `HTTPS 探测域名` | 调用 setup 初始化 |

**全部就绪** → 直接上传 → 返回预览链接
**有缺失** → 先初始化缺失部分 → 再上传

### 流程 2：文件上传

```bash
python3 scripts/preview_host.py upload /本地路径/文件.txt [子目录] [选项]
```

**新增选项：**

| 选项 | 说明 |
|------|------|
| `--password 密码` | 设置访问密码（可选） |
| `--expire-days 天数` | 设置过期天数（可选） |
| `--no-random-name` | 使用原始文件名（默认随机文件名） |

**示例：**

```bash
# 普通上传（随机文件名）
python3 scripts/preview_host.py upload /tmp/readme.md

# 带密码保护
python3 scripts/preview_host.py upload /tmp/readme.md --password 123456

# 7天后过期
python3 scripts/preview_host.py upload /tmp/readme.md --expire-days 7

# 密码+过期
python3 scripts/preview_host.py upload /tmp/readme.md --password 123456 --expire-days 30

# 使用原始文件名
python3 scripts/preview_host.py upload /tmp/readme.md --no-random-name
```

### 流程 3：管理操作

```bash
# 查看所有文件
python3 scripts/preview_host.py list

# 删除文件（通过 random_name）
python3 scripts/preview_host.py delete a1b2c3d4e5f6.md
```

### 流程 4：Web 管理面板

| 页面 | 地址 | 说明 |
|------|------|------|
| 文件列表 | `/admin/list` | 查看所有上传文件 |
| 删除文件 | `/admin/delete/{id}` | 删除指定文件 |
| 清理过期 | `/admin/cleanup` | 手动触发过期清理 |

## 支持的预览格式

| 格式 | 预览效果 |
|------|---------|
| `.md` `.markdown` | Markdown 渲染 + 代码高亮（highlight.js） |
| `.html` `.htm` | 直接渲染 |
| `.txt` `.log` `.conf` | 文本高亮 |
| `.py` `.sh` `.js` `.go` `.rs` `.c` `.cpp` `.java` | 代码高亮 |
| `.json` `.yaml` `.xml` `.csv` | 结构化文本高亮 |
| `.png` `.jpg` `.gif` `.webp` `.svg` | 图片预览 |
| 其他格式 | 文件下载 |

## 环境配置

在 `~/.openclaw/openclaw.json` 的 `skills.entries` 下添加：

```json
"remote-file-preview": {
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

### 必须配置

| 变量 | 说明 |
|------|------|
| `PREVIEW_HOST` | 服务器 IP |
| `PREVIEW_SSH_USER` | SSH 用户（建议 root） |
| `DNSPOD_DOMAIN` | 域名（如 `example.com`） |
| `DNSPOD_SUB_DOMAIN` | 二级域名（如 `preview`） |

### 可选配置

| 变量 | 说明 |
|------|------|
| `PREVIEW_SSH_KEY` | SSH 私钥，支持文件路径或原始私钥文本（以 `-----BEGIN` 开头） |
| `TENCENTCLOUD_SECRET_ID` | DNSPod API 密钥 ID |
| `TENCENTCLOUD_SECRET_KEY` | DNSPod API 密钥 Key |
| `PREVIEW_NPM_URL` | NPM 管理地址（默认 `http://IP:81`） |
| `PREVIEW_NPM_USER` | NPM 管理员邮箱 |
| `PREVIEW_NPM_PASS` | NPM 管理员密码 |

## 脚本说明

| 脚本 | 用途 |
|------|------|
| `scripts/setup.py` | 服务器初始化（首次使用） |
| `scripts/preview_host.py` | 文件上传与链接生成、管理操作 |
| `scripts/preview_server/app.py` | 预览服务源码（aiohttp） |
| `scripts/preview_server/Dockerfile` | 镜像构建文件 |
| `scripts/preview_server/docker-compose.yml` | 容器编排 |

## 详细文档

- [服务器部署指南](references/setup.md)
