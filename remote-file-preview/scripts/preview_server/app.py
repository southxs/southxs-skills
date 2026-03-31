#!/usr/bin/env python3
"""
Preview Server - 远程文件预览服务
支持：Markdown/代码高亮、图片、文本预览
支持：时间戳命名、过期清理
"""
import os
import sqlite3
import uuid
import hashlib
import time
import asyncio
from pathlib import Path
from urllib.parse import quote, unquote
from aiohttp import web
import markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension

FILES_DIR   = os.environ.get('PREVIEW_FILES_DIR', '/data/files')
DB_PATH     = os.environ.get('PREVIEW_DB_PATH',   '/data/preview.db')
PORT        = int(os.environ.get('PREVIEW_PORT',  '8081'))
RATE_LIMIT  = int(os.environ.get('PREVIEW_RATE_LIMIT', '10'))   # 每分钟每 IP 上传次数

# 全局限流状态
_upload_counts = {}   # {ip: (count, reset_time)}


# ==================== 数据库 ====================

def init_db():
    """初始化 SQLite 元数据库"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id           TEXT PRIMARY KEY,
            random_name  TEXT UNIQUE NOT NULL,
            original_name TEXT        NOT NULL,
            file_path    TEXT        NOT NULL,
            size         INTEGER      DEFAULT 0,
            password_hash TEXT,
            upload_time  INTEGER      NOT NULL,
            expire_time  INTEGER,
            access_count INTEGER      DEFAULT 0,
            ip           TEXT
        )
    """)
    conn.commit()
    return conn


def get_db():
    return sqlite3.connect(DB_PATH)


# ==================== 工具函数 ====================

def get_abs_path(path_info):
    clean = unquote(path_info.lstrip('/'))
    return Path(FILES_DIR) / clean


def check_rate_limit(ip):
    """简单 IP 限流：每分钟最多 RATE_LIMIT 次上传"""
    now = time.time()
    if ip in _upload_counts:
        count, reset_time = _upload_counts[ip]
        if now > reset_time:
            _upload_counts[ip] = (1, now + 60)
            return True
        if count >= RATE_LIMIT:
            return False
        _upload_counts[ip] = (count + 1, reset_time)
        return True
    _upload_counts[ip] = (1, now + 60)
    return True


def cleanup_expired():
    """删除过期文件（被调用时执行一次）"""
    conn = get_db()
    now = int(time.time())
    rows = conn.execute(
        "SELECT random_name, file_path FROM files WHERE expire_time IS NOT NULL AND expire_time < ?",
        (now,)
    ).fetchall()
    deleted = 0
    for row in rows:
        fp = Path(row[1])
        if fp.exists():
            try:
                fp.unlink()
                deleted += 1
            except OSError:
                pass
        conn.execute("DELETE FROM files WHERE random_name = ?", (row[0],))
    conn.commit()
    conn.close()
    return deleted


# ==================== 路由：文件访问 ====================

async def serve_file(abs_path):
    s = abs_path.suffix.lower()
    if s in ('.md', '.markdown'):
        c = abs_path.read_text(encoding='utf-8', errors='replace')
        h = markdown.markdown(c, extensions=[FencedCodeExtension(), CodeHiliteExtension(guess_lang=False), TableExtension(), 'nl2br'])
        return web.Response(
            text='<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark">'
                 '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css" id="hljs-light">'
                 '<script>if(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches){'
                 'document.getElementById("hljs-light").href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css"}'
                 '</script></head><body><article class="markdown-body">'+h+'</article></body></html>',
            content_type='text/html', charset='utf-8')
    if s in ('.html', '.htm'):
        return web.Response(text=abs_path.read_text(encoding='utf-8', errors='replace'), content_type='text/html')
    if s in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.bmp'):
        return web.FileResponse(abs_path)
    c = abs_path.read_text(encoding='utf-8', errors='replace') \
        .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return web.Response(
        text='<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"></head>'
             '<body style="background:var(--bg-primary);color:var(--text-primary);font-family:monospace">'
             '<a href="/" style="color:var(--link-color)">⬅️</a>'
             '<pre style="background:var(--code-bg);padding:16px;border-radius:8px;white-space:pre-wrap;border:1px solid var(--border-color)">'
             '<code>' + c + '</code></pre></body></html>',
        content_type='text/html', charset='utf-8')


