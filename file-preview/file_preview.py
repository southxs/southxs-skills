#!/usr/bin/env python3
"""
文件预览服务 - 支持文本/代码/Markdown/图片/PDF
"""

import os
import re
import hmac
import hashlib
import time
import base64
import json
import secrets
from collections import defaultdict
from datetime import datetime
from functools import wraps

import markdown
from flask import (
    Flask, send_file, abort, render_template_string,
    Response, request, jsonify, make_response, redirect
)

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)  # 随机生成，每次重启失效（仅影响 session）

# ============ 配置（从环境变量读取） ============
HOST = "0.0.0.0"
PORT = 8881
ALLOWED_DIRS = os.environ.get('ALLOWED_DIRS', '/root,/software').split(',')

# 鉴权配置 — 必须通过环境变量设置
AUTH_USERNAME = os.environ.get('AUTH_USERNAME', '')
AUTH_PASSWORD = os.environ.get('AUTH_PASSWORD', '')
AUTH_ENABLED = os.environ.get('AUTH_ENABLED', 'true').lower() == 'true'

if AUTH_ENABLED and (not AUTH_USERNAME or not AUTH_PASSWORD):
    raise ValueError(
        "鉴权已开启但未设置凭据。请设置环境变量 AUTH_USERNAME 和 AUTH_PASSWORD，"
        "或设置 AUTH_ENABLED=false 禁用鉴权"
    )

# ============ 登录频率限制 ============
LOGIN_ATTEMPTS = defaultdict(list)  # ip -> [timestamp, ...]
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5分钟


def is_locked_out(ip):
    """检查IP是否被锁定"""
    now = time.time()
    # 清理过期记录
    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS[ip] if now - t < LOCKOUT_SECONDS]
    return len(LOGIN_ATTEMPTS[ip]) >= MAX_ATTEMPTS


def record_attempt(ip):
    """记录登录尝试"""
    LOGIN_ATTEMPTS[ip].append(time.time())


def get_remaining_lockout(ip):
    """获取剩余锁定时间（秒）"""
    if not LOGIN_ATTEMPTS[ip]:
        return 0
    oldest = min(LOGIN_ATTEMPTS[ip])
    remaining = LOCKOUT_SECONDS - (time.time() - oldest)
    return max(0, int(remaining))


# ============ Token 管理 ============
def generate_token():
    """生成访问令牌（基于HMAC，有效期24小时）"""
    now = int(time.time())
    expire = now + 24 * 3600
    issued = now
    msg = f"{AUTH_USERNAME}:{issued}:{expire}"
    sig = hmac.new(AUTH_PASSWORD.encode(), msg.encode(), hashlib.sha256).hexdigest()
    token = base64.b64encode(f"{msg}:{sig}".encode()).decode()
    return token, issued, expire


