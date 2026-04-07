#!/usr/bin/env python3
"""
Preview Host - 文件上传与预览链接生成
支持自动检测服务状态、纯时间戳命名、过期清理

使用方式：
  python3 preview_host.py upload <本地路径> [子目录] [选项]
  python3 preview_host.py status
  python3 preview_host.py list
  python3 preview_host.py delete <random_name>

选项：
  --expire-days 天数     设置过期天数（可选，不设置则永不过期）
"""
import os, sys, uuid, subprocess, argparse, tempfile, time, json

HOST          = os.environ.get("PREVIEW_HOST", "")
SSH_USER      = os.environ.get("PREVIEW_SSH_USER", "root")
SSH_KEY_PATH  = None   # 延迟初始化
FILE_ROOT     = "/software/southxs-preview/files"
DNSPOD_DOMAIN = os.environ.get("DNSPOD_DOMAIN", "")
DNSPOD_SUB_DOMAIN = os.environ.get("DNSPOD_SUB_DOMAIN", "preview")
DEFAULT_URL   = "https://{}.{}".format(DNSPOD_SUB_DOMAIN, DNSPOD_DOMAIN) if DNSPOD_DOMAIN else ""

_rate_counts = {}

def _get_key():
    global SSH_KEY_PATH
    if SSH_KEY_PATH:
        return SSH_KEY_PATH
    key = os.environ.get("PREVIEW_SSH_KEY", "")
    if not key:
        return None
    if key.strip().startswith("-----BEGIN"):
        fd, path = tempfile.mkstemp(prefix="ssh_key_")
        os.write(fd, key.encode())
        os.close(fd)
        os.chmod(path, 0o600)
        SSH_KEY_PATH = path
    else:
        SSH_KEY_PATH = key
    return SSH_KEY_PATH

def cmd(c, check=True):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, executable="/bin/bash")
    if check and r.returncode != 0:
        return None
    return r

def ssh_cmd(c, check=True, timeout=30):
    key = _get_key()
    key_opt = "-i {}".format(key) if key else ""
    r = subprocess.run(
        "ssh {} -o StrictHostKeyChecking=no -o ConnectTimeout=10 {}@{} {}".format(
            key_opt, SSH_USER, HOST, c.replace('"', '\\"') if '"' in c else c),
        shell=True, capture_output=True, text=True, executable="/bin/bash", timeout=timeout)
    if check and r.returncode != 0:
        return None
    return r

def check_service_status():
    print("🔍 检查服务状态...")
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量: PREVIEW_HOST, PREVIEW_SSH_USER")
        return False, False, False, False
    r = ssh_cmd("echo OK", check=False, timeout=10)
    if r is None:
        print("❌ SSH 连接失败")
        return False, False, False, False
    print("  ✅ SSH 连接正常")

    r = ssh_cmd("docker --version 2>/dev/null && echo OK || echo FAIL", check=False, timeout=10)
    docker_ok = r and "OK" in r.stdout
    print("  {} Docker".format("✅" if docker_ok else "❌"))

    r = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:81/ 2>/dev/null", check=False, timeout=10)
    npm_ok = r and r.stdout.strip() == "200"
    print("  {} Nginx Proxy Manager (http://{}:81)".format("✅" if npm_ok else "❌", HOST if npm_ok else HOST))

    r = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/ 2>/dev/null", check=False, timeout=10)
    preview_ok = r and r.stdout.strip() == "200"
    print("  {} 预览服务 (http://{}:8081)".format("✅" if preview_ok else "❌", HOST if preview_ok else HOST))

    if DNSPOD_DOMAIN:
        dns_url = "https://{}.{}/".format(DNSPOD_SUB_DOMAIN, DNSPOD_DOMAIN)
        r = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' " + dns_url + " 2>/dev/null", check=False, timeout=10)
        dns_ok = r and r.stdout.strip() in ("200", "301", "302")
        print("  {} DNS/SSL ({})".format("✅" if dns_ok else "⚠️  未配置", dns_url))
    else:
        dns_ok = False
    return docker_ok, npm_ok, preview_ok, dns_ok


