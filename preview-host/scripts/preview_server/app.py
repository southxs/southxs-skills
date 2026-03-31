#!/usr/bin/env python3
import os
from pathlib import Path
from urllib.parse import quote, unquote
from aiohttp import web
import markdown
from markdown.extensions.codehilite import CodeHiliteExtension
from markdown.extensions.fenced_code import FencedCodeExtension
from markdown.extensions.tables import TableExtension

FILES_DIR = os.environ.get('PREVIEW_FILES_DIR', '/data/files')
PORT = int(os.environ.get('PREVIEW_PORT', '8081'))

def get_abs_path(path_info):
    clean = unquote(path_info.lstrip('/'))
    return Path(FILES_DIR) / clean

async def index(request):
    path_info = request.match_info.get('path', '')
    path = request.query.get('path', '/' + path_info if path_info else '/')
    display_path = unquote(path)  # 解码用于显示
    abs_path = get_abs_path(path)
    
    if not abs_path.exists():
        return web.Response(text='404 Not Found', status=404)
    if abs_path.is_file():
        return await serve_file(abs_path)
    
    items = []
    parent = str(Path(display_path).parent)
    for item in sorted(abs_path.iterdir()):
        rel = item.relative_to(FILES_DIR)
        # URL 编码文件名，防止下划线被 markdown 解析
        encoded_rel = quote(str(rel), safe='/')
        ipath = '/' + encoded_rel
        icon = '📄' if item.is_file() else '📁'
        size = str(round(item.stat().st_size/1024, 1)) + 'K' if item.is_file() else '-'
        items.append('<li><a href="' + ipath + '">' + icon + ' ' + item.name + '</a> <span>' + size + '</span></li>')
    
    html = '<html><head><meta charset="utf-8"><title>' + display_path + '</title>'
    html += '<style>body{font-family:-apple-system,sans-serif;max-width:900px;margin:0 auto;padding:20px;background:#f5f5f5}ul{list-style:none;padding:0;background:#fff;border-radius:8px}li{padding:12px 16px;border-bottom:1px solid #eee}a{color:#0066cc;text-decoration:none}.size{color:#888;font-size:.85em}</style>'
    html += '</head><body><h1>📁 ' + display_path + '</h1>'
    if parent and parent != '.':
        encoded_parent = quote(parent, safe='/')
        html += '<div style="margin-bottom:16px"><a href="' + encoded_parent + '">⬆️ 返回上级</a></div>'
    html += '<ul>' + ('' if not items else ''.join(items)) + '</ul></body></html>'
    return web.Response(text=html, content_type='text/html', charset='utf-8')

DARK_MODE_CSS = '''
<style>
/* Auto dark/light mode based on system preference */
@media (prefers-color-scheme: dark) {
  :root {
    --bg-primary: #0d1117;
    --bg-secondary: #161b22;
    --bg-tertiary: #21262d;
    --text-primary: #c9d1d9;
    --text-secondary: #8b949e;
    --border-color: #30363d;
    --link-color: #58a6ff;
    --code-bg: #161b22;
  }
}
@media (prefers-color-scheme: light) {
  :root {
    --bg-primary: #ffffff;
    --bg-secondary: #f6f8fa;
    --bg-tertiary: #eaeef2;
    --text-primary: #1f2328;
    --text-secondary: #656d76;
    --border-color: #d0d7de;
    --link-color: #0066cc;
    --code-bg: #f6f8fa;
  }
}
/* Fallback for browsers without prefers-color-scheme */
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f6f8fa;
  --bg-tertiary: #eaeef2;
  --text-primary: #1f2328;
  --text-secondary: #656d76;
  --border-color: #d0d7de;
  --link-color: #0066cc;
  --code-bg: #f6f8fa;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: var(--bg-primary);
  color: var(--text-primary);
  margin: 0;
  padding: 20px;
}
.markdown-body {
  max-width: 900px;
  margin: 0 auto;
  padding: 24px;
  background: var(--bg-primary);
  color: var(--text-primary);
  font-size: 16px;
  line-height: 1.6;
}
.markdown-body h1, .markdown-body h2, .markdown-body h3, 
.markdown-body h4, .markdown-body h5, .markdown-body h6 {
  color: var(--text-primary);
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 8px;
  margin-top: 24px;
}
.markdown-body a { color: var(--link-color); }
.markdown-body code {
  background: var(--code-bg);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 0.9em;
}
.markdown-body pre {
  background: var(--code-bg) !important;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  padding: 16px;
  overflow-x: auto;
}
.markdown-body pre code {
  background: transparent;
  padding: 0;
}
.markdown-body table {
  border-collapse: collapse;
  width: 100%;
}
.markdown-body table th, .markdown-body table td {
  border: 1px solid var(--border-color);
  padding: 8px 12px;
  text-align: left;
}
.markdown-body table th {
  background: var(--bg-secondary);
}
.markdown-body blockquote {
  border-left: 4px solid var(--link-color);
  margin: 16px 0;
  padding: 0 16px;
  color: var(--text-secondary);
}
.markdown-body img {
  max-width: 100%;
  height: auto;
}
.markdown-body hr {
  border: none;
  border-top: 1px solid var(--border-color);
  margin: 24px 0;
}
/* Syntax highlighting overrides for dark mode */
.hljs { background: transparent !important; }
</style>
'''

