# Remote File Preview

> 远程文件预览服务

将本地文件上传至远程服务器，借助服务器的预览能力生成 HTTPS 分享链接。

## 核心能力

本地文件 → SCP 上传 → HTTPS 预览链接

## 功能特性

- **时间戳命名**：`yyyy-MM-dd_HH-mm-ss_原始文件名`，可读性强
- **格式预览**：Markdown（代码高亮）、HTML、文本、图片等
- **过期清理**：支持设置 N 天后自动过期
- **服务检查**：上传前自动检查 SSH、Docker、NPM、预览服务状态
- **自动初始化**：检测到服务缺失时自动初始化
- **SSL 证书**：自动申请 Let's Encrypt 证书
- **上传限流**：每 IP 每分钟最多 10 次

## 支持格式

| 类型 | 格式 |
|------|------|
| 文档 | `.md` `.markdown` `.html` `.htm` |
| 代码 | `.py` `.sh` `.js` `.go` `.rs` `.c` `.cpp` `.java` `.json` `.yaml` `.xml` `.csv` |
| 文本 | `.txt` `.log` `.conf` |
| 图片 | `.png` `.jpg` `.gif` `.webp` `.svg` |
| 其他 | 文件下载 |

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/southxs/southxs-skills.git

# 2. 安装 Skill（复制到 OpenClaw skills 目录）
cp -r remote-file-preview ~/.openclaw/skills/

# 3. 配置环境变量（参考 SKILL.md）

# 4. 运行初始化（如服务器未部署）
python3 scripts/setup.py

# 5. 上传文件
python3 scripts/preview_host.py upload /本地路径/文件.txt
```

## 详细文档

- [SKILL.md](./SKILL.md) - 完整配置与使用说明
- [references/setup.md](./references/setup.md) - 服务器部署指南
