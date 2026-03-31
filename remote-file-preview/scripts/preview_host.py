#!/usr/bin/env python3
"""
Preview Host - 文件上传与预览链接生成
支持自动检测服务状态，缺失时自动初始化

使用方式：
  python3 preview_host.py upload <本地路径> [远程子目录]
  python3 preview_host.py status   # 检查服务状态
"""
import os
import sys
import subprocess
import argparse
import importlib.util
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


def cmd(c, check=True):
    r = subprocess.run(c, shell=True, capture_output=True, text=True, executable="/bin/bash")
    if check and r.returncode != 0:
        return None
    return r


def ssh_cmd(c, check=True, timeout=30):
    key_opt = "-i {}".format(SSH_KEY) if SSH_KEY else ""
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

def upload(local_path, remote_subdir=""):
    """上传文件/目录，返回预览链接"""
    if not HOST or not SSH_USER:
        print("❌ 缺少必要环境变量: PREVIEW_HOST, PREVIEW_SSH_USER")
        return None

    # 提取原始文件名（仅文件名，不含路径）
    raw_name = os.path.basename(local_path.rstrip("/"))
    if not raw_name:
        print("❌ 无效的路径: {}".format(local_path))
        return None

    remote_dir = "{}/{}".format(FILE_ROOT, remote_subdir).rstrip("/")
    remote_path = "{}/{}".format(remote_dir, raw_name)

    # 创建目录
    ssh_cmd("mkdir -p {}".format(remote_dir), check=False)

    key_opt = "-i {}".format(SSH_KEY) if SSH_KEY else ""
    if os.path.isdir(local_path):
        r = cmd('rsync -avz -e "ssh {} -o StrictHostKeyChecking=no" {}/ {}@{}:{}/'.format(
            key_opt, local_path, SSH_USER, HOST, remote_dir))
    else:
        r = cmd('scp {} -o StrictHostKeyChecking=no {} {}@{}:{}/'.format(
            key_opt, local_path, SSH_USER, HOST, remote_dir))

    if r is None:
        return None

    # 验证服务器端实际文件名（防止下划线等字符被 shell/rsync 改变）
    check = ssh_cmd("ls -1 '{}' 2>/dev/null || ls -1 {}".format(
        remote_path, remote_path), check=False, timeout=10)
    actual_name = check.stdout.strip().split('\n')[0] if check and check.returncode == 0 else None

    if actual_name and actual_name != raw_name:
        print("⚠️  文件名疑似改变：")
        print("   原始文件名: {}".format(raw_name))
        print("   服务器文件名: {}".format(actual_name))
        # 使用服务器实际文件名生成链接
        name = actual_name
    elif actual_name:
        name = actual_name
    else:
        name = raw_name

    # 生成链接（文件名 URL 编码，防止下划线被 markdown 解析）
    encoded_name = quote(name, safe='/')
    encoded_subdir = quote(remote_subdir, safe='/') if remote_subdir else ''
    path = "{}/{}".format(encoded_subdir, encoded_name) if remote_subdir else encoded_name
    url = "{}/{}".format(DEFAULT_URL, path) if DEFAULT_URL else None

    print("✅ 上传成功")
    if url:
        print("   预览: {}".format(url))
    else:
        print("   文件已上传到: {}/{}".format(remote_dir, name))
    return url


# ------------------ 主入口 ------------------

def main():
    parser = argparse.ArgumentParser(description="Preview Host 上传工具")
    parser.add_argument("action", choices=["upload", "status", "setup"], help="操作")
    parser.add_argument("local", nargs="?", default="", help="本地路径（upload 时用）")
    parser.add_argument("remote", nargs="?", default="", help="远程子目录")

    args = parser.parse_args()

    if args.action == "status":
        check_service_status()

    elif args.action == "setup":
        auto_setup()

    elif args.action == "upload":
        if not args.local:
            print("❌ 请指定要上传的文件或目录")
            sys.exit(1)

        # 先检查状态
        docker_ok, npm_ok, preview_ok, dns_ok = check_service_status()

        # 如果有任何服务缺失，自动初始化
        if not all([docker_ok, npm_ok, preview_ok]):
            print("")
            if not auto_setup():
                print("⚠️  自动初始化未完成，上传可能失败")

        # 执行上传
        url = upload(args.local, args.remote)
        if url:
            print("\n📎 {}".format(url))
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
