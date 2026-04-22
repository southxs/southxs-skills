#!/usr/bin/env python3
"""
文件预览服务 - 支持文本/代码/Markdown/图片/PDF
"""

import os
import re
import markdown
from flask import Flask, send_file, abort, render_template_string, Response, request, jsonify
from functools import wraps

app = Flask(__name__)

# ============ 配置 ============
HOST = "0.0.0.0"
PORT = 8881
ALLOWED_DIRS = ['/root', '/software']  # 只允许访问这两个目录

# ============ 安全检查 ============
def safe_path(path):
    """防止目录遍历，只允许访问 ALLOWED_DIRS"""
    # 先 realpath 解析
    real = os.path.realpath('/' + path)
    # 检查是否在允许目录内
    allowed = any(real.startswith(d) for d in ALLOWED_DIRS)
    if not allowed:
        abort(403)
    return real

def allowed_file(filename):
    """允许的文件类型"""
    return True  # 放开限制，用后端判断

# ============ 页面模板 ============
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>📁 文件预览</title>
    <style>
        /* 默认白色主题 */
        :root {
            --primary: #e94560;
            --primary-light: #ff6b6b;
            --bg: #f5f7fa;
            --sidebar-bg: #ffffff;
            --text: #333333;
            --text-muted: #888888;
            --border: #e0e0e0;
            --hover: #fff0f0;
            --code-bg: #f0f0f0;
        }
        
        /* 深色主题 */
        [data-theme="dark"] {
            --primary: #ff6b6b;
            --primary-light: #e94560;
            --bg: #1a1a2e;
            --sidebar-bg: #16213e;
            --text: #eeeeee;
            --text-muted: #888888;
            --border: #2a2a4a;
            --hover: #1f2b4a;
            --code-bg: #0f0f1a;
        }
        
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
        
        /* 主题切换按钮 */
        .theme-btn {
            background: none; border: 1px solid var(--border);
            cursor: pointer; font-size: 0.9rem;
            padding: 4px 8px; border-radius: 4px;
            color: var(--text);
        }
        .theme-btn:hover { background: var(--hover); }
        
        /* 侧边栏 */
        .sidebar {
            position: fixed; left: 0; top: 0; bottom: 0;
            width: 260px; background: var(--sidebar-bg);
            border-right: 1px solid var(--border);
            display: flex; flex-direction: column;
            transition: transform 0.3s ease;
            z-index: 100;
        }
        .sidebar.collapsed { transform: translateX(-260px); }
        .sidebar-header {
            padding: 16px; border-bottom: 1px solid var(--border);
            display: flex; align-items: center; gap: 10px;
        }
        .sidebar-header h2 { font-size: 1rem; color: var(--primary); flex: 1; display: none; }
        .sidebar-header .toggle-btn {
            display: none;
        }
        .toggle-btn {
            background: none; border: none; cursor: pointer;
            font-size: 1.2rem; color: var(--text-muted);
            padding: 4px 8px; border-radius: 4px;
        }
        .toggle-btn:hover { background: var(--hover); }
        .sidebar-content { flex: 1; overflow-y: auto; padding: 8px 0; }
        .file-list { list-style: none; }
        .file-item {
            padding: 8px 16px; display: flex; align-items: center;
            gap: 8px; cursor: pointer; transition: background 0.15s;
            text-decoration: none; color: var(--text);
        }
        .file-item:hover { background: var(--hover); }
        .file-item a { color: inherit; text-decoration: none; display: flex; align-items: center; gap: 8px; flex: 1; }
        .file-item .icon { font-size: 1rem; }
        .file-item .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.9rem; }
        .file-item .size { color: var(--text-muted); font-size: 0.75rem; }
        .file-item .meta { display: flex; align-items: center; gap: 12px; margin-left: auto; }
        .file-item .time { color: var(--text-muted); font-size: 0.7rem; white-space: nowrap; }
        .file-item .time-item { display: flex; align-items: center; gap: 2px; }
        .file-item .time-icon { font-size: 0.65rem; opacity: 0.7; }
        .file-item.folder .name { color: var(--primary); font-weight: 500; }
        
        /* 展开收起按钮 */
        .expand-btn {
            background: none; border: none; cursor: pointer;
            padding: 2px 6px; font-size: 0.8rem; color: var(--text-muted);
            transition: transform 0.2s;
        }
        .expand-btn.expanded { transform: rotate(90deg); }
        
        /* 主内容区 */
        .main {
            margin-left: 260px; padding: 24px;
            transition: margin-left 0.3s ease;
        }
        .main.expanded { margin-left: 0; }
        
        /* 首页 */
        .home-hero {
            text-align: center;
            padding: 40px 20px;
            margin-bottom: 30px;
        }
        .home-hero h1 {
            font-size: 2rem;
            color: var(--primary);
            margin-bottom: 12px;
        }
        .home-hero p {
            color: var(--text-muted);
            font-size: 1.1rem;
        }
        .home-cards {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            max-width: 800px;
            margin: 0 auto;
        }
        .home-card {
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 24px;
            background: var(--sidebar-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            text-decoration: none;
            color: var(--text);
            transition: all 0.2s;
        }
        .home-card:hover {
            border-color: var(--primary);
            box-shadow: 0 4px 20px rgba(233, 69, 96, 0.1);
            transform: translateY(-2px);
        }
        .home-card-icon {
            font-size: 2.5rem;
        }
        .home-card-info {
            flex: 1;
        }
        .home-card-name {
            font-size: 1.3rem;
            font-weight: 600;
            color: var(--text);
            margin-bottom: 4px;
        }
        .home-card-desc {
            font-size: 0.9rem;
            color: var(--text-muted);
        }
        .home-card-arrow {
            font-size: 1.5rem;
            color: var(--text-muted);
        }
        .home-card:hover .home-card-arrow {
            color: var(--primary);
        }
        
        .topbar {
            display: flex; align-items: center; gap: 12px;
            margin-bottom: 20px;
        }
        .topbar .toggle-btn {
            background: var(--sidebar-bg); border: 1px solid var(--border);
            border-radius: 6px; padding: 6px 10px;
        }
        .breadcrumb {
            padding: 10px 16px; background: var(--sidebar-bg);
            border: 1px solid var(--border); border-radius: 8px;
            font-size: 0.9rem;
        }
        .breadcrumb a { color: var(--primary); text-decoration: none; }
        .breadcrumb a:hover { text-decoration: underline; }
        
        /* 预览区 */
        .preview-box {
            background: var(--sidebar-bg); border: 1px solid var(--border);
            border-radius: 10px; padding: 20px; margin-top: 16px;
        }
        .preview-header {
            display: flex; align-items: center; justify-content: space-between;
            padding-bottom: 12px; margin-bottom: 16px;
            border-bottom: 1px solid var(--border);
        }
        .preview-header .filename { font-weight: 600; color: var(--primary); }
        .preview-header a { color: var(--primary); text-decoration: none; font-size: 0.9rem; }
        .preview-header a:hover { text-decoration: underline; }
        
        pre { background: var(--code-bg); padding: 16px; border-radius: 8px; overflow-x: auto; font-family: 'Fira Code', 'Consolas', monospace; font-size: 0.85rem; line-height: 1.5; }
        code { font-family: inherit; }
        img.preview-img { max-width: 100%; height: auto; border-radius: 8px; }
        .embed-pdf { width: 100%; height: 80vh; border: none; border-radius: 8px; }
        
        /* Markdown 样式（适配双主题） */
        .markdown-body { color: var(--text); line-height: 1.7; font-size: 0.95rem; }
        .markdown-body h1 { font-size: 1.6rem; color: var(--primary); border-bottom: 2px solid var(--primary); padding-bottom: 8px; margin: 1.5em 0 1em; }
        .markdown-body h2 { font-size: 1.3rem; color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 6px; margin: 1.3em 0 0.8em; }
        .markdown-body h3 { font-size: 1.1rem; color: var(--text); margin: 1.2em 0 0.6em; }
        .markdown-body h1:first-child, .markdown-body h2:first-child, .markdown-body h3:first-child { margin-top: 0; }
        .markdown-body p { margin: 0.8em 0; }
        .markdown-body a { color: var(--primary); }
        .markdown-body code { background: var(--code-bg); padding: 2px 6px; border-radius: 4px; font-size: 0.88em; }
        .markdown-body pre { background: var(--code-bg); margin: 1em 0; }
        .markdown-body pre code { background: none; padding: 0; }
        .markdown-body ul, .markdown-body ol { margin: 0.8em 0; padding-left: 1.5em; }
        .markdown-body li { margin: 0.3em 0; }
        .markdown-body blockquote { border-left: 4px solid var(--primary); margin: 1em 0; padding: 8px 16px; background: var(--hover); border-radius: 0 6px 6px 0; color: var(--text-muted); }
        .markdown-body table { border-collapse: collapse; width: 100%; margin: 1em 0; }
        .markdown-body th, .markdown-body td { border: 1px solid var(--border); padding: 10px 14px; text-align: left; }
        .markdown-body th { background: var(--hover); font-weight: 600; }
        .markdown-body hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
        .markdown-body img { max-width: 100%; border-radius: 8px; }
        
        @media (max-width: 768px) {
            .sidebar { transform: translateX(-260px); }
            .sidebar.mobile-open { transform: translateX(0); }
            .main { margin-left: 0; padding: 12px; }
            .topbar { flex-wrap: wrap; gap: 8px; }
            .breadcrumb { font-size: 0.8rem; padding: 8px 12px; }
            .preview-box { padding: 12px; }
            .preview-header { flex-direction: column; align-items: flex-start; gap: 8px; }
            pre { font-size: 0.75rem; padding: 10px; }
            .markdown-body { font-size: 0.9rem; }
            .markdown-body h1 { font-size: 1.3rem; }
            .markdown-body h2 { font-size: 1.15rem; }
            .file-item { padding: 10px 12px; }
            .file-item .name { font-size: 0.85rem; }
        }
        
        /* 移动端顶部栏 */
        .mobile-nav {
            display: none;
            position: fixed; top: 0; left: 0; right: 0;
            background: var(--sidebar-bg); border-bottom: 1px solid var(--border);
            padding: 10px 12px; z-index: 99;
            align-items: center; gap: 10px;
        }
        .mobile-nav .logo { font-size: 1rem; font-weight: 600; color: var(--primary); flex: 1; }
        .mobile-nav .mobile-back {
            background: none; border: 1px solid var(--border);
            border-radius: 6px; padding: 8px 12px;
            cursor: pointer; font-size: 0.9rem;
            color: var(--primary); text-decoration: none;
        }
        .mobile-nav button {
            background: none; border: 1px solid var(--border);
            border-radius: 6px; padding: 8px 12px;
            cursor: pointer; font-size: 1rem;
        }
        
        /* 遮罩层 */
        .sidebar-overlay {
            display: none;
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5); z-index: 99;
        }
        .sidebar-overlay.active { display: block; }
        
        @media (max-width: 768px) {
            .mobile-nav { display: flex; }
            body { padding-top: 50px; }
            .sidebar-overlay { display: none; }
            .sidebar-overlay.active { display: block; }
            .home-hero { padding: 24px 12px; }
            .home-hero h1 { font-size: 1.5rem; }
            .home-cards { grid-template-columns: 1fr; padding: 0 12px; }
            .home-card { padding: 16px; }
            .home-card-icon { font-size: 2rem; }
            .topbar { display: none; }
        }
    </style>
