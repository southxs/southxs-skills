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

### 启动服务

```bash
bash start.sh
```

服务将在 `http://0.0.0.0:8881` 启动

### Docker 部署

```bash
docker run -d -p 8881:8881 -v /your/path:/root --name file-preview your-image
```

## 目录配置

默认允许访问 `/root` 和 `/software` 目录，可在 `file_preview.py` 中修改 `ALLOWED_DIRS` 配置。

## 开源协议

本项目采用 [MIT License](LICENSE) 开源。

## 作者

**southxs**  
- GitHub: [github.com/southxs](https://github.com/southxs)

## 捐赠

如果您觉得这个项目对您有帮助，可以请作者喝一杯咖啡 ☕

---

*Made with ❤️ on southxs server*