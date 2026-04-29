# 📁 File Preview Service

一个简洁的文件预览服务，支持文本、代码、Markdown、图片和 PDF 文件的在线预览。

## 功能特性

- 📂 **目录浏览** - 支持多目录访问，树形结构导航
- 📝 **文本预览** - 支持代码高亮，自动分页加载大文件
- 📋 **Markdown 渲染** - 支持表格、代码块、目录等扩展
- 🖼️ **图片预览** - 支持 JPG、PNG、GIF、WebP、SVG 等格式
- 📄 **PDF 预览** - 内嵌 PDF 渲染器
- 🌙 **双主题** - 支持明暗主题切换
- 📱 **响应式** - 适配桌面端和移动端
- 🔐 **安全鉴权** - HMAC Token + httpOnly Cookie + 登录频率限制

## 技术栈

- Python 3
- Flask
- PyMarkdown
- 纯前端 HTML/CSS/JavaScript

## 快速开始

### 安装依赖

```bash
cd file-preview
pip install flask markdown
```

### 配置环境变量

```bash
# 必须设置
export AUTH_USERNAME="your_username"
export AUTH_PASSWORD="your_password"

# 可选
export ALLOWED_DIRS="/root,/software"  # 默认允许目录
export AUTH_ENABLED=true               # 默认开启鉴权
```

### 启动服务

```bash
bash start.sh
```

服务将在 `http://0.0.0.0:8881` 启动

### Docker 部署

```bash
docker run -d -p 8881:8881 \
  -e AUTH_USERNAME="your_username" \
  -e AUTH_PASSWORD="your_password" \
  -v /your/path:/root \
  --name file-preview \
  your-image
```

## 安全特性

- **凭据环境变量化** — 用户名密码不硬编码，通过环境变量注入
- **路径遍历防护** — 使用 `os.path.commonpath` 精确校验，防止目录穿越
- **httpOnly Cookie** — Token 不暴露给 JavaScript，防范 XSS 窃取
- **登录频率限制** — 同一 IP 5分钟内最多5次尝试，防止暴力破解
- **Token 签名验证** — HMAC-SHA256 签名，防篡改

## 目录配置

默认允许访问 `/root` 和 `/software` 目录，可通过 `ALLOWED_DIRS` 环境变量修改。

## 开源协议

本项目采用 MIT License 开源。

## 作者

**southxs**  
- GitHub: [github.com/southxs](https://github.com/southxs)