</head>
<body>
    <!-- 移动端顶部导航 -->
    <nav class="mobile-nav" id="mobileNav">
        {% if is_home %}
        <button onclick="toggleSidebar()">☰</button>
        {% elif parent_link and parent_link != '/' %}
        <a href="{{ parent_link }}" class="mobile-back">← 返回</a>
        {% elif parent_link == '/' %}
        <a href="/" class="mobile-back">← 首页</a>
        {% else %}
        <button onclick="toggleSidebar()">☰</button>
        {% endif %}
        <span class="logo">{{ filename or '📁 文件预览' }}</span>
    </nav>

    <!-- 遮罩层 -->
    <div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>
    
    <!-- 侧边栏 -->
    <aside class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <button class="toggle-btn" onclick="toggleSidebar()">☰</button>
            <h2>📁 文件浏览</h2>
            <button class="theme-btn" onclick="toggleTheme()" title="切换主题" id="themeBtn">🌙</button>
        </div>
        <nav class="sidebar-content">
            <ul class="file-list">
                <li class="file-item folder">
                    <a href="/">🏠 首页</a>
                </li>
                {% for item in sidebar_items %}
                <li class="file-item {{ item.class }}">
                    <a href="{{ item.url }}">
                        <span class="icon">{{ item.icon }}</span>
                        <span class="name">{{ item.name }}</span>
                        {% if item.size %}<span class="size">{{ item.size }}</span>{% endif %}
                    </a>
                    {% if item.time %}
                    <span class="meta">
                        <span class="time-item" title="创建时间">📅 {{ item.ctime }}</span>
                        <span class="time-item" title="更新时间">🕐 {{ item.mtime }}</span>
                    </span>
                    {% endif %}
                </li>
                {% endfor %}
                {% if current_path %}
                <li class="file-item" style="padding: 8px 16px; color: var(--text-muted); font-size: 0.8rem; border-top: 1px solid var(--border); margin-top: 8px;">
                    当前: {{ current_path }}
                </li>
                {% endif %}
            </ul>
        </nav>
    </aside>

    <!-- 主内容 -->
    <main class="main" id="main">
        {% if not is_home %}
        <div class="topbar">
            <button class="toggle-btn" onclick="toggleSidebar()">☰</button>
            <div class="breadcrumb">
                <a href="/">首页</a>
                {% if path %}
                    {% for part in breadcrumb %}
                        / <a href="{{ part.url }}">{{ part.name }}</a>
                    {% endfor %}
                {% endif %}
            </div>
        </div>
        {% endif %}
        
        {% if is_file %}
        <div class="preview-box">
            <div class="preview-header">
                <div style="display: flex; gap: 8px; align-items: center;">
                    {% if parent_link %}
                    <a href="{{ parent_link }}" style="color: var(--primary); text-decoration: none;">📂 返回</a>
                    <span style="color: var(--text-muted);">|</span>
                    {% endif %}
                    <span class="filename">{{ filename }}</span>
                </div>
                <a href="/raw{{ file_path }}">⬇ 下载</a>
            </div>
            {% if preview_type == 'image' %}
                <img src="/raw{{ file_path }}" class="preview-img" alt="{{ filename }}">
            {% elif preview_type == 'pdf' %}
                <embed src="/raw{{ file_path }}" class="embed-pdf" type="application/pdf">
            {% elif preview_type == 'text' %}
                {% if large_file %}
                <div id="textContent"><pre><code>{{ content }}</code></pre></div>
                <div style="text-align: center; padding: 16px;">
                    <span id="loadStatus" style="color: var(--text-muted);">{{ end_line }} / {{ total_lines }} 行</span>
                    {% if has_more %}
                    <button onclick="loadMoreText('{{ file_path }}')" id="loadMoreBtn" style="display: block; margin: 12px auto 0; padding: 8px 20px; background: var(--primary); color: white; border: none; border-radius: 6px; cursor: pointer;">加载更多</button>
                    {% endif %}
                </div>
                {% else %}
                <pre><code>{{ content }}</code></pre>
                {% endif %}
            {% elif preview_type == 'markdown' %}
                <div class="markdown-body">{{ content | safe }}</div>
            {% endif %}
        </div>
        {% else %}
        {% if is_home %}
        <!-- 首页 -->
        <div class="home-hero">
            <h1>📁 文件预览服务</h1>
            <p>选择一个目录开始浏览</p>
        </div>
        <div class="home-cards">
            {% for item in items %}
            <a href="{{ item.url }}" class="home-card">
                <div class="home-card-icon">{{ item.icon }}</div>
                <div class="home-card-info">
                    <div class="home-card-name">{{ item.name }}</div>
                    <div class="home-card-desc">{{ item.size }}</div>
                </div>
                <div class="home-card-arrow">→</div>
            </a>
            {% endfor %}
        </div>
        {% else %}
        <!-- 目录列表 -->
        <ul class="file-list">
            {% if parent_link and parent_link != '/' %}
            <li class="file-item folder">
                <a href="{{ parent_link }}">📂 .. 上级目录</a>
            </li>
            {% endif %}
            {% for item in items %}
            <li class="file-item {{ item.class }}">
                <a href="{{ item.url }}">
                    <span class="icon">{{ item.icon }}</span>
                    <span class="name">{{ item.name }}</span>
                    {% if item.size %}<span class="size">{{ item.size }}</span>{% endif %}
                </a>
                {% if item.time %}
                <span class="meta">
                    <span class="time-item" title="创建时间">📅 {{ item.ctime }}</span>
                    <span class="time-item" title="更新时间">🕐 {{ item.mtime }}</span>
                </span>
                {% endif %}
            </li>
            {% endfor %}
        </ul>
        {% endif %}
        {% endif %}
    </main>