async def index(request):
    """浏览目录"""
    path_info = request.match_info.get('path', '')
    path = request.query.get('path', '/' + path_info if path_info else '/')
    display_path = unquote(path)
    abs_path = get_abs_path(path)

    if not abs_path.exists():
        return web.Response(text='404 Not Found', status=404)
    if abs_path.is_file():
        return await serve_file(abs_path)

    items = []
    parent = str(Path(display_path).parent)
    for item in sorted(abs_path.iterdir()):
        rel = item.relative_to(FILES_DIR)
        encoded_rel = quote(str(rel), safe='/')
        ipath = '/' + encoded_rel
        icon = '📄' if item.is_file() else '📁'
        size = str(round(item.stat().st_size / 1024, 1)) + 'K' if item.is_file() else '-'
        items.append('<li><a href="' + ipath + '">' + icon + ' ' + item.name + '</a> <span class="size">' + size + '</span></li>')

    html = ('<html><head><meta charset="utf-8"><title>' + quote(display_path) + '</title>'
            '<style>body{font-family:-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:20px;background:#f5f5f5}'
            'ul{list-style:none;padding:0;background:#fff;border-radius:8px}'
            'li{padding:12px 16px;border-bottom:1px solid #eee;display:flex;justify-content:space-between}'
            'a{color:#0066cc;text-decoration:none}.size{color:#888;font-size:.85em}</style></head>'
            '<body><h1>📁 ' + display_path + '</h1>')
    if parent and parent != '.':
        encoded_parent = quote(parent, safe='/')
        html += '<div style="margin-bottom:16px"><a href="' + encoded_parent + '">⬆️ 返回上级</a></div>'
    html += '<ul>' + ('' if not items else ''.join(items)) + '</ul></body></html>'
    return web.Response(text=html, content_type='text/html', charset='utf-8')


# ==================== 路由：文件访问 /{timestamp_name} ====================

async def file_access(request):
    """
    访问文件：/{timestamp_name}
    """
    timestamp_name = request.match_info.get('random_name', '')

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM files WHERE random_name = ?", (timestamp_name,)
    ).fetchone()
    conn.close()

    if not row:
        return web.Response(text='❌ 文件不存在或已删除', status=404)

    (file_id, timestamp_name, original_name, file_path,
     size, password_hash, upload_time, expire_time,
     access_count, ip) = row

    # 过期检查
    if expire_time and expire_time < int(time.time()):
        return web.Response(text='❌ 链接已过期', status=410)

    abs_path = Path(file_path)
    if not abs_path.exists():
        return web.Response(text='❌ 文件已删除', status=404)

    # 更新访问计数
    conn2 = get_db()
    conn2.execute("UPDATE files SET access_count = access_count + 1 WHERE random_name = ?", (timestamp_name,))
    conn2.commit()
    conn2.close()

    return await serve_file(abs_path)


# ==================== 路由：管理接口 ====================