def validate_token(token):
    """验证令牌是否有效，返回(是否有效, 剩余秒数, 过期时间戳)"""
    try:
        decoded = base64.b64decode(token.encode()).decode()
        parts = decoded.rsplit(':', 1)
        if len(parts) != 2:
            return False, 0, 0
        msg, sig = parts
        fields = msg.split(':')
        if len(fields) != 3:
            return False, 0, 0
        username, issued, expire = fields[0], int(fields[1]), int(fields[2])

        # 验证签名
        expected_sig = hmac.new(
            AUTH_PASSWORD.encode(), msg.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return False, 0, 0

        remaining = expire - time.time()
        if remaining > 0:
            return True, int(remaining), int(expire)
        return False, 0, 0
    except Exception:
        return False, 0, 0


# ============ 登录页面 ============
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>🔐 登录 - 文件预览</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; margin: 0; }
        .login-box { background: white; padding: 40px; border-radius: 16px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); width: 340px; }
        .login-box h1 { text-align: center; color: #333; margin-bottom: 8px; font-size: 1.5rem; }
        .login-box p { text-align: center; color: #888; margin-bottom: 30px; font-size: 0.9rem; }
        .form-group { margin-bottom: 20px; }
        .form-group label { display: block; margin-bottom: 8px; color: #555; font-weight: 500; }
        .form-group input { width: 100%; padding: 12px 16px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 1rem; box-sizing: border-box; transition: border-color 0.2s; }
        .form-group input:focus { outline: none; border-color: #e94560; }
        .error { background: #fff0f0; color: #e94560; padding: 12px; border-radius: 8px; margin-bottom: 20px; text-align: center; }
        .submit-btn { width: 100%; padding: 14px; background: linear-gradient(135deg, #e94560, #ff6b6b); color: white; border: none; border-radius: 8px; font-size: 1rem; cursor: pointer; transition: transform 0.2s, box-shadow 0.2s; }
        .submit-btn:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(233,69,96,0.4); }
        .submit-btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }
    </style>
</head>
<body>
    <div class="login-box">
        <h1>🔐 文件预览</h1>
        <p>请登录以继续访问</p>
        {% if error %}
        <div class="error">{{ error }}</div>
        {% endif %}
        <form method="post">
            <div class="form-group">
                <label>用户名</label>
                <input type="text" name="username" placeholder="请输入用户名" required autocomplete="username">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" name="password" placeholder="请输入密码" required autocomplete="current-password">
            </div>
            <button type="submit" class="submit-btn" {% if locked %}disabled{% endif %}>
                {% if locked %}锁定中，请 {{ remaining }}秒 后重试{% else %}登 录{% endif %}
            </button>
        </form>
    </div>
</body>
</html>
"""

# ============ Cookie鉴权装饰器 ============
def require_auth(f):
    """Cookie验证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not AUTH_ENABLED:
            return f(*args, **kwargs)

        valid, _ = check_auth()
        if valid:
            return f(*args, **kwargs)

        return redirect('/login', 302)
    return decorated_function


def check_auth():
    """检查Cookie中的Token，返回(是否有效, 剩余秒数)"""
    if not AUTH_ENABLED:
        return True, 0
    token = request.cookies.get('fp_token', '')
    if not token:
        return False, 0
    return validate_token(token)[:2]


def make_login_page(error=None, locked=False, remaining=0):
    return render_template_string(LOGIN_TEMPLATE, error=error, locked=locked, remaining=remaining)


# ============ 安全检查 ============
def safe_path(path):
    """防止目录遍历，只允许访问 ALLOWED_DIRS"""
    real = os.path.realpath('/' + path)
    # 使用 os.path.commonpath 精确判断，防止 /rootfoo 通过 /root 校验
    for d in ALLOWED_DIRS:
        try:
            if os.path.commonpath([real, d]) == d:
                return real
        except ValueError:
            # 跨盘符比较会抛 ValueError，跳过
            continue
    abort(403)


def allowed_file(filename):
    """允许的文件类型"""
    return True


# ============ 页面模板 ============
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>📁 文件预览</title>
    <link rel="icon" type="image/x-icon" href="/favicon.ico">
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
        
        .theme-btn {
            background: none; border: 1px solid var(--border);
            cursor: pointer; font-size: 0.9rem;
            padding: 4px 8px; border-radius: 4px;
            color: var(--text);
        }
        .theme-btn:hover { background: var(--hover); }
        
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
        .toggle-btn {
            background: none; border: none; cursor: pointer;
            font-size: 1.2rem; color: var(--text-muted);
            padding: 4px 8px; border-radius: 4px;
        }
        .toggle-btn:hover { background: var(--hover); }
        .logout-btn {
            background: none; border: 1px solid var(--border);
            cursor: pointer; font-size: 0.9rem;
            padding: 4px 8px; border-radius: 4px;
            color: var(--text-muted); text-decoration: none;
        }
        .logout-btn:hover { background: var(--hover); }
        .token-expire {
            font-size: 0.75rem; color: var(--text-muted);
            padding: 4px 8px; background: var(--hover); border-radius: 4px;
        }
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
        .file-item .meta { display: flex; align-items: center; gap: 12px; margin-left: auto; flex-shrink: 0; }
        .file-item .time { color: var(--text-muted); font-size: 0.7rem; white-space: nowrap; }
        .file-item .time-item { display: flex; align-items: center; gap: 2px; white-space: nowrap; }
        .file-item .time-icon { font-size: 0.65rem; opacity: 0.7; }
        .file-item.folder .name { color: var(--primary); font-weight: 500; }
        
        .main {
            margin-left: 260px; padding: 24px;
            transition: margin-left 0.3s ease;
        }
        .main.expanded { margin-left: 0; }
        
        .home-hero { text-align: center; padding: 40px 20px; margin-bottom: 30px; }
        .home-hero h1 { font-size: 2rem; color: var(--primary); margin-bottom: 12px; }
        .home-hero p { color: var(--text-muted); font-size: 1.1rem; }
        .home-cards {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px; max-width: 800px; margin: 0 auto;
        }
        .home-card {
            display: flex; align-items: center; gap: 16px; padding: 24px;
            background: var(--sidebar-bg); border: 1px solid var(--border);
            border-radius: 12px; text-decoration: none; color: var(--text); transition: all 0.2s;
        }
        .home-card:hover { border-color: var(--primary); box-shadow: 0 4px 20px rgba(233, 69, 96, 0.1); transform: translateY(-2px); }
        .home-card-icon { font-size: 2.5rem; }
        .home-card-info { flex: 1; }
        .home-card-name { font-size: 1.3rem; font-weight: 600; color: var(--text); margin-bottom: 4px; }
        .home-card-desc { font-size: 0.9rem; color: var(--text-muted); }
        .home-card-arrow { font-size: 1.5rem; color: var(--text-muted); }
        .home-card:hover .home-card-arrow { color: var(--primary); }
        
        .topbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
        .topbar .toggle-btn { background: var(--sidebar-bg); border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; }
        .breadcrumb { padding: 10px 16px; background: var(--sidebar-bg); border: 1px solid var(--border); border-radius: 8px; font-size: 0.9rem; }
        .breadcrumb a { color: var(--primary); text-decoration: none; }
        .breadcrumb a:hover { text-decoration: underline; }
        
        .preview-box { background: var(--sidebar-bg); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-top: 16px; }
        .preview-header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; margin-bottom: 16px; border-bottom: 1px solid var(--border); }
        .preview-header .filename { font-weight: 600; color: var(--primary); }
        .preview-header a { color: var(--primary); text-decoration: none; font-size: 0.9rem; }
        .preview-header a:hover { text-decoration: underline; }
        
        pre { background: var(--code-bg); padding: 16px; border-radius: 8px; overflow-x: auto; font-family: 'Fira Code', 'Consolas', monospace; font-size: 0.85rem; line-height: 1.5; }
        code { font-family: inherit; }
        img.preview-img { max-width: 100%; height: auto; border-radius: 8px; }
        .embed-pdf { width: 100%; height: 80vh; border: none; border-radius: 8px; }
        
        .markdown-body { color: var(--text); line-height: 1.7; font-size: 0.95rem; }
        .markdown-body h1 { font-size: 1.6rem; color: var(--primary); border-bottom: 2px solid var(--primary); padding-bottom: 8px; margin: 1.5em 0 1em; }
        .markdown-body h2 { font-size: 1.3rem; color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 6px; margin: 1.3em 0 0.8em; }
        .markdown-body h3 { font-size: 1.1rem; color: var(--text); margin: 1.2em 0 0.6em; }
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
        
        .mobile-nav {
            display: none; position: fixed; top: 0; left: 0; right: 0;
            background: var(--sidebar-bg); border-bottom: 1px solid var(--border);
            padding: 10px 12px; z-index: 99; align-items: center; gap: 10px;
        }
        .mobile-nav .logo { font-size: 1rem; font-weight: 600; color: var(--primary); flex: 1; }
        .mobile-nav .mobile-back {
            background: none; border: 1px solid var(--border); border-radius: 6px;
            padding: 8px 12px; cursor: pointer; font-size: 0.9rem; color: var(--primary); text-decoration: none;
        }
        .mobile-nav button { background: none; border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; cursor: pointer; font-size: 1rem; }
        
        .sidebar-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 99; }
        .sidebar-overlay.active { display: block; }
        
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
            .file-item { padding: 10px 12px; flex-wrap: wrap; }
            .file-item .name { font-size: 0.85rem; }
            .file-item .meta { gap: 6px; }
            .file-item .time { font-size: 0.65rem; }
            .file-item .time-item { font-size: 0.65rem; }
            .file-item .size { display: none; }
            .mobile-nav { display: flex; }
            body { padding-top: 50px; }
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

    <div class="sidebar-overlay" id="sidebarOverlay" onclick="closeSidebar()"></div>
    
    <aside class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <button class="toggle-btn" onclick="toggleSidebar()">☰</button>
            <h2>📁 文件浏览</h2>
            <a href="/logout" class="logout-btn" title="退出登录">🚪</a>
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
                <pre><code id="textContent">{{ content }}</code></pre>
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
        sidebar.classList.toggle('mobile-open');
        overlay.classList.toggle('active', sidebar.classList.contains('mobile-open'));
    } else {
        sidebar.classList.toggle('collapsed');
        main.classList.toggle('expanded');
    }
}

function closeSidebar() {
    document.getElementById('sidebar').classList.remove('mobile-open');
    document.getElementById('sidebarOverlay').classList.remove('active');
}

document.addEventListener('DOMContentLoaded', function() {
    const saved = localStorage.getItem('theme');
    if (saved === 'dark') {
        document.body.setAttribute('data-theme', 'dark');
        const btn = document.getElementById('themeBtn');
        if (btn) btn.textContent = '☀️';
    }
    // Token 自动刷新（httpOnly cookie 由服务端管理，客户端无需操作）
    checkTokenHealth();
});

function checkTokenHealth() {
    fetch('/token_health', {credentials: 'include'})
        .then(r => { if (r.status === 401) window.location.href = '/login'; })
        .catch(() => {});
}

function loadMoreText(filepath) {
    const btn = document.getElementById('loadMoreBtn');
    const status = document.getElementById('loadStatus');
    btn.disabled = true;
    btn.textContent = '加载中...';
    
    fetch('/text_chunk/' + filepath + '?start=' + currentEnd + '&end=' + (currentEnd + 500), {credentials: 'include'})
        .then(r => r.json())
        .then(data => {
            if (data.error) { btn.textContent = '加载失败'; return; }
            const code = document.getElementById('textContent');
            // 使用 textContent 而非 innerHTML，防止 XSS
            code.textContent += data.content;
            currentEnd = data.end;
            status.textContent = currentEnd + ' / ' + totalLines + ' 行';
            if (data.has_more) {
                btn.disabled = false;
                btn.textContent = '加载更多';
            } else {
                btn.remove();
            }
        })
        .catch(() => { btn.disabled = false; btn.textContent = '重试'; });
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

var currentEnd = {{ end_line|default(0) }};
var totalLines = {{ total_lines|default(0) }};
</script>
</body>
</html>
"""

# ============ 路由 ============
@app.route('/favicon.ico')
def favicon():
    safe = os.path.join(os.path.dirname(__file__), 'favicon.png')
    if os.path.exists(safe):
        return send_file(safe)
    abort(404)


@app.route('/login', methods=['GET', 'POST'])
def login():
    ip = request.remote_addr

    if request.method == 'GET':
        if is_locked_out(ip):
            remaining = get_remaining_lockout(ip)
            return make_login_page(locked=True, remaining=remaining), 429
        return make_login_page()

    username = request.form.get('username', '')
    password = request.form.get('password', '')

    if is_locked_out(ip):
        remaining = get_remaining_lockout(ip)
        return make_login_page(error=f'登录尝试过多，请 {remaining}秒 后重试', locked=True, remaining=remaining), 429

    if username == AUTH_USERNAME and password == AUTH_PASSWORD:
        token, issued, expire = generate_token()
        # Token 仅通过 httpOnly cookie 传递，不暴露给 JS
        resp = make_response(redirect('/'))
        resp.set_cookie('fp_token', token, max_age=24 * 3600, httponly=True, samesite='Lax')
        return resp
    else:
        record_attempt(ip)
        remaining = get_remaining_lockout(ip)
        error_msg = '用户名或密码错误'
        if is_locked_out(ip):
            error_msg = f'登录尝试过多，请 {remaining}秒 后重试'
            return make_login_page(error=error_msg, locked=True, remaining=remaining), 429
        return make_login_page(error=error_msg), 401


@app.route('/token_health')
@require_auth
def token_health():
    """Token 健康检查 — 前端用于检测是否需要重新登录"""
    return jsonify({'status': 'ok'})


@app.route('/logout')
def logout():
    resp = make_response(redirect('/login'))
    resp.delete_cookie('fp_token')
    return resp


@app.route('/')
@require_auth
def index():
    return render_home()


@app.route('/<path:filepath>')
@require_auth
def browse(filepath):
    return render_directory(filepath)


@app.route('/raw<path:filepath>')
@require_auth
def raw_file(filepath):
    safe = safe_path(filepath)
    if os.path.isdir(safe):
        abort(403)
    return send_file(safe)


@app.route('/text_chunk/<path:filepath>')
@require_auth
def text_chunk(filepath):
    """分块获取文本内容，用于大文件预览"""
    safe = safe_path(filepath)
    if os.path.isdir(safe):
        return jsonify({'error': 'not a file'}), 403

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
    items = []
    for d in ALLOWED_DIRS:
        if os.path.isdir(d):
            name = d.replace('/', '')
            items.append({'name': name, 'url': '/' + name, 'icon': '📂', 'class': 'folder', 'size': ''})
    return items


# ============ 首页 ============
def render_home():
    items = []
    for d in ALLOWED_DIRS:
        if os.path.isdir(d):
            name = d.replace('/', '') or 'root'
            try:
                sub_count = len([x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))])
            except Exception:
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
        sidebar_items=sidebar_items, current_path='', is_home=True,
        end_line=0, total_lines=0)


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
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')


def render_directory(filepath):
    safe = safe_path(filepath)

    if os.path.isfile(safe):
        return render_file_preview(filepath, safe)

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

    breadcrumb = []
    parts = filepath.strip('/').split('/')
    url_acc = ''
    for part in parts[:-1]:
        url_acc += '/' + part
        breadcrumb.append({'name': part, 'url': url_acc})

    parent_link = '/' + '/'.join(parts[:-1]) if len(parts) > 1 else '/'
    if not filepath:
        parent_link = None

    sidebar_items = get_sidebar_items(filepath)
    return render_template_string(HTML_TEMPLATE,
        path=filepath, breadcrumb=breadcrumb, items=items, is_file=False,
        parent_link=parent_link, file_path='', preview_type='', content='', filename='',
        sidebar_items=sidebar_items, current_path=filepath
    )


def render_file_preview(filepath, safe):
    ext = os.path.splitext(filepath)[1].lower()
    filename = os.path.basename(filepath)

    parts = filepath.rsplit('/', 1)
    parent_link = '/' + parts[0] if parts[0] else '/'

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
            content = '<p>【文件过大，请下载查看】</p>'
        else:
            try:
                with open(safe, 'r', encoding='utf-8', errors='replace') as f:
                    raw = f.read()
                content = markdown.markdown(raw, extensions=['fenced_code', 'tables', 'toc'])
            except Exception:
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
    except Exception:
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
    if not AUTH_USERNAME:
        print("⚠️  警告: 未设置 AUTH_USERNAME / AUTH_PASSWORD，鉴权已禁用")
    app.run(host=HOST, port=PORT, debug=False)
