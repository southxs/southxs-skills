# southxs-skills

Personal agent skills repository for [southxs](https://github.com/southxs).

## Skills

### [remote-file-preview](./remote-file-preview/) 🌐
> 远程文件预览服务

将本地文件上传至远程服务器，借助服务器的预览能力（Markdown 渲染、代码高亮、图片预览等）生成 HTTPS 分享链接。

**核心能力：** 本地文件 → SCP 上传 → HTTPS 预览链接

---

## 仓库结构

```
southxs-skills/
├── README.md
├── {skill-name}/
│   ├── SKILL.md           # Skill 定义（触发条件、流程、配置）
│   ├── scripts/           # 可执行脚本
│   └── references/        # 详细文档
└── .gitignore
```

## 维护指南

### 本地开发

```bash
# 克隆仓库
git clone https://github.com/southxs/southxs-skills.git

# 开发后推送
git add .
git commit -m "描述"
git push
```

### Skill 安装到 Agent

OpenClaw 会从 `~/.openclaw/skills/` 目录加载 Skill。将仓库中的 Skill 复制到该目录即可启用。

---

## 作者

[southxs](https://github.com/southxs)