async def serve_file(abs_path):
    s = abs_path.suffix.lower()
    if s in ('.md', '.markdown'):
        c = abs_path.read_text(encoding='utf-8', errors='replace')
        h = markdown.markdown(c, extensions=[FencedCodeExtension(), CodeHiliteExtension(guess_lang=False), TableExtension(), 'nl2br'])
        return web.Response(text='<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"><link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css" id="hljs-light"><script>if(window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches){document.getElementById("hljs-light").href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css"}</script></head><body><article class="markdown-body">'+h+'</article></body></html>' + DARK_MODE_CSS, content_type='text/html', charset='utf-8')
    if s in ('.html', '.htm'):
        return web.Response(text=abs_path.read_text(encoding='utf-8', errors='replace'), content_type='text/html')
    if s in ('.png','.jpg','.jpeg','.gif','.webp','.svg','.ico','.bmp'):
        return web.FileResponse(abs_path)
    c = abs_path.read_text(encoding='utf-8', errors='replace').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;')
    return web.Response(text='<html><head><meta charset="utf-8"><meta name="color-scheme" content="light dark"></head><body style="background:var(--bg-primary);color:var(--text-primary);font-family:monospace"><a href="/" style="color:var(--link-color)">⬅️</a><pre style="background:var(--code-bg);padding:16px;border-radius:8px;white-space:pre-wrap;border:1px solid var(--border-color)"><code>'+c+'</code></pre></body></html>' + '''<style>:root{--bg-primary:#fff;--bg-secondary:#f6f8fa;--code-bg:#f6f8fa;--text-primary:#1f2328;--text-secondary:#656d76;--border-color:#d0d7de;--link-color:#0066cc}@media(prefers-color-scheme:dark){:root{--bg-primary:#0d1117;--bg-secondary:#161b22;--code-bg:#161b22;--text-primary:#c9d1d9;--text-secondary:#8b949e;--border-color:#30363d;--link-color:#58a6ff}}</style>''', content_type='text/html', charset='utf-8')

async def raw_file(request):
    path = request.query.get('path', '')
    abs_path = get_abs_path('/' + path)
    if not abs_path.exists() or not abs_path.is_file():
        return web.Response(text='404', status=404)
    return web.FileResponse(abs_path, headers={'Content-Disposition': 'attachment; filename=' + abs_path.name})

app = web.Application()
app.router.add_get('/', index)
app.router.add_get('/preview', index)
app.router.add_get('/raw', raw_file)
app.router.add_get('/{path:.+}', index)

if __name__ == '__main__':
    print('Preview: ' + FILES_DIR + ' on :' + str(PORT))
    web.run_app(app, host='0.0.0.0', port=PORT)