def auto_setup():
    print("\n🚀 检测到服务缺失，启动自动初始化...")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    setup_path = os.path.join(script_dir, "setup.py")
    if not os.path.exists(setup_path):
        print("❌ setup.py 不存在，无法自动初始化")
        return False
    spec = importlib.util.spec_from_file_location("setup_module", setup_path)
    setup_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup_module)
    docker_ok, npm_ok, preview_ok, dns_ok = check_service_status()
    steps = [("SSH 连接", setup_module.step_ssh)]
    if not dns_ok:
        steps.append(("DNS 配置", setup_module.step_dns))
    steps += [
        ("目录创建",  setup_module.step_dirs),
    ]
    if not docker_ok:
        steps.append(("Docker 安装", setup_module.step_docker))
    if not npm_ok:
        steps.append(("NPM 部署",   setup_module.step_npm))
        steps.append(("NPM 代理+SSL", setup_module.step_npm_proxy))
    if not preview_ok:
        steps.append(("预览服务",   setup_module.step_preview))
    failed = False
    for name, fn in steps:
        print("\n" + "=" * 50 + "\n  {}\n".format(name) + "=" * 50)
        if not fn():
            print("\n❌ 初始化失败: {}".format(name))
            failed = True
            break
    if not failed:
        print("\n✅ 自动初始化完成！")
    print("\n📋 初始化后状态：")
    check_service_status()
    return not failed


# ------------------ 文件上传 ------------------

