"""Epic 64 S1 — 腾讯云 COS 上传后端回归护栏（Task 991）。

覆盖：
1. CosClient 签名算法自洽（官方四步：HttpString → SHA1 → StringToSign → SignKey → Signature）；
2. 预签名 GET URL 结构与参数规则；
3. PUT 请求构造（Authorization / Host / Content-Length）；
4. 未配置优雅降级（CosClient 抛错 + API 503 + config configured:false）；
5. API 端点直接调用：404 / 422（超大、非图片 MIME）/ 201 成功（FakeClient）；
6. 真实 uvicorn 子进程（REQUIRE_AUTH=1）：非成员 403 / 匿名 401 / 成员 config 200 / 未配置 503。

运行：
    PYTHONPATH=. python -m pytest tests/test_cos_upload.py -q
"""
import asyncio
import hashlib
import hmac
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse

import httpx
import pytest
from starlette.datastructures import UploadFile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 独立临时数据库 + 显式清空 COS 配置（默认未配置态）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
for _k in ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET", "COS_REGION"):
    os.environ.pop(_k, None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, service  # noqa: E402
from agentboard.cos_client import CosClient, CosError  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()  # 跑完整 alembic 迁移链

# 固定测试凭据/时间（便于手算回归）
_AK = "AKIDzGkr1W6w7Mk9E2H1VjM2fJ2mF2vP3z"
_SK = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
_KT = "1510109259;1510112859"


# ===================== 参考实现（官方四步，刻意与实现不同风格） =====================
def _ref_signature(secret_key, method, path, params, headers, key_time):
    def enc(v):
        return urllib.parse.quote(str(v), safe=";/:=+,-_.~")
    p_str = "&".join(f"{k}={enc(params[k])}" for k in sorted(params))
    h_str = "&".join(f"{k.lower()}={enc(headers[k])}" for k in sorted(headers))
    http_string = f"{method}\n{path}\n{p_str}\n{h_str}\n"
    http_hash = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
    sts = f"sha1\n{key_time}\n{http_hash}\n"
    sign_key = hmac.new(secret_key.encode("utf-8"), key_time.encode("utf-8"), hashlib.sha1).hexdigest()
    return hmac.new(sign_key.encode("utf-8"), sts.encode("utf-8"), hashlib.sha1).hexdigest()


# ===================== CosClient 纯单元 =====================
class TestCosClient:
    def _client(self):
        return CosClient(env={
            "COS_SECRET_ID": _AK, "COS_SECRET_KEY": _SK,
            "COS_BUCKET": "examplebucket-1250000000", "COS_REGION": "ap-beijing",
        })

    def test_configured_and_config_dict(self):
        c = self._client()
        assert c.is_configured()
        d = c.config_dict()
        assert d["configured"] is True and d["bucket"] == "examplebucket-1250000000"
        assert d["region"] == "ap-beijing" and d["error"] is None

    def test_unconfigured_and_config_dict(self):
        c = CosClient(env={})
        assert not c.is_configured()
        assert "COS_SECRET_ID" in c.config_error
        assert c.config_dict()["error"]

    def test_sign_matches_reference_steps(self):
        """固定输入：实现签名 == 官方四步参考实现（算法正确性核心护栏）。"""
        c = self._client()
        path = "/uploads/1/abc.png"
        params = {
            "q-sign-algorithm": "sha1",
            "q-ak": _AK,
            "q-sign-time": _KT,
            "q-key-time": _KT,
            "q-header-list": "host",
            "q-url-param-list": "",
        }
        sig = c._sign("get", path, params, {"host": c.host}, _KT, _KT)
        ref = _ref_signature(_SK, "get", path, params, {"host": c.host}, _KT)
        assert sig == ref, "签名与官方四步算法不一致"
        assert len(sig) == 40 and all(ch in "0123456789abcdef" for ch in sig)

    def test_presigned_url_structure(self):
        c = self._client()
        now = 1510109259
        expire = 86400
        url = c.presigned_get_url("uploads/1/abc.png", expire_sec=expire, now_ts=now)
        assert url.startswith("https://examplebucket-1250000000.cos.ap-beijing.myqcloud.com/uploads/1/abc.png?")
        q = urllib.parse.parse_qs(url.split("?", 1)[1], keep_blank_values=True)
        assert q["q-sign-algorithm"] == ["sha1"]
        assert q["q-ak"] == [_AK]
        kt = f"{now};{now + expire}"
        assert q["q-key-time"] == [kt]
        assert q["q-sign-time"] == [kt]
        assert q["q-header-list"] == ["host"]
        assert q["q-url-param-list"] == [""]
        assert len(q["q-signature"][0]) == 40
        # query 参数按字母序（q-ak < q-header-list < q-key-time < q-sign-algorithm < q-sign-time < q-signature < q-url-param-list）
        keys = [kv.split("=")[0] for kv in url.split("?", 1)[1].split("&")]
        assert keys == sorted(keys)

    def test_put_object_request_construction(self):
        c = self._client()
        captured = {}

        class _FakeResp:
            status = 200

        def _fake_urlopen(req):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["headers"] = dict(req.headers.items())
            captured["body"] = req.data
            return _FakeResp()

        c.put_object("uploads/1/abc.png", b"\x89PNG-data", "image/png",
                     now_ts=1510109259, urlopen=_fake_urlopen)
        assert captured["method"] == "PUT"
        assert captured["url"] == "https://examplebucket-1250000000.cos.ap-beijing.myqcloud.com/uploads/1/abc.png"
        assert captured["body"] == b"\x89PNG-data"
        # urllib 会把 header 名 capitalize（Content-Type → Content-type），统一小写断言
        headers = {k.lower(): v for k, v in captured["headers"].items()}
        assert headers["content-type"] == "image/png"
        assert headers["content-length"] == "9"
        assert headers["host"] == "examplebucket-1250000000.cos.ap-beijing.myqcloud.com"
        auth = headers["authorization"]
        # Authorization 参数按字典序排列（与官方 SDK 行为一致），解析后断言各 q-* 字段
        auth_params = dict(kv.split("=", 1) for kv in auth.split("&"))
        assert auth_params["q-sign-algorithm"] == "sha1"
        assert auth_params["q-ak"] == _AK
        # PUT 请求签名 TTL=60s
        assert auth_params["q-sign-time"] == "1510109259;1510109319"
        assert auth_params["q-key-time"] == "1510109259;1510109319"
        assert auth_params["q-header-list"] == "host"
        assert auth_params["q-url-param-list"] == ""
        assert len(auth_params["q-signature"]) == 40

    def test_put_object_non_2xx_raises(self):
        c = self._client()

        class _FakeResp:
            status = 500

        def _fake_urlopen(req):
            return _FakeResp()

        with pytest.raises(CosError, match="HTTP 500"):
            c.put_object("uploads/1/x.png", b"x", "image/png", now_ts=1, urlopen=_fake_urlopen)

    def test_unconfigured_ops_raise(self):
        c = CosClient(env={})
        with pytest.raises(CosError, match="not configured"):
            c.put_object("uploads/1/x.png", b"x", "image/png")
        with pytest.raises(CosError, match="not configured"):
            c.presigned_get_url("uploads/1/x.png")


# ===================== API 端点（同进程直调 + FakeClient） =====================
class TestCosApi:
    def _mk_project(self, s):
        p = service.create_project(s, name="COS 上传测试", key="cospid" + str(int(time.time() * 1000) % 1000000))
        s.commit()
        return p.id

    @pytest.fixture(scope="class")
    def pid(self):
        with SessionLocal() as s:
            return self._mk_project(s)

    def test_config_unconfigured(self, pid):
        with SessionLocal() as s:
            r = api.cos_config(pid, s=s)
        assert r["configured"] is False and r["upload_endpoint"] == f"/api/projects/{pid}/cos/upload"

    def test_config_unknown_project_404(self):
        with SessionLocal() as s:
            with pytest.raises(Exception) as ei:
                api.cos_config(999999, s=s)
            assert getattr(ei.value, "status_code", 0) == 404

    def test_upload_404_unknown_project(self):
        with SessionLocal() as s:
            with pytest.raises(Exception) as ei:
                asyncio.run(api.cos_upload(999999, UploadFile(file=io.BytesIO(b"x"), filename="a.png"), s=s))
            assert getattr(ei.value, "status_code", 0) == 404

    def test_upload_503_unconfigured(self, pid):
        with SessionLocal() as s:
            with pytest.raises(Exception) as ei:
                asyncio.run(api.cos_upload(pid, UploadFile(file=io.BytesIO(b"x"), filename="a.png"), s=s))
            assert getattr(ei.value, "status_code", 0) == 503
            assert "COS not configured" in str(ei.value.detail)

    def test_upload_201_success(self, monkeypatch, pid):
        captured = {}

        class _FakeClient:
            def is_configured(self):
                return True

            def put_object(self, key, data, content_type):
                captured.update(key=key, data=data, ct=content_type)

            def presigned_get_url(self, key):
                return "https://presigned.example/" + key

        monkeypatch.setattr(api, "_cos_client", _FakeClient())
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        uf = UploadFile(file=io.BytesIO(data), filename="shot.png", headers={"content-type": "image/png"})
        with SessionLocal() as s:
            r = asyncio.run(api.cos_upload(pid, uf, s=s))
        assert r["key"].startswith(f"uploads/{pid}/") and r["key"].endswith(".png")
        assert r["url"] == "https://presigned.example/" + r["key"]
        assert r["size"] == len(data)
        assert r["content_type"] == "image/png"
        assert r["original_name"] == "shot.png"
        assert r["cos_configured"] is True
        assert captured["data"] == data and captured["ct"] == "image/png"

    def test_upload_422_too_large(self, monkeypatch, pid):
        monkeypatch.setattr(api, "_cos_client", type("_C", (), {"is_configured": lambda self: True})())
        big = b"\x89PNG" + b"\x00" * (api._COS_MAX_SIZE + 1)
        uf = UploadFile(file=io.BytesIO(big), filename="big.png", headers={"content-type": "image/png"})
        with SessionLocal() as s:
            with pytest.raises(Exception) as ei:
                asyncio.run(api.cos_upload(pid, uf, s=s))
            assert getattr(ei.value, "status_code", 0) == 422
            assert "too large" in str(ei.value.detail)

    def test_upload_422_bad_mime(self, monkeypatch, pid):
        monkeypatch.setattr(api, "_cos_client", type("_C", (), {"is_configured": lambda self: True})())
        uf = UploadFile(file=io.BytesIO(b"%PDF-1.4"), filename="doc.pdf", headers={"content-type": "application/pdf"})
        with SessionLocal() as s:
            with pytest.raises(Exception) as ei:
                asyncio.run(api.cos_upload(pid, uf, s=s))
            assert getattr(ei.value, "status_code", 0) == 422
            assert "unsupported content type" in str(ei.value.detail)


# ===================== 真实 uvicorn 子进程（REQUIRE_AUTH=1） =====================
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["AGENTBOARD_REQUIRE_AUTH"] = "1"
    for _k in ("COS_SECRET_ID", "COS_SECRET_KEY", "COS_BUCKET", "COS_REGION"):
        env.pop(_k, None)  # 子进程保持「未配置」态，验证降级与权限
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


def _register_login(base: str, username: str, password: str) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30)
    c.post("/api/auth/register", json={"username": username, "password": password})
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"{username} 登录失败：{r.text}"
    c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return c