<script>
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    const main = document.getElementById('main');
    
    if (window.innerWidth <= 768) {
        // 移动端：切换 mobile-open
        sidebar.classList.toggle('mobile-open');
        if (sidebar.classList.contains('mobile-open')) {
            overlay.classList.add('active');
        } else {
            overlay.classList.remove('active');
        }
    } else {
        // 桌面端：切换 collapsed
        sidebar.classList.toggle('collapsed');
        main.classList.toggle('expanded');
    }
}

function closeSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    sidebar.classList.remove('mobile-open');
    overlay.classList.remove('active');
}

// 点击链接后关闭侧边栏
document.addEventListener('DOMContentLoaded', function() {
    const links = document.querySelectorAll('.sidebar a');
    links.forEach(function(link) {
        link.addEventListener('click', function() {
            if (window.innerWidth <= 768) {
                closeSidebar();
            }
        });
    });
    
    // 恢复主题设置
    const saved = localStorage.getItem('theme');
    if (saved === 'dark') {
        document.body.setAttribute('data-theme', 'dark');
        document.getElementById('themeBtn').textContent = '☀️';
    }
});

let currentEnd = {{ end_line }};
const totalLines = {{ total_lines }};

function loadMoreText(filepath) {
    const btn = document.getElementById('loadMoreBtn');
    const status = document.getElementById('loadStatus');
    btn.disabled = true;
    btn.textContent = '加载中...';
    
    fetch(`/text_chunk/${filepath}?start=${currentEnd}&end=${currentEnd + 500}`)
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                btn.textContent = '加载失败';
                return;
            }
            const pre = document.querySelector('#textContent pre');
            pre.innerHTML += data.content.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            currentEnd = data.end;
            status.textContent = `${currentEnd} / ${totalLines} 行`;
            if (data.has_more) {
                btn.disabled = false;
                btn.textContent = '加载更多';
            } else {
                btn.remove();
            }
        })
        .catch(() => {
            btn.disabled = false;
            btn.textContent = '重试';
        });
}

