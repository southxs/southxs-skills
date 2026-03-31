#!/usr/bin/env python3
"""
Preview Host - 服务器初始化脚本
阶段：SSH → DNS → 目录 → Docker → NPM → 预览服务 → NPM代理配置
"""
import os, sys, subprocess, json, time, argparse, urllib.request, hashlib, hmac

# ------------------ 凭证（环境变量）------------------

HOST          = os.environ.get("PREVIEW_HOST", "")
SSH_USER      = os.environ.get("PREVIEW_SSH_USER", "root")
SSH_KEY       = os.environ.get("PREVIEW_SSH_KEY", "")
NPM_URL       = os.environ.get("PREVIEW_NPM_URL", "http://127.0.0.1:81")
NPM_USER      = os.environ.get("PREVIEW_NPM_USER", "")
NPM_PASS      = os.environ.get("PREVIEW_NPM_PASS", "")
DNSPOD_DOMAIN = os.environ.get("DNSPOD_DOMAIN", "")
DNSPOD_SUB_DOMAIN = os.environ.get("DNSPOD_SUB_DOMAIN", "preview")
TENCENTCLOUD_SECRET_ID     = os.environ.get("TENCENTCLOUD_SECRET_ID", "")
TENCENTCLOUD_SECRET_KEY    = os.environ.get("TENCENTCLOUD_SECRET_KEY", "")
PREVIEW_DOMAIN = "{}.{}".format(DNSPOD_SUB_DOMAIN, DNSPOD_DOMAIN)

# NPM 容器名/镜像
NPM_CONTAINER = "npm"
NPM_IMAGE     = "jc21/nginx-proxy-manager:latest"
PREVIEW_IMAGE = "southxs-preview"

# ------------------ 辅助函数 ------------------

def cmd(c, check=True):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, executable="/bin/bash")
    if check and r.returncode != 0:
        print("❌ 本地命令失败: {}".format(c))
        print("   stderr: {}".format(r.stderr.strip()[:200]))
        return None
    return r

def ssh_cmd(c, check=True, timeout=30):
    key_opt = "-i {}".format(SSH_KEY) if SSH_KEY else ""
    r = subprocess.run(
        "ssh {} -o StrictHostKeyChecking=no -o ConnectTimeout=10 {}@{} \"{}\"".format(
            key_opt, SSH_USER, HOST, c.replace('"', '\\"') if '"' in c else c
        ),
        shell=True, capture_output=True, text=True, executable="/bin/bash",
        timeout=timeout
    )
    if check and r.returncode != 0:
        return None
    return r

def scp_upload(local_path, remote_path):
    key_opt = "-i {}".format(SSH_KEY) if SSH_KEY else ""
    return cmd("scp {} -o StrictHostKeyChecking=no -r {} {}@{}:{}".format(
        key_opt, local_path, SSH_USER, HOST, remote_path), check=True)


# ------------------ DNSPod API (TC3-HMAC-SHA256) ------------------

DNSPOD_HOST     = "dnspod.tencentcloudapi.com"
DNSPOD_SERVICE  = "dnspod"
DNSPOD_VERSION  = "2021-03-23"

def _sha256(s):
    return hashlib.sha256(s.encode("utf-8")).digest()