@pytest.fixture(scope="module")
def stack():
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(base)
        owner = _register_login(base, "cosowner", "cosowner123")
        outsider = _register_login(base, "cosout", "cosout123")

        r = owner.post("/api/projects", json={"name": "COS 权限验证"})
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        # owner 是创建者=成员；outsider 非成员
        yield {"base": base, "owner": owner, "outsider": outsider, "project_id": pid}
        owner.close()
        outsider.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_auth_member_config_200(stack):
    r = stack["owner"].get(f"/api/projects/{stack['project_id']}/cos/config")
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False  # 未配置 → 优雅降级，不报错


def test_auth_outsider_upload_403(stack):
    r = stack["outsider"].post(
        f"/api/projects/{stack['project_id']}/cos/upload",
        files={"file": ("a.png", b"\x89PNG", "image/png")})
    assert r.status_code == 403, r.text


def test_auth_anonymous_401(stack):
    r = httpx.post(f"{stack['base']}/api/projects/{stack['project_id']}/cos/upload",
                   files={"file": ("a.png", b"\x89PNG", "image/png")})
    assert r.status_code == 401, r.text


def test_upload_503_unconfigured_via_http(stack):
    r = stack["owner"].post(
        f"/api/projects/{stack['project_id']}/cos/upload",
        files={"file": ("a.png", b"\x89PNG", "image/png")})
    assert r.status_code == 503, r.text
    assert "COS not configured" in r.json()["detail"]