def _do_sql(sql, args=None):
    """在容器内执行 SQL（通过 stdin 传入脚本），返回 (success, error_msg)"""
    key = _get_key()
    key_opt = "-i {}".format(key) if key else ""

    # 生成 Python 脚本内容
    lines = [
        "import sqlite3",
        "conn=sqlite3.connect('/data/preview.db')",
        "conn.execute('CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, random_name TEXT UNIQUE, original_name TEXT, file_path TEXT, size INTEGER, password_hash TEXT, upload_time INTEGER, expire_time INTEGER, access_count INTEGER DEFAULT 0, ip TEXT)')",
    ]
    if sql.strip():
        if args:
            lines.append("conn.execute({}, {})".format(repr(sql), repr(args)))
        else:
            lines.append("conn.execute({})".format(repr(sql)))
    lines.append("conn.commit()")
    lines.append("conn.close()")
    script = "\n".join(lines)

    # 通过 stdin 传给 docker exec（避免容器内 /tmp/ 不可见的问题）
    cmd = f'ssh {key_opt} -o StrictHostKeyChecking=no {SSH_USER}@{HOST} "docker exec -i southxs-preview python3 -"'
    r = subprocess.run(cmd, shell=True, input=script, capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return False, r.stderr.strip()[:100]
    return True, ""


def _insert_row(timestamp_name, original_name, file_path, size, expire_days):
    now = int(time.time())
    expire_ts = now + expire_days * 86400 if expire_days else None
    fid = str(uuid.uuid4())
    sql = ("INSERT INTO files (id,random_name,original_name,file_path,size,password_hash,upload_time,expire_time,ip) "
           "VALUES (?,?,?,?,?,'',?,?,'')")
    args = (fid, timestamp_name, original_name, file_path, size or 0, now, expire_ts)
    return _do_sql(sql, args)


def upload(local_path, remote_subdir="", expire_days=None):
    """上传文件，返回预览链接"""
    if not HOST or not SSH_USER:
        print("❌ 缺少必要环境变量")
        return None

    raw_name = os.path.basename(local_path.rstrip("/"))
    if not raw_name:
        print("❌ 无效的路径")
        return None

    # 纯时间戳命名：yyyy-MM-dd-HH-mm-ss
    ts = time.strftime("%Y-%m-%d-%H-%M-%S", time.localtime())
    ext = os.path.splitext(raw_name)[1]
    timestamp_name = "{}{}".format(ts, ext)
    remote_dir = "{}/{}".format(FILE_ROOT, remote_subdir).rstrip("/")
    container_path = "/data/files/{}".format(timestamp_name)

    local_size = 0
    if os.path.isfile(local_path):
        local_size = os.path.getsize(local_path)
    elif os.path.isdir(local_path):
        local_size = sum(f.stat().st_size for f in os.scandir(local_path) if f.is_file())

    # 初始化 DB（如不存在）
    _do_sql("", None)

    key = _get_key()
    key_opt = "-i {}".format(key) if key else ""

    # SCP 上传
    ssh_cmd("mkdir -p {}".format(remote_dir), check=False)
    if os.path.isdir(local_path):
        tmp_dir = remote_dir + "/.tmp_" + uuid.uuid4().hex[:8]
        ssh_cmd("mkdir -p {}".format(tmp_dir), check=False)
        r = cmd('rsync -avz -e "ssh {} -o StrictHostKeyChecking=no" {}/ {}@{}:{}/'.format(
            key_opt, local_path, SSH_USER, HOST, tmp_dir), check=False)
        if r and r.returncode == 0:
            ssh_cmd("cp -r {}/* {}/ && rm -rf {}".format(tmp_dir, remote_dir, tmp_dir), check=False)
    else:
        r = cmd('scp {} -o StrictHostKeyChecking=no "{}" {}@{}:"{}/{}"'.format(
            key_opt, local_path, SSH_USER, HOST, remote_dir, timestamp_name), check=False)

    if r is None:
        print("❌ 上传失败")
        return None

    # 写入元数据
    ok, err = _insert_row(timestamp_name, raw_name, container_path, local_size, expire_days)
    if not ok:
        print("   ⚠️  元数据写入失败，但文件已上传")

    url = "{}/{}".format(DEFAULT_URL, timestamp_name) if DEFAULT_URL else None
    print("✅ 上传成功")
    if expire_days:
        print("   ⏰ 过期时间: {} 天后".format(expire_days))
    if url:
        print("   预览: {}".format(url))
    else:
        print("   文件: {}/{}".format(remote_dir, timestamp_name))
    return url


def list_files():
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量")
        return
    key = _get_key()
    kopt = f"-i {key}" if key else ""
    script = (
        "import sqlite3,time\n"
        "conn=sqlite3.connect('/data/preview.db')\n"
        "rows=conn.execute('SELECT random_name,original_name,size,upload_time,expire_time FROM files ORDER BY upload_time DESC').fetchall()\n"
        "for r in rows:\n"
        " et='永不过期' if not r[4] else time.strftime('%Y-%m-%d %H:%M',time.localtime(r[4]))\n"
        " ut=time.strftime('%Y-%m-%d %H:%M',time.localtime(r[3]))\n"
        " print('%-40s %-30s %8s %s %s' % (r[0],r[1],str(r[2] or 0)+'B',ut,et))"
    )
    r = subprocess.run(
        f'ssh {kopt} -o StrictHostKeyChecking=no {SSH_USER}@{HOST} "docker exec -i southxs-preview python3 -"',
        shell=True, input=script, capture_output=True, text=True, timeout=15)
    print("\n📋 远程文件列表：")
    print("-" * 100)
    if r.returncode == 0 and r.stdout.strip():
        print(r.stdout)
    else:
        print("  （暂无文件）")


def delete_file(random_name):
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量")
        return False

    key = _get_key()
    kopt = f"-i {key}" if key else ""

    sel_script = (
        "import sqlite3\n"
        "conn=sqlite3.connect('/data/preview.db')\n"
        "row=conn.execute('SELECT file_path FROM files WHERE random_name=?',('{}',)).fetchone()\n"
        "if row: print(row[0])\n"
    ).format(random_name)
    r = subprocess.run(
        f'ssh {kopt} -o StrictHostKeyChecking=no {SSH_USER}@{HOST} "docker exec -i southxs-preview python3 -"',
        shell=True, input=sel_script, capture_output=True, text=True, timeout=15)
    file_path = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else ""
    if file_path:
        ssh_cmd(f"docker exec southxs-preview rm -f '{file_path}'", check=False)

    del_script = (
        "import sqlite3\n"
        "conn=sqlite3.connect('/data/preview.db')\n"
        "conn.execute('DELETE FROM files WHERE random_name=?',('{}',))\n"
        "conn.commit()"
    ).format(random_name)
    subprocess.run(
        f'ssh {kopt} -o StrictHostKeyChecking=no {SSH_USER}@{HOST} "docker exec -i southxs-preview python3 -"',
        shell=True, input=del_script, capture_output=True, text=True, timeout=15)

    print(f"✅ 已删除: {random_name}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Preview Host 上传工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python3 preview_host.py upload /tmp/readme.md
  python3 preview_host.py upload /tmp/readme.md projects/
  python3 preview_host.py upload /tmp/readme.md --expire-days 7
  python3 preview_host.py list
  python3 preview_host.py delete 2026-04-07-19-52-00.md
        """)
    parser.add_argument("action", choices=["upload","status","setup","list","delete"],
                        help="操作")
    parser.add_argument("local", nargs="?", default="", help="本地路径")
    parser.add_argument("remote", nargs="?", default="", help="远程子目录")
    parser.add_argument("--expire-days", dest="expire_days", type=int, default=None,
                        help="过期天数")

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
        docker_ok, npm_ok, preview_ok, dns_ok = check_service_status()
        if not all([docker_ok, npm_ok, preview_ok]):
            print("")
            if not auto_setup():
                print("⚠️  自动初始化未完成，上传可能失败")
        url = upload(args.local, args.remote, expire_days=args.expire_days)
        if url:
            print("\n📎 {}".format(url))
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
