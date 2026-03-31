#!/usr/bin/env python3
"""
Preview Host - 文件上传与预览链接生成
支持自动检测服务状态、随机文件名、密码保护、过期清理

使用方式：
  python3 preview_host.py upload <本地路径> [子目录] [选项]
  python3 preview_host.py status   # 检查服务状态
  python3 preview_host.py list     # 列出远程文件
  python3 preview_host.py delete <random_name>  # 删除文件

选项：
  --password 密码        设置文件访问密码（可选）
  --expire-days 天数     设置过期天数（可选，不设置则永不过期）
  --no-random-name      使用原始文件名（默认使用随机文件名）
"""
import os
import sys
import uuid
import hashlib
import subprocess
import argparse
import importlib.util
import tempfile
import time
import sqlite3
from urllib.parse import quote

# 读取环境变量
HOST         = os.environ.get("PREVIEW_HOST", "")
SSH_USER     = os.environ.get("PREVIEW_SSH_USER", "root")
SSH_KEY      = os.environ.get("PREVIEW_SSH_KEY", "")
FILE_ROOT    = "/software/southxs-preview/files"
DNSPOD_DOMAIN    = os.environ.get("DNSPOD_DOMAIN", "")
DNSPOD_SUB_DOMAIN = os.environ.get("DNSPOD_SUB_DOMAIN", "preview")
DEFAULT_URL  = "https://{}.{}".format(
    DNSPOD_SUB_DOMAIN, DNSPOD_DOMAIN) if DNSPOD_DOMAIN else ""

# 凭证状态（避免重复写 temp 文件）
_ssh_key_path = None

# 限流状态（本地）
_rate_counts = {}


def _check_rate_limit(remote_ip):
    """简单限流：每分钟最多 10 次上传（per remote IP）"""
    global _rate_counts
    now = time.time()
    if remote_ip in _rate_counts:
        count, reset_time = _rate_counts[remote_ip]
        if now > reset_time:
            _rate_counts[remote_ip] = (1, now + 60)
            return True
        if count >= 10:
            print("⚠️  限流：超过每分钟 10 次上传限制，请稍后再试")
            return False
        _rate_counts[remote_ip] = (count + 1, reset_time)
        return True
    _rate_counts[remote_ip] = (1, now + 60)
    return True


def _get_ssh_key_path():
    """
    返回 SSH 密钥路径：
    - 原始密钥文本（含 -----BEGIN）→ 写入 temp 文件，返回路径
    - 文件路径 → 直接返回
    """
    global _ssh_key_path
    if _ssh_key_path:
        return _ssh_key_path
    if not SSH_KEY:
        return None
    if SSH_KEY.strip().startswith("-----BEGIN"):
        fd, path = tempfile.mkstemp(prefix="ssh_key_")
        os.write(fd, SSH_KEY.encode())
        os.close(fd)
        os.chmod(path, 0o600)
        _ssh_key_path = path
    else:
        _ssh_key_path = SSH_KEY
    return _ssh_key_path


def cmd(c, check=True):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, executable="/bin/bash")
    if check and r.returncode != 0:
        return None
    return r


def ssh_cmd(c, check=True, timeout=30):
    key_path = _get_ssh_key_path()
    key_opt = "-i {}".format(key_path) if key_path else ""
    r = subprocess.run(
        "ssh {} -o StrictHostKeyChecking=no -o ConnectTimeout=10 {}@{} {}".format(
            key_opt, SSH_USER, HOST, c.replace('"', '\\"') if '"' in c else c),
        shell=True, capture_output=True, text=True, executable="/bin/bash", timeout=timeout
    )
    if check and r.returncode != 0:
        return None
    return r


# ------------------ 状态检查 ------------------