def _hex_sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _tc3_sign(secret_key, date, service, string_to_sign):
    def _hmac256(k, m): return hmac.new(k, m.encode("utf-8"), hashlib.sha256).digest()
    sk = _hmac256(("TC3" + secret_key).encode("utf-8"), date)
    ss = _hmac256(sk, service)
    sb = _hmac256(ss, "tc3_request")
    return hmac.new(sb, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

def dnspod_call(action, params):
    if not TENCENTCLOUD_SECRET_ID or not TENCENTCLOUD_SECRET_KEY:
        return None

    ts = str(int(time.time()))
    date = time.strftime("%Y-%m-%d", time.gmtime(int(ts)))

    # body 参数（不含 Action/Version）
    body = {"SecretId": TENCENTCLOUD_SECRET_ID, "Timestamp": ts,
            "Nonce": str(__import__("random").randint(10000, 99999)),
            "Region": "", **params}
    body_json = json.dumps(body, separators=(",", ":"), sort_keys=True)

    hashed_payload = _hex_sha256(body_json)
    canonical_hdrs = "content-type:application/json\nhost:{}\n".format(DNSPOD_HOST)
    canonical_request = "POST\n/\n\n" + canonical_hdrs + "content-type;host\n" + hashed_payload
    hashed_cr = _hex_sha256(canonical_request)
    string_to_sign = "TC3-HMAC-SHA256\n" + ts + "\n" + date + "/" + DNSPOD_SERVICE + "/tc3_request\n" + hashed_cr
    signature = _tc3_sign(TENCENTCLOUD_SECRET_KEY, date, DNSPOD_SERVICE, string_to_sign)

    authorization = ("TC3-HMAC-SHA256 "
        "Credential={}/{}/{}/tc3_request, ".format(TENCENTCLOUD_SECRET_ID, date, DNSPOD_SERVICE)
        + "SignedHeaders=content-type;host, Signature={}".format(signature))

    headers = {
        "Content-Type": "application/json", "Host": DNSPOD_HOST,
        "X-TC-Action": action, "X-TC-Timestamp": ts,
        "X-TC-Version": DNSPOD_VERSION, "Authorization": authorization,
    }

    try:
        req = urllib.request.Request(
            "https://" + DNSPOD_HOST + "/",
            data=body_json.encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("Response", result)
    except Exception as e:
        print("   API 错误: {}".format(e))
        return None


# ------------------ 阶段1：SSH 连接 ------------------

def step_ssh():
    print("\n" + "=" * 50)
    print("阶段1：SSH 连接测试")
    print("=" * 50)
    if not HOST or not SSH_USER:
        print("❌ 缺少环境变量: PREVIEW_HOST, PREVIEW_SSH_USER")
        return False
    r = ssh_cmd("echo OK && hostname && uname -a", check=False)
    if r is None:
        print("❌ SSH 连接失败")
        return False
    print("✅ SSH 成功: {}".format(r.stdout.strip().split('\n')[-1]))
    return True


# ------------------ 阶段2：DNS 配置 ------------------

def step_dns():
    print("\n" + "=" * 50)
    print("阶段2：DNS 解析配置（腾讯云 DNSPod）")
    print("=" * 50)
    if not TENCENTCLOUD_SECRET_ID or not TENCENTCLOUD_SECRET_KEY:
        print("⚠️  未配置腾讯云凭证，跳过 DNS 配置")
        print("   请手动在 DNSPod 控制台添加 A 记录: {} -> {}".format(PREVIEW_DOMAIN, HOST))
        return True

    # 查询
    resp = dnspod_call("DescribeRecordList", {"Domain": DNSPOD_DOMAIN, "SubDomain": DNSPOD_SUB_DOMAIN})
    records = resp.get("RecordList", []) if resp else []

    if records:
        record = records[0]
        rid = record["RecordId"]
        current_ip = record.get("Value", "")
        if current_ip == HOST:
            print("✅ DNS 记录已存在且正确: {} -> {}".format(PREVIEW_DOMAIN, HOST))
            return True
        # 更新
        resp = dnspod_call("ModifyRecord", {
            "Domain": DNSPOD_DOMAIN, "RecordId": rid,
            "SubDomain": DNSPOD_SUB_DOMAIN, "RecordType": "A",
            "RecordLine": "默认", "Value": HOST
        })
        if resp and resp.get("RequestId"):
            print("✅ DNS 记录已更新: {} -> {}".format(PREVIEW_DOMAIN, HOST))
        else:
            print("❌ DNS 更新失败，请手动修改")
            return False
    else:
        resp = dnspod_call("CreateRecord", {
            "Domain": DNSPOD_DOMAIN, "SubDomain": DNSPOD_SUB_DOMAIN,
            "RecordType": "A", "RecordLine": "默认", "Value": HOST
        })
        if resp and resp.get("RecordId"):
            print("✅ DNS 记录已创建: {} -> {}".format(PREVIEW_DOMAIN, HOST))
        else:
            print("❌ DNS 创建失败，请手动在 DNSPod 控制台添加")
            return False

    print("⏳ DNS 生效通常需要 1-10 分钟")
    return True


# ------------------ 阶段3：目录创建 ------------------

def step_dirs():
    print("\n" + "=" * 50)
    print("阶段3：创建目录结构")
    print("=" * 50)
    dirs = ["/software/southxs-preview", "/software/southxs-preview/app",
            "/software/southxs-preview/files", "/software/southxs-preview/app/npm",
            "/software/southxs-preview/app/preview"]
    for d in dirs:
        r = ssh_cmd("mkdir -p {} && echo OK".format(d), check=False)
        print("  {} {}".format("✅" if r and r.returncode == 0 else "❌", d))
    return True


# ------------------ 阶段4：Docker 安装 ------------------

def step_docker():
    print("\n" + "=" * 50)
    print("阶段4：Docker 安装")
    print("=" * 50)
    r = ssh_cmd("docker --version 2>/dev/null", check=False)
    if r and r.returncode == 0:
        print("✅ Docker 已安装: {}".format(r.stdout.strip()))
        return True

    print("📦 安装 Docker (docker.io)...")
    script = [
        "export DEBIAN_FRONTEND=noninteractive",
        "apt-get update -qq",
        "apt-get install -y -qq docker.io docker-compose > /dev/null 2>&1",
        "systemctl enable docker",
        "systemctl start docker",
        "docker --version"
    ]
    # 配置国内镜像
    ssh_cmd("mkdir -p /etc/docker && cat > /etc/docker/daemon.json << 'EOF'\n{\n  \"registry-mirrors\": [\"https://mirror.ccs.tencentyun.com\"]\n}\nEOF\nsystemctl daemon-reload && systemctl restart docker", check=False)
    for s in script:
        ssh_cmd(s, check=False)
    r = ssh_cmd("docker --version", check=False)
    if r and r.returncode == 0:
        print("✅ Docker 安装成功: {}".format(r.stdout.strip()))
        return True
    print("❌ Docker 安装失败，请手动安装")
    return False


# ------------------ 阶段5：NPM 部署（docker run 方式） ------------------

def step_npm():
    print("\n" + "=" * 50)
    print("阶段5：Nginx Proxy Manager 部署")
    print("=" * 50)

    # 检查是否已运行
    r = ssh_cmd("docker ps --format '{{.Names}}' | grep -w npm", check=False)
    if r and "npm" in r.stdout:
        print("✅ NPM 已运行")
        return True

    # 检查容器是否存在（stopped 状态）
    r = ssh_cmd("docker ps -a --format '{{.Names}}' | grep -w npm", check=False)
    if r and "npm" in r.stdout:
        print("📦 NPM 容器已存在，启动中...")
        ssh_cmd("docker start npm", check=False)
        time.sleep(10)
        # 检查是否正常
        r2 = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:81/", check=False)
        if r2 and r2.stdout.strip() == "200":
            print("  ✅ NPM 运行正常")
            return True
        else:
            print("  ⚠️  NPM 未正常响应，继续...")

    # 使用 docker run 创建（避免 docker-compose 兼容性问题）
    # 先配置 NPM 数据目录
    ssh_cmd("mkdir -p /software/southxs-preview/app/npm/data /software/southxs-preview/app/npm/letsencrypt", check=False)

    env_opts = ""
    if NPM_USER and NPM_PASS:
        env_opts = "-e INITIAL_ADMIN_EMAIL={} -e INITIAL_ADMIN_PASSWORD={}".format(
            NPM_USER, NPM_PASS)

    npm_cmd = (
        "docker run -d --name {} --restart unless-stopped "
        "--network host "
        "-v /software/southxs-preview/app/npm/data:/data "
        "-v /software/southxs-preview/app/npm/letsencrypt:/etc/letsencrypt "
        "{} "
        "{}".format(NPM_CONTAINER, env_opts, NPM_IMAGE)
    )

    print("📦 启动 NPM 容器...")
    r = ssh_cmd(npm_cmd, check=False)
    if r and r.returncode == 0:
        print("✅ NPM 容器启动中...")
        time.sleep(15)
        # 验证
        r2 = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:81/", check=False)
        if r2 and r2.stdout.strip() == "200":
            print("✅ NPM 运行正常 (http://127.0.0.1:81)")
            return True
        else:
            print("⚠️  NPM 启动延迟，等待更多时间...")
            time.sleep(15)
            return True
    else:
        print("❌ NPM 容器启动失败: {}".format(r.stderr if r else "unknown"))
        return False


# ------------------ 阶段6：NPM 代理+SSL 配置 ------------------

def step_npm_proxy():
    print("\n" + "=" * 50)
    print("阶段6：NPM 代理主机与 SSL 证书配置")
    print("=" * 50)
    if not NPM_USER or not NPM_PASS:
        print("⚠️  未配置 NPM 凭证，请手动配置:")
        print("   访问 http://{}:81 创建管理员账号".format(HOST))
        print("   然后添加代理主机和 SSL 证书")
        return True

    # 1. 获取 Token
    try:
        req = urllib.request.Request(
            NPM_URL + "/api/tokens",
            data=json.dumps({"identity": NPM_USER, "secret": NPM_PASS}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            token_data = json.loads(resp.read())
            token = token_data.get("token", "")
        if not token:
            print("❌ NPM Token 获取失败: {}".format(token_data))
            return False
        print("  ✅ NPM 登录成功")
    except Exception as e:
        print("❌ NPM 登录失败: {}".format(e))
        return False

    hdrs = {"Content-Type": "application/json", "Authorization": "Bearer " + token}

    def api_post(path, data):
        try:
            req = urllib.request.Request(
                NPM_URL + path,
                data=json.dumps(data).encode(), headers=hdrs, method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print("  ⚠️  API 错误 {}: {}".format(path, e))
            return None

    def api_get(path):
        try:
            req = urllib.request.Request(
                NPM_URL + path,
                headers=hdrs, method="GET"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print("  ⚠️  API GET 错误 {}: {}".format(path, e))
            return []

    def api_put(path, data):
        try:
            req = urllib.request.Request(
                NPM_URL + path,
                data=json.dumps(data).encode(), headers=hdrs, method="PUT"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            print("  ⚠️  API 错误 {}: {}".format(path, e))
            return None

    # 2. 申请 Let's Encrypt 证书
    print("  📝 申请 SSL 证书: {}".format(PREVIEW_DOMAIN))
    cert = api_post("/api/nginx/certificates", {
        "provider": "letsencrypt",
        "nice_name": PREVIEW_DOMAIN,
        "domain_names": [PREVIEW_DOMAIN],
        "meta": {"dns_challenge": False, "letsencrypt_email": NPM_USER}
    })
    cert_id = cert.get("id") if cert else None
    if cert_id:
        print("  ✅ 证书已创建: ID={}".format(cert_id))
    else:
        print("  ⚠️  证书创建失败或已存在，继续...")
        cert_id = None

    # 3. 检查是否已有同名代理主机
    print("  📋 检查已有代理主机...")
    existing = api_get("/api/nginx/proxy-hosts")
    proxy_id = None
    if existing:
        for p in existing:
            if PREVIEW_DOMAIN in p.get("domain_names", []):
                proxy_id = p.get("id")
                print("  ✅ 代理主机已存在: ID={}".format(proxy_id))
                break

    if not proxy_id:
        print("  📝 创建代理主机: {} -> 127.0.0.1:8081".format(PREVIEW_DOMAIN))
        proxy = api_post("/api/nginx/proxy-hosts", {
            "domain_names": [PREVIEW_DOMAIN],
            "forward_scheme": "http",
            "forward_host": "127.0.0.1",
            "forward_port": 8081,
            "enabled": True
        })
        if proxy and proxy.get("id"):
            proxy_id = proxy["id"]
            print("  ✅ 代理主机已创建: ID={}".format(proxy_id))
        else:
            print("  ⚠️  代理主机创建失败，请手动在 NPM 控制台配置")
            return True

    # 4. 绑定 SSL
    if cert_id:
        print("  📝 绑定 SSL 证书...")
        result = api_put("/api/nginx/proxy-hosts/{}".format(proxy_id), {
            "domain_names": [PREVIEW_DOMAIN],
            "forward_scheme": "http",
            "forward_host": "127.0.0.1",
            "forward_port": 8081,
            "certificate_id": cert_id,
            "ssl_forced": True,
            "enabled": True
        })
        if result:
            print("  ✅ SSL 已绑定")
        else:
            print("  ⚠️  SSL 绑定失败，请手动在 NPM 控制台配置")

    return True


# ------------------ 阶段7：预览服务部署 ------------------

def step_preview():
    print("\n" + "=" * 50)
    print("阶段7：预览服务部署")
    print("=" * 50)

    # 检查是否已运行
    r = ssh_cmd("docker ps --format '{{.Names}}' | grep -w southxs-preview", check=False)
    if r and "southxs-preview" in r.stdout:
        print("✅ 预览服务已运行")
        return True

    # 检查镜像是否存在
    r = ssh_cmd("docker images --format '{{.Repository}}' | grep -w southxs-preview", check=False)
    has_image = r and "southxs-preview" in r.stdout

    if not has_image:
        # 构建镜像（需要 Python 镜像先拉取）
        print("📦 首次构建预览服务镜像（需要下载 Python 基础镜像，可能较慢）...")
        ssh_cmd("docker pull python:3.12-slim", check=False)
        ssh_cmd("docker build -t southxs-preview /software/southxs-preview/app/preview/", check=False)

    # 启动容器
    ssh_cmd("docker rm -f southxs-preview 2>/dev/null", check=False)
    preview_cmd = (
        "docker run -d --name southxs-preview --restart unless-stopped "
        "--network host "
        "-v /software/southxs-preview/files:/data/files "
        "-e PREVIEW_FILES_DIR=/data/files "
        "-e PREVIEW_PORT=8081 "
        "southxs-preview"
    )
    r = ssh_cmd(preview_cmd, check=False)
    if r and r.returncode == 0:
        time.sleep(3)
        r2 = ssh_cmd("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/", check=False)
        if r2 and r2.stdout.strip() == "200":
            print("✅ 预览服务部署成功: http://{}:8081".format(HOST))
        else:
            print("✅ 预览服务已启动（验证延迟，稍后测试）")
        return True
    else:
        print("❌ 预览服务启动失败")
        return False


# ------------------ 主流程 ------------------

def main():
    parser = argparse.ArgumentParser(description="Preview Host 服务器初始化")
    parser.add_argument("--skip-dns", action="store_true", help="跳过 DNS 配置")
    parser.add_argument("--skip-npm", action="store_true", help="跳过 NPM 配置")
    parser.add_argument("--skip-preview", action="store_true", help="跳过预览服务")
    args = parser.parse_args()

    if not HOST or not SSH_USER:
        print("❌ 缺少必要环境变量: PREVIEW_HOST, PREVIEW_SSH_USER")
        sys.exit(1)

    print("""
╔══════════════════════════════════════════════════╗
║     Preview Host - 服务器初始化                   ║
╠══════════════════════════════════════════════════╣
║  服务器:        {}
║  预览域名:       {}
║  文件目录:       /software/southxs-preview/files
╚══════════════════════════════════════════════════╝
""".format(HOST, PREVIEW_DOMAIN))

    steps = [
        ("SSH 连接",    step_ssh),
    ]
    if not args.skip_dns:
        steps.append(("DNS 配置", step_dns))
    steps += [
        ("目录创建",    step_dirs),
        ("Docker 安装", step_docker),
        ("NPM 部署",   step_npm),
    ]
    if not args.skip_npm:
        steps.append(("NPM 代理+SSL", step_npm_proxy))
    if not args.skip_preview:
        steps.append(("预览服务",   step_preview))

    failed = False
    for name, fn in steps:
        if not fn():
            print("\n❌ 阶段失败: {}".format(name))
            failed = True
            break

    if failed:
        sys.exit(1)

    print("""
╔══════════════════════════════════════════════════╗
║     ✅ 初始化完成！                             ║
╠══════════════════════════════════════════════════╣
║  预览地址: https://{}
║  文件目录: /software/southxs-preview/files
║  NPM 管理: http://{}:81
╚══════════════════════════════════════════════════╝
""".format(PREVIEW_DOMAIN, HOST))


if __name__ == "__main__":
    main()
