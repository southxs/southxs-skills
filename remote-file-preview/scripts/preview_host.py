#!/usr/bin/env python3
"""
Preview Host - 文件上传与预览链接生成
支持自动检测服务状态、时间戳命名、过期清理

使用方式：
  python3 preview_host.py upload <本地路径> [子目录] [选项]
  python3 preview_host.py status   # 检查服务状态
  python3 preview_host.py list     # 列出远程文件
  python3 preview_host.py delete <random_name>  # 删除文件

选项：
  --expire-days 天数     设置过期天数（可选，不设置则永不过期）
  --no-timestamp        使用原始文件名（默认使用时间戳命名）
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
    # /data/preview.db inside container = /software/southxs-preview/app/preview/data/preview.db on host
    return "/software/southxs-preview/app/preview/data/preview.db"


def _insert_metadata(timestamp_name, original_name, file_path, size, expire_days, ip):
    """
    在服务器 SQLite 中插入文件元数据。
    策略：写一个 Python 脚本到远程 /tmp，执行后清理。
    所有数据直接写在脚本里，不通过 shell 传参。
    """
    now = int(time.time())
    expire_time = now + expire_days * 86400 if expire_days else None
    fid = str(uuid.uuid4())

    # 将所有数据写成 Python 字面量，直接 embed 到脚本中（避免 shell 传参转义）
    script = (
        "import sqlite3\n"
        "conn=sqlite3.connect('/software/southxs-preview/app/preview/data/preview.db')\n"
        "conn.execute('CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY,"
        "random_name TEXT UNIQUE,original_name TEXT,file_path TEXT,size INTEGER,"
        "password_hash TEXT,upload_time INTEGER,expire_time INTEGER,"
        "access_count INTEGER DEFAULT 0,ip TEXT)')\n"
        "conn.execute('INSERT INTO files (id,random_name,original_name,file_path,size,"
        "password_hash,upload_time,expire_time,ip) values (?,?,?,?,?,?,?,?,?)',"
        "('%s','%s','%s','%s',%s,%s,%s,%s,'%s'))\n"
        "conn.commit()\n"
        "print('OK')\n"
    ) % (
        fid,
        timestamp_name,
        original_name.replace("'", "''"),
        file_path.replace("'", "''"),
        size,
        'NULL',
        now,
        'NULL' if not expire_time else str(expire_time),
        ip or ''
    )

    remote_script = "/tmp/_ins_{}.py".format(fid[:8])

    # 写脚本到远程（通过本地文件上传）
    script_local = "/tmp/_ins_{}.py".format(fid[:8])
    with open(script_local, 'w') as f:
        f.write(script)
    key_opt = "-i {}".format(_get_ssh_key_path()) if _get_ssh_key_path() else ""
    r = cmd('scp {} -o StrictHostKeyChecking=no {} root@{}:{}'.format(
        key_opt, script_local, HOST, remote_script), check=False)
    os.unlink(script_local)
    if r is None or r.returncode != 0:
        print("   ⚠️  脚本上传失败")
        return False

    # 执行
    r = ssh_cmd("python3 {}".format(remote_script), check=False, timeout=15)
    ok = r and r.returncode == 0 and 'OK' in r.stdout

    # 清理
    ssh_cmd("rm -f {}".format(remote_script), check=False)

    if not ok:
        print("   ⚠️  DB 写入失败: {}".format(r.stderr[:100] if r and r.stderr else "unknown"))
    return ok


def upload(local_path, remote_subdir="", expire_days=None, use_timestamp_name=True):
    """上传文件/目录，返回预览链接（包含 timestamp_name）"""
    if not HOST or not SSH_USER:
        print("❌ 缺少必要环境变量: PREVIEW_HOST, PREVIEW_SSH_USER")
        return None

    raw_name = os.path.basename(local_path.rstrip("/"))
    if not raw_name:
        print("❌ 无效的路径: {}".format(local_path))
        return None

    # 生成时间戳文件名：yyyy-MM-dd_HH-mm-ss_原始文件名
    if use_timestamp_name:
        ts = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
        timestamp_name = "{}_{}".format(ts, raw_name)
    else:
        timestamp_name = raw_name

    remote_dir = "{}/{}".format(FILE_ROOT, remote_subdir).rstrip("/")
    # 存储容器内路径（app.py 用 FILES_DIR=/data/files）
    container_path = "/data/files/{}".format(timestamp_name)

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

    # 上传（使用随机文件名）
    if os.path.isdir(local_path):
        # 目录：先同步到临时位置，再重命名
        tmp_dir = remote_dir + "/.tmp_" + uuid.uuid4().hex[:8]
        ssh_cmd("mkdir -p {}".format(tmp_dir), check=False)
        r = cmd('rsync -avz -e "ssh {} -o StrictHostKeyChecking=no" {}/ {}@{}:{}/'.format(
            key_opt, local_path, SSH_USER, HOST, tmp_dir))
        if r and r.returncode == 0:
            # 重命名每个文件（保持目录结构，随机文件名）
            ssh_cmd("cd {} && for f in $(find . -type f); do dir=$(dirname $f | sed 's|^\./||'); bn=$(uuidgen | cut -d'-' -f1)$(echo $f | rev | cut -d'.' -f1 | rev) ; mv \"$f\" \"$dir/$bn\" 2>/dev/null || true; done".format(tmp_dir), check=False)
            ssh_cmd("cp -r {}/* {}/ && rm -rf {}".format(tmp_dir, remote_dir, tmp_dir), check=False)
    else:
        r = cmd('scp {} -o StrictHostKeyChecking=no {} {}@{}:{}/{}'.format(
            key_opt, local_path, SSH_USER, HOST, remote_dir, timestamp_name))

    if r is None:
        return None

    # 写入元数据
    _insert_metadata(timestamp_name, raw_name, container_path, local_size, expire_days, None)

    # 生成访问链接
    url = "{}/{}".format(DEFAULT_URL, timestamp_name) if DEFAULT_URL else None

    print("✅ 上传成功")
    if expire_days:
        print("   ⏰ 过期时间: {} 天后".format(expire_days))
    if url:
        print("   预览: {}".format(url))
    else:
        print("   文件已上传到: {}/{}".format(remote_dir, timestamp_name))
    return url


# ------------------ 远程文件列表/删除 ------------------

def list_files():
    """列出远程所有文件"""
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量")
        return

    import json
    script = (
        "import sqlite3,time\n"
        "conn=sqlite3.connect('/software/southxs-preview/app/preview/data/preview.db')\n"
        "rows=conn.execute('SELECT random_name,original_name,size,upload_time,expire_time,password_hash FROM files ORDER BY upload_time DESC').fetchall()\n"
        "print('%-32s %-30s %8s %19s %12s %5s' % ('RANDOM_NAME','ORIGINAL_NAME','SIZE','UPLOAD_TIME','EXPIRE_TIME','PWD'))\n"
        "for r in rows:\n"
        " pwd='Y' if r[5] else 'N'\n"
        " et='-' if not r[4] else time.strftime('%Y-%m-%d %H:%M',time.localtime(r[4]))\n"
        " ut=time.strftime('%Y-%m-%d %H:%M',time.localtime(r[3]))\n"
        " print('%-32s %-30s %8d %19s %12s %5s' % (r[0],r[1],r[2] or 0,ut,et,pwd))\n"
    )

    remote_script = "/tmp/_list_files.py"
    script_local = "/tmp/_list_files.py"
    with open(script_local, 'w') as f:
        f.write(script)
    key_opt = "-i {}".format(_get_ssh_key_path()) if _get_ssh_key_path() else ""
    cmd('scp {} -o StrictHostKeyChecking=no {} root@{}:{}'.format(
        key_opt, script_local, HOST, remote_script), check=False)
    os.unlink(script_local)

    r = ssh_cmd("python3 {}".format(remote_script), check=False, timeout=15)
    ssh_cmd("rm -f {}".format(remote_script), check=False)

    print("\n📋 远程文件列表：")
    print("-" * 100)
    if r and r.returncode == 0 and r.stdout.strip():
        print(r.stdout)
    else:
        print("  （暂无文件）")


def delete_file(random_name):
    """删除远程文件"""
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量")
        return None

    # Step 1: 查询文件路径
    select_script = (
        "import sqlite3\n"
        "conn=sqlite3.connect('/software/southxs-preview/app/preview/data/preview.db')\n"
        "row=conn.execute('SELECT file_path FROM files WHERE random_name=?',('%s',)).fetchone()\n"
        "if row: print(row[0])\n"
    ) % (random_name,)

    sel_local = "/tmp/_del_sel.py"
    sel_remote = "/tmp/_del_sel.py"
    with open(sel_local, 'w') as f:
        f.write(select_script)
    key_opt = "-i {}".format(_get_ssh_key_path()) if _get_ssh_key_path() else ""
    cmd('scp {} -o StrictHostKeyChecking=no {} root@{}:{}'.format(
        key_opt, sel_local, HOST, sel_remote), check=False)
    os.unlink(sel_local)

    r = ssh_cmd("python3 {}".format(sel_remote), check=False, timeout=15)
    ssh_cmd("rm -f {}".format(sel_remote), check=False)
    file_path = r.stdout.strip() if r and r.returncode == 0 else ""

    if not file_path:
        print("❌ 文件不存在: {}".format(random_name))
        return False

    # Step 2: 删除物理文件（支持容器内路径和宿主机路径）
    # 容器内路径 /data/files/xxx 或宿主机路径 /software/southxs-preview/files/xxx
    container_path = file_path.replace('/software/southxs-preview/files/', '/data/files/')
    ssh_cmd("docker exec southxs-preview rm -f '{}'".format(container_path), check=False)

    # Step 3: 删除元数据
    del_script = (
        "import sqlite3\n"
        "conn=sqlite3.connect('/software/southxs-preview/app/preview/data/preview.db')\n"
        "conn.execute('DELETE FROM files WHERE random_name=?',('%s',))\n"
        "conn.commit()\n"
    ) % (random_name,)

    del_local = "/tmp/_del_meta.py"
    del_remote = "/tmp/_del_meta.py"
    with open(del_local, 'w') as f:
        f.write(del_script)
    cmd('scp {} -o StrictHostKeyChecking=no {} root@{}:{}'.format(
        key_opt, del_local, HOST, del_remote), check=False)
    os.unlink(del_local)
    ssh_cmd("python3 {}".format(del_remote), check=False, timeout=15)
    ssh_cmd("rm -f {}".format(del_remote), check=False)

    print("✅ 已删除: {} (文件: {})".format(random_name, file_path))
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
    parser.add_argument("--expire-days", dest="expire_days", type=int, default=None,
                        help="设置过期天数（可选）")
    parser.add_argument("--no-timestamp", dest="no_timestamp", action="store_true",
                        help="使用原始文件名（默认使用时间戳命名）")

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
            expire_days=args.expire_days,
            use_timestamp_name=not args.no_timestamp
        )
        if url:
            print("\n📎 {}".format(url))
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