def check_service_status():
    """
    检查服务器服务状态，返回 (docker_ok, npm_ok, preview_ok, dns_ok)
    """
    print("🔍 检查服务状态...")

    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量: PREVIEW_HOST, PREVIEW_SSH_USER")
        return False, False, False, False

    # 1. SSH 连接
    r = ssh_cmd("echo OK", check=False, timeout=10)
    if r is None:
        print("❌ SSH 连接失败")
        return False, False, False, False
    print("  ✅ SSH 连接正常")

    # 2. Docker
    r = ssh_cmd("docker --version 2>/dev/null && echo OK || echo FAIL", check=False, timeout=10)
    docker_ok = r and "OK" in r.stdout
    print("  {} Docker".format("✅" if docker_ok else "❌"))

    # 3. NPM
    r = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:81/ 2>/dev/null", check=False, timeout=10)
    npm_ok = r and r.stdout.strip() == "200"
    print("  {} Nginx Proxy Manager (http://{}:81)".format("✅" if npm_ok else "❌", HOST if npm_ok else HOST))

    # 4. 预览服务
    r = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/ 2>/dev/null", check=False, timeout=10)
    preview_ok = r and r.stdout.strip() == "200"
    print("  {} 预览服务 (http://{}:8081)".format("✅" if preview_ok else "❌", HOST if preview_ok else HOST))

    # 5. DNS
    if DNSPOD_DOMAIN:
        dns_url = "https://{}.{}/".format(DNSPOD_SUB_DOMAIN, DNSPOD_DOMAIN)
        r = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' " + dns_url + " 2>/dev/null", check=False, timeout=10)
        dns_ok = r and r.stdout.strip() in ("200", "301", "302")
        print("  {} DNS/SSL ({})".format(
            "✅" if dns_ok else "⚠️  未配置", dns_url))
    else:
        dns_ok = False
        print("  ⚠️  未配置域名")

    return docker_ok, npm_ok, preview_ok, dns_ok


def auto_setup():
    """
    服务缺失时，自动调用 setup.py 进行初始化
    """
    print("\n🚀 检测到服务缺失，启动自动初始化...")

    # 动态导入 setup 模块
    script_dir = os.path.dirname(os.path.abspath(__file__))
    setup_path = os.path.join(script_dir, "setup.py")

    if not os.path.exists(setup_path):
        print("❌ setup.py 不存在，无法自动初始化")
        return False

    # 导入 setup 模块
    spec = importlib.util.spec_from_file_location("setup_module", setup_path)
    setup_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup_module)

    # 只运行必要的步骤（跳过 DNS 让用户自己配，或者跳过已成功的步骤）
    steps = [
        ("SSH 连接",     setup_module.step_ssh),
    ]

    # 动态判断，跳过可选步骤
    docker_ok, npm_ok, preview_ok, dns_ok = check_service_status()

    if not dns_ok:
        steps.append(("DNS 配置", setup_module.step_dns))
    steps += [
        ("目录创建",     setup_module.step_dirs),
    ]
    if not docker_ok:
        steps.append(("Docker 安装", setup_module.step_docker))
    if not npm_ok:
        steps.append(("NPM 部署",   setup_module.step_npm))
    if not npm_ok:
        steps.append(("NPM 代理+SSL", setup_module.step_npm_proxy))
    if not preview_ok:
        steps.append(("预览服务",   setup_module.step_preview))

    failed = False
    for name, fn in steps:
        print("\n" + "=" * 50)
        print("  {}".format(name))
        print("=" * 50)
        if not fn():
            print("\n❌ 初始化失败: {}".format(name))
            failed = True
            break

    if not failed:
        print("\n✅ 自动初始化完成！")

    # 重新检查状态
    print("\n📋 初始化后状态：")
    check_service_status()

    return not failed


# ------------------ 文件上传 ------------------

def _db_path():
    return "{}/preview.db".format(FILE_ROOT)


