"""腾讯云 COS 对象存储客户端(纯标准库实现,零第三方依赖)。

实现腾讯云 COS V5 签名协议(q-sign-algorithm=sha1)的子集:
- 服务端直传(PUT Object):``put_object``
- 预签名下载 URL(GET,带签名 query):``presigned_get_url``
- 配置占位 + 优雅降级:``is_configured()`` / ``config_error``

环境变量(占位,用户后续配置):
- ``COS_SECRET_ID``
- ``COS_SECRET_KEY``
- ``COS_BUCKET``(如 examplebucket-1250000000)
- ``COS_REGION``(如 ap-guangzhou)

签名算法(COS V5,官方规范 https://cloud.tencent.com/document/product/436/7778):
1. HttpString = "{Method}\\n{UriPathname}\\n{HttpParameters}\\n{HttpHeaders}\\n"
2. StringToSign = "sha1\\n{KeyTime}\\n{SHA1(HttpString)}\\n"
3. SignKey = HMAC-SHA1(SecretKey, KeyTime)
4. Signature = HMAC-SHA1(SignKey, StringToSign)

HttpParameters / HttpHeaders 均需按 key 字母序排列、值做 URL 编码(保留合法子分隔符)。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

ENV_KEYS = ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET", "COS_REGION")

_DEFAULT_EXPIRE_SECONDS = 86400  # 预签名 URL 默认 24h
_REQUEST_TTL_SECONDS = 60  # PUT 请求签名有效期


def _urlencode_value(value: str) -> str:
    """对签名参数值做 URL 编码,保留 COS 允许的子分隔符(分号等)。"""
    return urllib.parse.quote(str(value), safe=";/:=+,-_.~")


def _quote_path(key: str) -> str:
    """对象 key 的路径编码:保留 / 与子分隔符。"""
    return urllib.parse.quote(key, safe="/;/:=+,-_.~")


class CosError(Exception):
    """COS 请求失败(网络 / 非 2xx / 配置缺失等)。"""


def _hmac_sha1_hex(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha1).hexdigest()


class CosClient:
    """最小腾讯云 COS 客户端。未配置时所有写/URL 操作抛 CosError,读取操作安全。"""

    def __init__(self, env: Optional[Dict[str, str]] = None) -> None:
        env = env if env is not None else os.environ
        self.secret_id = env.get("COS_SECRET_ID", "").strip()
        self.secret_key = env.get("COS_SECRET_KEY", "").strip()
        self.bucket = env.get("COS_BUCKET", "").strip()
        self.region = env.get("COS_REGION", "").strip()
        missing = [k for k in ENV_KEYS if not env.get(k, "").strip()]
        self._configured = not missing
        self.config_error = (
            f"missing env: {', '.join(missing)}" if missing else ""
        )

    # ---------- 配置 ----------
    def is_configured(self) -> bool:
        return self._configured

    @property
    def host(self) -> str:
        return f"{self.bucket}.cos.{self.region}.myqcloud.com"

    def config_dict(self) -> dict:
        return {
            "configured": self._configured,
            "bucket": self.bucket or None,
            "region": self.region or None,
            "error": self.config_error or None,
        }

    # ---------- 签名 ----------
    def _sign(
        self,
        method: str,
        path: str,
        params: Dict[str, str],
        headers: Dict[str, str],
        key_time: str,
        sign_time: str,
    ) -> str:
        """COS V5 签名核心:返回 q-signature(hex)。

        四步算法:
        1. HttpString = method\\npath\\nparams\\nheaders\\n
        2. StringToSign = sha1\\n{key_time}\\n{SHA1(HttpString)}\\n
        3. SignKey = HMAC-SHA1(secret_key, key_time)
        4. Signature = HMAC-SHA1(SignKey, StringToSign)
        """
        # HttpParameters:q-* 参数按 key 字母序,值 URL 编码
        params_str = "&".join(
            f"{k}={_urlencode_value(params[k])}" for k in sorted(params)
        )
        # HttpHeaders:header 名小写、按字母序,值 URL 编码
        headers_str = "&".join(
            f"{k.lower()}={_urlencode_value(headers[k])}" for k in sorted(headers)
        )
        http_string = f"{method}\n{path}\n{params_str}\n{headers_str}\n"
        http_string_hash = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
        string_to_sign = f"sha1\n{key_time}\n{http_string_hash}\n"
        sign_key = _hmac_sha1_hex(self.secret_key, key_time)
        return _hmac_sha1_hex(sign_key, string_to_sign)

    def _build_authorization(
        self, method: str, path: str, headers: Dict[str, str], now_ts: int,
        expire_sec: int,
    ) -> Tuple[str, str]:
        """构造 PUT 请求的 Authorization header。返回 (authorization, host)。"""
        start, end = now_ts, now_ts + expire_sec
        key_time = sign_time = f"{start};{end}"
        host = self.host
        params = {
            "q-sign-algorithm": "sha1",
            "q-ak": self.secret_id,
            "q-sign-time": sign_time,
            "q-key-time": key_time,
            "q-header-list": "host",
            "q-url-param-list": "",
        }
        sig_headers = {"host": host}
        q_signature = self._sign(method, path, params, sig_headers, key_time, sign_time)
        parts = [f"{k}={v}" for k, v in sorted(params.items())]
        parts.append(f"q-signature={q_signature}")
        return "&".join(parts), host

    def _build_presigned_query(
        self, method: str, path: str, now_ts: int, expire_sec: int,
    ) -> Dict[str, str]:
        start, end = now_ts, now_ts + expire_sec
        key_time = sign_time = f"{start};{end}"
        params = {
            "q-sign-algorithm": "sha1",
            "q-ak": self.secret_id,
            "q-sign-time": sign_time,
            "q-key-time": key_time,
            "q-header-list": "host",
            "q-url-param-list": "",
        }
        q_signature = self._sign(method, path, params, {"host": self.host}, key_time, sign_time)
        out = dict(params)
        out["q-signature"] = q_signature
        return out

    # ---------- 对象操作 ----------
    def put_object(self, key: str, data: bytes, content_type: str = "application/octet-stream",
                   now_ts: Optional[int] = None, urlopen=None) -> None:
        """服务端直传:PUT Object 至 COS。非 2xx 抛 CosError。"""
        if not self._configured:
            raise CosError(f"COS not configured: {self.config_error}")
        now = now_ts if now_ts is not None else int(time.time())
        path = _quote_path(key)
        headers = {"host": self.host}
        authorization, host = self._build_authorization("put", path, headers, now, _REQUEST_TTL_SECONDS)
        req = urllib.request.Request(
            f"https://{host}/{path}",
            data=data,
            method="PUT",
            headers={
                "Host": host,
                "Content-Type": content_type,
                "Content-Length": str(len(data)),
                "Authorization": authorization,
            },
        )
        opener = urlopen or urllib.request.urlopen
        try:
            resp = opener(req)
            if getattr(resp, "status", None) and resp.status >= 300:
                raise CosError(f"COS upload failed: HTTP {resp.status}")
        except urllib.error.HTTPError as e:
            raise CosError(f"COS upload failed: HTTP {e.code}: {e.reason}") from e

    def presigned_get_url(self, key: str, expire_sec: int = _DEFAULT_EXPIRE_SECONDS,
                          now_ts: Optional[int] = None) -> str:
        """生成预签名 GET URL(默认 24h 有效)。"""
        if not self._configured:
            raise CosError(f"COS not configured: {self.config_error}")
        now = now_ts if now_ts is not None else int(time.time())
        path = _quote_path(key)
        query = self._build_presigned_query("get", path, now, expire_sec)
        qs = "&".join(f"{k}={_urlencode_value(query[k])}" for k in sorted(query))
        return f"https://{self.host}/{path}?{qs}"


# 模块级单例(api.py 引用)
client = CosClient()