async def admin_list(request):
    """列出所有文件（管理面板）"""
    conn = get_db()
    rows = conn.execute(
        "SELECT random_name, original_name, size, upload_time, expire_time, access_count "
        "FROM files ORDER BY upload_time DESC"
    ).fetchall()
    conn.close()

    now = int(time.time())
    items = []
    for row in rows:
        rn, on, size, ut, et, ac = row
        expire_str = '永不过期' if not et else (
            '已过期' if et < now else '还剩 {}s'.format(et - now))
        ut_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ut))
        items.append(
            '<tr><td><a href="/' + rn + '">' + on + '</a></td>'
            '<td>' + ut_str + '</td>'
            '<td>' + expire_str + '</td>'
            '<td>' + str(ac) + '</td>'
            '<td><a href="/admin/delete/' + rn + '" onclick="return confirm(\'确认删除 ' + on + '？\')">🗑️ 删除</a></td></tr>'
        )

    html = ('<html><head><meta charset="utf-8"><title>文件管理</title>'
            '<style>body{font-family:-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:20px}'
            'table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden}'
            'th,td{padding:12px 16px;border-bottom:1px solid #eee;text-align:left}'
            'th{background:#f6f8fa;font-weight:600}'
            'a{color:#0066cc;text-decoration:none}a:hover{text-decoration:underline}'
            '.expired{color:#d00}body{background:#f5f5f5}</style></head>'
            '<body><h1>📋 文件管理</h1>'
            '<p style="color:#888">共 {} 个文件 &nbsp;|&nbsp; '
            '<a href="/admin/cleanup">🧹 清理过期文件</a> &nbsp;|&nbsp; '
            '<a href="/">📁 浏览文件</a></p>'
            '<table><thead><tr><th>文件名</th><th>上传时间</th><th>过期时间</th><th>访问次数</th><th>操作</th></tr></thead>'
            '<tbody>').format(len(items))
    html += ''.join(items) if items else '<tr><td colspan="5" style="text-align:center;color:#888">暂无文件</td></tr>'
    html += '</tbody></table></body></html>'
    return web.Response(text=html, content_type='text/html', charset='utf-8')


async def admin_delete(request):
    """删除指定文件"""
    random_name = request.match_info.get('random_name', '')
    confirm = request.query.get('confirm', '')

    conn = get_db()
    row = conn.execute("SELECT file_path FROM files WHERE random_name = ?", (random_name,)).fetchone()
    if not row:
        conn.close()
        return web.Response(text='❌ 文件不存在', status=404)

    if confirm.lower() != 'yes':
        conn.close()
        return web.Response(
            text='<html><head><meta charset="utf-8"><title>确认删除</title></head>'
                 '<body style="font-family:-apple-system,sans-serif;max-width:400px;margin:100px auto;text-align:center">'
                 '<h2>确认删除此文件？</h2><p>此操作不可恢复。</p>'
                 '<a href="/admin/delete/' + random_name + '?confirm=yes"><button style="padding:10px 24px;background:#d00;color:#fff;border:none;border-radius:6px;cursor:pointer">确认删除</button></a>'
                 ' <a href="/admin/list"><button style="padding:10px 24px;border:1px solid #ddd;border-radius:6px;cursor:pointer">取消</button></a>'
                 '</body></html>',
            content_type='text/html', charset='utf-8')

    # 删除物理文件
    fp = Path(row[0])
    if fp.exists():
        fp.unlink()
    conn.execute("DELETE FROM files WHERE random_name = ?", (random_name,))
    conn.commit()
    conn.close()
    raise web.HTTPFound('/admin/list?msg=deleted')


async def admin_cleanup(request):
    """手动触发过期清理"""
    deleted = cleanup_expired()
    raise web.HTTPFound('/admin/list?msg=cleaned&count=' + str(deleted))


# ==================== 主入口 ====================

app = web.Application()

# 启动时初始化数据库 + 清理过期文件
init_db()
try:
    deleted = cleanup_expired()
    if deleted > 0:
        print('🧹 已清理 {} 个过期文件'.format(deleted))
except Exception as e:
    print('⚠️  清理过期文件失败:', e)

# 路由注册
app.router.add_get('/',          index)
app.router.add_get('/preview',   index)
app.router.add_get('/{timestamp_name}',  file_access)
app.router.add_get('/admin/list',       admin_list)
app.router.add_get('/admin/delete/{random_name}', admin_delete)
app.router.add_get('/admin/cleanup',    admin_cleanup)
app.router.add_get('/{path:.+}', index)

if __name__ == '__main__':
    print('Preview Server: ' + FILES_DIR + ' | DB: ' + DB_PATH + ' | Port: ' + str(PORT))
    web.run_app(app, host='0.0.0.0', port=PORT)