def _insert_metadata(random_name, original_name, file_path, size, password, expire_days, ip):
    """在服务器 SQLite 中插入文件元数据"""
    pwd_hash = hashlib.sha256(password.encode()).hexdigest() if password else None
    now = int(time.time())
    expire_time = now + expire_days * 86400 if expire_days else None
    fid = str(uuid.uuid4())

    # 构建 SQL（用 Python 上传到服务器执行，避免 shell 引号转义问题）
    script_path = "/tmp/_insert_meta_{}.py".format(fid[:8])
    script = """
import sqlite3, sys
conn = sqlite3.connect('{}')
conn.execute('CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, random_name TEXT UNIQUE, original_name TEXT, file_path TEXT, size INTEGER, password_hash TEXT, upload_time INTEGER, expire_time INTEGER, access_count INTEGER DEFAULT 0, ip TEXT)')
conn.execute('INSERT INTO files (id, random_name, original_name, file_path, size, password_hash, upload_time, expire_time, ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
    ('{}', '{}', '{}', '{}', {}, {}, ?, ?))
conn.commit()
conn.close()
print('OK')
""".format(
        _db_path(), fid, random_name,
        original_name.replace("'", "''"),
        file_path.replace("'", "''"),
        size,
        pwd_hash if pwd_hash else None,
        now,
        expire_time if expire_time else None,
        ip or ''
    )

    # 写入临时 Python 脚本
    ssh_cmd("cat > {} << 'SCRIPT'\n{}\nSCRIPT".format(script_path, script.replace("'", "'\"'\"'")), check=False)
    # 执行
    r = ssh_cmd("python3 {}".format(script_path), check=False, timeout=15)
    # 清理
    ssh_cmd("rm -f {}".format(script_path), check=False)
    return r and 'OK' in r.stdout


def upload(local_path, remote_subdir="", password=None, expire_days=None, use_random_name=True):
    """上传文件/目录，返回预览链接（包含 random_name）"""
    if not HOST or not SSH_USER:
        print("❌ 缺少必要环境变量: PREVIEW_HOST, PREVIEW_SSH_USER")
        return None

    raw_name = os.path.basename(local_path.rstrip("/"))
    if not raw_name:
        print("❌ 无效的路径: {}".format(local_path))
        return None

    # 生成随机文件名（保留原始扩展名）
    if use_random_name:
        ext = os.path.splitext(raw_name)[1]
        random_name = uuid.uuid4().hex + ext
    else:
        random_name = raw_name

    remote_dir = "{}/{}".format(FILE_ROOT, remote_subdir).rstrip("/")
    remote_path = "{}/{}".format(remote_dir, random_name)

    # 限流检查（上传前获取服务器 IP 作为标识）
    remote_ip = HOST
    if not _check_rate_limit(remote_ip):
        return None

    local_size = 0
    if os.path.isfile(local_path):
        local_size = os.path.getsize(local_path)
    elif os.path.isdir(local_path):
        local_size = sum(f.stat().st_size for f in os.scandir(local_path) if f.is_file())

    # 创建目录
    ssh_cmd("mkdir -p {}".format(remote_dir), check=False)
    # 初始化数据库（如不存在）
    ssh_cmd(
        'sqlite3 {} "CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, random_name TEXT UNIQUE, original_name TEXT, file_path TEXT, size INTEGER, password_hash TEXT, upload_time INTEGER, expire_time INTEGER, access_count INTEGER DEFAULT 0, ip TEXT)"'.format(_db_path()),
        check=False
    )

    key_path = _get_ssh_key_path()
    key_opt = "-i {}".format(key_path) if key_path else ""

    # 上传
    if os.path.isdir(local_path):
        r = cmd('rsync -avz -e "ssh {} -o StrictHostKeyChecking=no" {}/ {}@{}:{}/'.format(
            key_opt, local_path, SSH_USER, HOST, remote_dir))
    else:
        r = cmd('scp {} -o StrictHostKeyChecking=no {} {}@{}:{}/'.format(
            key_opt, local_path, SSH_USER, HOST, remote_dir))

    if r is None:
        return None

    # 写入元数据
    _insert_metadata(random_name, raw_name, remote_path, local_size, password, expire_days, None)

    # 生成访问链接
    # 新格式：/f/{random_name}（受保护访问）
    url = "{}/f/{}".format(DEFAULT_URL, random_name) if DEFAULT_URL else None

    print("✅ 上传成功")
    if password:
        print("   🔒 访问密码: {}".format(password))
    if expire_days:
        print("   ⏰ 过期时间: {} 天后".format(expire_days))
    if url:
        print("   预览: {}".format(url))
        if password:
            print("   完整访问: {}?pwd={}".format(url, password))
    else:
        print("   文件已上传到: {}/{}".format(remote_dir, random_name))
    return url


