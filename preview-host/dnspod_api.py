#!/usr/bin/env python3
"""
DNSPod API - DNS记录管理（腾讯云 API 3.0 - TC3-HMAC-SHA256 签名）
读取环境变量: TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY, DNSPOD_DOMAIN

关键：DNSPod API 签名规则
- 公共参数(SecretId/Timestamp/Nonce/Region/Version/Action) 放 Query String
- 业务参数放 Body (JSON)
- Body 为空时 hashed_payload = sha256('')
"""
import os, sys, json, argparse, urllib.request, urllib.parse, hashlib, hmac, time, random

SECRET_ID    = os.environ.get("TENCENTCLOUD_SECRET_ID", "")
SECRET_KEY   = os.environ.get("TENCENTCLOUD_SECRET_KEY", "")
DOMAIN       = os.environ.get("DNSPOD_DOMAIN", "")

API_HOST     = "dnspod.tencentcloudapi.com"
API_SERVICE  = "dnspod"
API_VERSION  = "2021-03-23"


def _sign(secret_key, date, service, string_to_sign):
    def _hmac256(k, m):
        return hmac.new(k, m.encode("utf-8"), hashlib.sha256).digest()
    sk = _hmac256(("TC3" + secret_key).encode("utf-8"), date)
    ss = _hmac256(sk, service)
    sb = _hmac256(ss, "tc3_request")
    return hmac.new(sb, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def dnspod_call(action, params):
    """
    调用 DNSPod API（TC3-HMAC-SHA256 签名）
    公共参数放 Query String，业务参数放 Body
    """
    if not SECRET_ID or not SECRET_KEY:
        print("❌ 缺少环境变量: TENCENTCLOUD_SECRET_ID, TENCENTCLOUD_SECRET_KEY")
        return None

    timestamp = str(int(time.time()))
    date = time.strftime("%Y-%m-%d", time.gmtime(int(timestamp)))

    # Query String: 公共参数
    common_params = {
        "SecretId": SECRET_ID,
        "Timestamp": timestamp,
        "Nonce": str(random.randint(10000, 99999)),
        "Region": "",
        "Version": API_VERSION,
        "Action": action,
    }
    # DNSPod API 要求 Domain 同时在 query 和 body 中
    if DOMAIN:
        common_params["Domain"] = DOMAIN

    # 按字典序排序，URL encode
    sorted_common = sorted(common_params.items())
    query_parts = []
    for k, v in sorted_common:
        if v:  # 跳过空值
            query_parts.append("{}={}".format(k, urllib.parse.quote(str(v), safe="")))
    canonical_query = "&".join(query_parts)

    # Body: 业务参数 + Domain（DNSPod 要求 Domain 在 body 中）
    body = dict(params) if params else {}
    if DOMAIN and "Domain" not in body:
        body["Domain"] = DOMAIN
    body_json = json.dumps(body, separators=(",", ":"), sort_keys=True) if body else ""

    # 1. CanonicalRequest
    http_method = "POST"
    canonical_uri = "/"
    canonical_headers = "content-type:application/json\nhost:{}\n".format(API_HOST)
    signed_headers = "content-type;host"
    hashed_payload = hashlib.sha256(body_json.encode("utf-8")).hexdigest()

    canonical_request = (
        http_method + "\n"
        + canonical_uri + "\n"
        + canonical_query + "\n"
        + canonical_headers + "\n"
        + signed_headers + "\n"
        + hashed_payload
    )

    # 2. StringToSign
    hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        + timestamp + "\n"
        + date + "/" + API_SERVICE + "/tc3_request\n"
        + hashed_canonical_request
    )

    # 3. Signature
    signature = _sign(SECRET_KEY, date, API_SERVICE, string_to_sign)

    # 4. Authorization
    authorization = (
        "TC3-HMAC-SHA256 "
        "Credential={}/{}/{}/tc3_request, ".format(SECRET_ID, date, API_SERVICE)
        + "SignedHeaders={}, Signature={}".format(signed_headers, signature)
    )

    # 5. Request
    url = "https://" + API_HOST + "/?" + canonical_query
    headers = {
        "Content-Type": "application/json",
        "Host": API_HOST,
        "X-TC-Action": action,
        "X-TC-Timestamp": timestamp,
        "X-TC-Version": API_VERSION,
        "Authorization": authorization,
    }

    try:
        req = urllib.request.Request(
            url,
            data=body_json.encode("utf-8") if body_json else b"",
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("Response", result)
    except urllib.request.HTTPError as e:
        try:
            err = json.loads(e.read())
            print("❌ HTTP {}: {}".format(e.code, err))
        except:
            print("❌ HTTP {}: {}".format(e.code, e.reason))
        return None
    except Exception as e:
        print("❌ 请求失败: {}".format(e))
        return None


def create_record(sub_domain, value):
    if not DOMAIN:
        print("❌ 缺少环境变量: DNSPOD_DOMAIN")
        return None
    resp = dnspod_call("CreateRecord", {
        "SubDomain": sub_domain,
        "RecordType": "A",
        "RecordLine": "默认",
        "Value": value
    })
    if resp and resp.get("RecordId"):
        print("✅ DNS 记录已创建: {}.{} -> {} (ID: {})".format(sub_domain, DOMAIN if DOMAIN else params.get("Domain",""), value, resp["RecordId"]))
        return resp["RecordId"]
    err = resp.get("Error", {}) if resp else {}
    print("❌ 创建失败: {} - {}".format(err.get("Code"), err.get("Message")))
    return None


def delete_record(record_id):
    if not DOMAIN:
        print("❌ 缺少环境变量: DNSPOD_DOMAIN")
        return None
    resp = dnspod_call("DeleteRecord", {"RecordId": record_id})
    if resp and resp.get("RequestId"):
        print("✅ RecordId {} 已删除".format(record_id))
        return True
    err = resp.get("Error", {}) if resp else {}
    print("❌ 删除失败: {} - {}".format(err.get("Code"), err.get("Message")))
    return False


def list_records(sub_domain=""):
    if not DOMAIN:
        print("❌ 缺少环境变量: DNSPOD_DOMAIN")
        return []
    params = {}
    if sub_domain:
        params["SubDomain"] = sub_domain
    resp = dnspod_call("DescribeRecordList", params)
    if resp is None:
        return []
    records = resp.get("RecordList", [])
    if not records:
        print("ℹ️  没有找到记录: {}.{}".format(sub_domain, DOMAIN))
    else:
        print("📋 DNS 记录 ({}.{}):".format(sub_domain, DOMAIN))
        for r in records:
            print("   [{}] {}.southxs.online {} {} (Line: {})".format(r["RecordId"], r["Name"], r["Type"], r["Value"], r.get("Line", "")))
    return records


def get_record_id(sub_domain):
    records = list_records(sub_domain)
    if not records:
        return None
    if len(records) == 1:
        return records[0]["RecordId"]
    for r in records:
        if r["Type"] == "A" and r.get("Name") == sub_domain:
            return r["RecordId"]
    for r in records:
        if r["Type"] == "A":
            return r["RecordId"]
    return records[0]["RecordId"]


def modify_record(record_id, sub_domain, value):
    if not DOMAIN:
        print("❌ 缺少环境变量: DNSPOD_DOMAIN")
        return None
    resp = dnspod_call("ModifyRecord", {
        "RecordId": record_id,
        "SubDomain": sub_domain,
        "RecordType": "A",
        "RecordLine": "默认",
        "Value": value
    })
    if resp and resp.get("RequestId"):
        print("✅ DNS 记录已更新: {}.{} -> {}".format(sub_domain, DOMAIN, value))
        return True
    err = resp.get("Error", {}) if resp else {}
    print("❌ 更新失败: {} - {}".format(err.get("Code"), err.get("Message")))
    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="DNSPod DNS 管理")
    sp = p.add_subparsers(dest="action")

    c = sp.add_parser("create", help="创建 DNS 记录")
    c.add_argument("sub_domain")
    c.add_argument("value", nargs="?", default="")

    sp.add_parser("delete", help="删除 DNS 记录").add_argument("record_id")

    l = sp.add_parser("list", help="列出 DNS 记录")
    l.add_argument("sub_domain", nargs="?", default="")

    sp.add_parser("id", help="获取 RecordId").add_argument("sub_domain")

    m = sp.add_parser("modify", help="修改 DNS 记录")
    m.add_argument("record_id")
    m.add_argument("sub_domain")
    m.add_argument("value")

    args = p.parse_args()
    a = args.action

    if a == "create":
        v = args.value or os.environ.get("PREVIEW_HOST", "")
        if not v:
            print("❌ 未提供记录值")
            sys.exit(1)
        create_record(args.sub_domain, v)
    elif a == "delete":
        delete_record(args.record_id)
    elif a == "list":
        list_records(args.sub_domain)
    elif a == "id":
        rid = get_record_id(args.sub_domain)
        if rid:
            print(rid)
        else:
            sys.exit(1)
    elif a == "modify":
        modify_record(args.record_id, args.sub_domain, args.value)
    else:
        p.print_help()