function toggleTheme() {
    const body = document.body;
    const btn = document.getElementById('themeBtn');
    const isDark = body.getAttribute('data-theme') === 'dark';
    
    if (isDark) {
        body.removeAttribute('data-theme');
        btn.textContent = '🌙';
        localStorage.setItem('theme', 'light');
    } else {
        body.setAttribute('data-theme', 'dark');
        btn.textContent = '☀️';
        localStorage.setItem('theme', 'dark');
    }
}
</script>
</body>
</html>
"""

# ============ 路由 ============
@app.route('/')
def index():
    return render_home()

@app.route('/<path:filepath>')
def browse(filepath):
    return render_directory(filepath)

@app.route('/raw<path:filepath>')
def raw_file(filepath):
    safe = safe_path(filepath)
    if os.path.isdir(safe):
        abort(403)
    return send_file(safe)

@app.route('/text_chunk/<path:filepath>')
def text_chunk(filepath):
    """分块获取文本内容，用于大文件预览"""
    from flask import jsonify
    safe = safe_path(filepath)
    if os.path.isdir(safe):
        return jsonify({'error': 'not a file'}), 403
    
    import math
    chunk_size = 500
    start = int(request.args.get('start', 0))
    end = int(request.args.get('end', chunk_size))
    
    try:
        with open(safe, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        total = len(lines)
        content = ''.join(lines[start:end])
        has_more = end < total
        return jsonify({
            'content': content,
            'start': start,
            'end': end,
            'total': total,
            'has_more': has_more
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ 侧边栏数据 ============
def get_sidebar_items(current_path=''):
    """获取侧边栏目录列表"""
    items = []
    for d in ALLOWED_DIRS:
        if os.path.isdir(d):
            name = d.replace('/', '')
            icon = '📂'
            items.append({'name': name, 'url': '/' + name, 'icon': icon, 'class': 'folder', 'size': ''})
    return items

# ============ 首页 ============
def render_home():
    """渲染首页，显示可访问的目录"""
    # 构造首页卡片数据
    items = []
    for d in ALLOWED_DIRS:
        if os.path.isdir(d):
            name = d.replace('/', '') or 'root'
            # 统计子目录数量
            try:
                sub_count = len([x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))])
            except:
                sub_count = 0
            items.append({
                'name': name,
                'url': '/' + name,
                'icon': '📂',
                'class': 'folder',
                'size': f'{sub_count} 个子目录'
            })
    
    sidebar_items = get_sidebar_items()
    return render_template_string(HTML_TEMPLATE,
        path='', breadcrumb=[], items=items, is_file=False,
        parent_link=None, file_path='', preview_type='', content='', filename='',
        sidebar_items=sidebar_items, current_path='', is_home=True)

# ============ 目录渲染 ============
def get_file_icon(name, is_dir):
    if is_dir:
        return '📂', 'folder'
    ext = os.path.splitext(name)[1].lower()
    icons = {
        '.jpg': ('🖼️', 'image'), '.jpeg': ('🖼️', 'image'), '.png': ('🖼️', 'image'),
        '.gif': ('🖼️', 'image'), '.webp': ('🖼️', 'image'), '.svg': ('🖼️', 'image'),
        '.pdf': ('📄', 'pdf'), '.doc': ('📄', 'pdf'), '.docx': ('📄', 'pdf'),
        '.txt': ('📝', 'text'), '.md': ('📝', 'text'), '.markdown': ('📝', 'text'),
        '.py': ('💻', 'code'), '.js': ('💻', 'code'), '.ts': ('💻', 'code'),
        '.html': ('💻', 'code'), '.css': ('💻', 'code'), '.json': ('💻', 'code'),
        '.xml': ('💻', 'code'), '.yaml': ('💻', 'code'), '.yml': ('💻', 'code'),
        '.sh': ('💻', 'code'), '.bash': ('💻', 'code'), '.zsh': ('💻', 'code'),
        '.c': ('💻', 'code'), '.cpp': ('💻', 'code'), '.h': ('💻', 'code'),
        '.java': ('💻', 'code'), '.go': ('💻', 'code'), '.rs': ('💻', 'code'),
        '.sql': ('💻', 'code'), '.log': ('📝', 'text'), '.ini': ('⚙️', 'text'),
        '.conf': ('⚙️', 'text'), '.cfg': ('⚙️', 'text'), '.env': ('⚙️', 'text'),
    }
    return icons.get(ext, ('📄', 'text'))

def format_size(size):
    if size < 1024:
        return f'{size}B'
    elif size < 1024**2:
        return f'{size/1024:.1f}K'
    elif size < 1024**3:
        return f'{size/1024**2:.1f}M'
    else:
        return f'{size/1024**3:.1f}G'

def format_time(timestamp):
    """格式化时间戳为可读格式"""
    from datetime import datetime
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')

def render_directory(filepath):
    safe = safe_path(filepath)
    
    # 如果是文件，渲染预览
    if os.path.isfile(safe):
        return render_file_preview(filepath, safe)
    
    # 如果是目录，渲染列表
    if not os.path.isdir(safe):
        abort(404)
    
    items = []
    try:
        for name in sorted(os.listdir(safe)):
            full_path = os.path.join(safe, name)
            is_dir = os.path.isdir(full_path)
            icon, cls = get_file_icon(name, is_dir)
            url = f'/{name}' if not filepath else f'/{filepath}/{name}'
            size = format_size(os.path.getsize(full_path)) if not is_dir else ''
            # 获取文件创建时间和修改时间
            import stat
            st = os.stat(full_path)
            ctime = format_time(st.st_ctime)
            mtime = format_time(st.st_mtime)
            time_str = f"创建:{ctime} 更新:{mtime}" if not is_dir else f"更新:{mtime}"
            items.append({'name': name, 'url': url, 'icon': icon, 'class': cls, 'size': size, 'time': time_str, 'ctime': ctime, 'mtime': mtime})
    except PermissionError:
        sidebar_items = get_sidebar_items(filepath)
        return render_template_string(HTML_TEMPLATE, 
            path=filepath, breadcrumb=[], items=[], is_file=False, parent_link=None, 
            file_path='', preview_type='', content='', filename='',
            sidebar_items=sidebar_items, current_path=filepath), 403
    
    # 面包屑
    breadcrumb = []
    parts = filepath.strip('/').split('/')
    url_acc = ''
    for part in parts[:-1]:
        url_acc += '/' + part
        breadcrumb.append({'name': part, 'url': url_acc})
    
    parent_link = '/' + '/'.join(parts[:-1]) if len(parts) > 1 else '/'
    # 如果已经是顶级目录，parent_link保持为'/'，模板会显示"← 首页"
    # 只有根目录本身才设置为None
    if not filepath:
        parent_link = None
    
    sidebar_items = get_sidebar_items(filepath)
    return render_template_string(HTML_TEMPLATE,
        path=filepath,
        breadcrumb=breadcrumb,
        items=items,
        is_file=False,
        parent_link=parent_link,
        file_path='',
        preview_type='',
        content='',
        filename='',
        sidebar_items=sidebar_items, current_path=filepath
    )

def render_file_preview(filepath, safe):
    ext = os.path.splitext(filepath)[1].lower()
    filename = os.path.basename(filepath)
    
    # 计算父目录路径
    parts = filepath.rsplit('/', 1)
    parent_link = '/' + parts[0] if parts[0] else '/'
    # 根目录的父目录回到首页
    if parent_link == '/':
        parent_link = '/'
    
    # 图片预览
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']:
        sidebar_items = get_sidebar_items(filepath)
        return render_template_string(HTML_TEMPLATE,
            path=filepath, breadcrumb=[], items=[], is_file=True,
            parent_link=parent_link, file_path=filepath, preview_type='image',
            content='', filename=filename,
            sidebar_items=sidebar_items, current_path=filepath)
    
    # PDF预览
    if ext == '.pdf':
        sidebar_items = get_sidebar_items(filepath)
        return render_template_string(HTML_TEMPLATE,
            path=filepath, breadcrumb=[], items=[], is_file=True,
            parent_link=parent_link, file_path=filepath, preview_type='pdf',
            content='', filename=filename,
            sidebar_items=sidebar_items, current_path=filepath)
    
    # Markdown 渲染
    if ext in ['.md', '.markdown']:
        max_size = 1024 * 1024
        size = os.path.getsize(safe)
        if size > max_size:
            content = f'<p>【文件过大 ({format_size(size)})，请下载查看】</p>'
        else:
            try:
                with open(safe, 'r', encoding='utf-8', errors='replace') as f:
                    raw = f.read()
                content = markdown.markdown(raw, extensions=['fenced_code', 'tables', 'toc'])
            except:
                content = '<p>【无法预览此文件】</p>'
        sidebar_items = get_sidebar_items(filepath)
        return render_template_string(HTML_TEMPLATE,
            path=filepath, breadcrumb=[], items=[], is_file=True,
            parent_link=parent_link, file_path=filepath, preview_type='markdown',
            content=content, filename=filename,
            sidebar_items=sidebar_items, current_path=filepath)
    
    # 文本/代码预览（分页加载大文件）
    try:
        with open(safe, 'r', encoding='utf-8', errors='replace') as f:
            all_lines = f.readlines()
        total_lines = len(all_lines)
        chunk_size = 500
        
        # 大文件分页，小文件直接加载
        if total_lines > chunk_size:
            content = ''.join(all_lines[:chunk_size]).replace('<', '&lt;').replace('>', '&gt;')
            large_file = True
            end_line = chunk_size
            has_more = True
        else:
            content = ''.join(all_lines).replace('<', '&lt;').replace('>', '&gt;')
            large_file = False
            end_line = total_lines
            has_more = False
    except:
        content = '【无法预览此文件】'
        total_lines = 0
        large_file = False
        end_line = 0
        has_more = False
    
    sidebar_items = get_sidebar_items(filepath)
    return render_template_string(HTML_TEMPLATE,
        path=filepath, breadcrumb=[], items=[], is_file=True,
        parent_link=parent_link, file_path=filepath, preview_type='text',
        content=content, filename=filename,
        sidebar_items=sidebar_items, current_path=filepath,
        large_file=large_file, total_lines=total_lines,
        end_line=end_line, has_more=has_more)

if __name__ == '__main__':
    print(f"🚀 文件预览服务启动: http://0.0.0.0:{PORT}")
    print(f"📁 访问根目录: /")
    app.run(host=HOST, port=PORT, debug=False)