# ------------------ 远程文件列表/删除 ------------------

def list_files():
    """列出远程所有文件"""
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量")
        return

    print("\n📋 远程文件列表：")
    print("-" * 80)
    r = ssh_cmd(
        'sqlite3 -header -column {} "SELECT random_name, original_name, size, upload_time, expire_time, password_hash FROM files ORDER BY upload_time DESC"'.format(_db_path()),
        check=False, timeout=15
    )
    if not r or r.returncode != 0:
        print("  （暂无文件）")
        return
    print(r.stdout)


def delete_file(random_name):
    """删除远程文件"""
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量")
        return None

    # 查询文件路径
    r = ssh_cmd(
        'sqlite3 {} "SELECT file_path FROM files WHERE random_name=\'{}\'"'.format(_db_path(), random_name),
        check=False, timeout=10
    )
    if not r or not r.stdout.strip():
        print("❌ 文件不存在: {}".format(random_name))
        return False

    file_path = r.stdout.strip()
    # 删除物理文件
    ssh_cmd("rm -f '{}'".format(file_path), check=False)
    # 删除元数据
    ssh_cmd("sqlite3 {} \"DELETE FROM files WHERE random_name='{}'\"".format(_db_path(), random_name), check=False)
    print("✅ 已删除: {}".format(random_name))
    return True


# ------------------ 主入口 ------------------

def main():
    parser = argparse.ArgumentParser(
        description="Preview Host 上传工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python3 preview_host.py upload /tmp/readme.md
  python3 preview_host.py upload /tmp/readme.md projects/
  python3 preview_host.py upload /tmp/readme.md --password 123456 --expire-days 7
  python3 preview_host.py upload /tmp/readme.md --no-random-name
  python3 preview_host.py list
  python3 preview_host.py delete a1b2c3d4.md
        """
    )
    parser.add_argument("action", choices=["upload", "status", "setup", "list", "delete"],
                        help="操作: upload=上传, status=检查状态, setup=初始化, list=列表, delete=删除")
    parser.add_argument("local", nargs="?", default="", help="本地路径（upload 时用）")
    parser.add_argument("remote", nargs="?", default="", help="远程子目录")
    parser.add_argument("--password", dest="password", default=None,
                        help="设置访问密码（可选）")
    parser.add_argument("--expire-days", dest="expire_days", type=int, default=None,
                        help="设置过期天数（可选）")
    parser.add_argument("--no-random-name", dest="no_random_name", action="store_true",
                        help="使用原始文件名（默认使用随机文件名）")

    args = parser.parse_args()

    if args.action == "status":
        check_service_status()

    elif args.action == "setup":
        auto_setup()

    elif args.action == "list":
        list_files()

    elif args.action == "delete":
        if not args.local:
            print("❌ 请指定要删除的 random_name")
            sys.exit(1)
        delete_file(args.local)

    elif args.action == "upload":
        if not args.local:
            print("❌ 请指定要上传的文件或目录")
            sys.exit(1)

        # 检查状态
        docker_ok, npm_ok, preview_ok, dns_ok = check_service_status()

        # 自动初始化（如需要）
        if not all([docker_ok, npm_ok, preview_ok]):
            print("")
            if not auto_setup():
                print("⚠️  自动初始化未完成，上传可能失败")

        # 执行上传
        url = upload(
            args.local,
            args.remote,
            password=args.password,
            expire_days=args.expire_days,
            use_random_name=not args.no_random_name
        )
        if url:
            print("\n📎 {}".format(url))
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
