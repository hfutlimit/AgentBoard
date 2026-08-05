# Design: 腾讯云 COS 上传后端（Epic 64 S1）

## 架构

```
前端 (App)                          API (api.py)                          COS (腾讯云)
  |  POST /api/projects/{pid}/cos/upload  |  read env: COS_SECRET_ID/KEY/BUCKET/REGION  |
  |  (multipart: file, Form)              |  cos_client.CosClient.put_object(key, data)  |--PUT (HMAC-SHA1 签名)-->
  |  <-- 201 {key, url, size, ...}        |  presigned_get_url(key)  <-------------------|
```

## 模块划分

### 1. `agentboard/cos_client.py`（新增，纯标准库）

- `class CosClient`：
  - `__init__()`：从 `os.environ` 读 4 个配置；任缺 → `configured=False`，`config_error` 描述缺哪个。
  - `is_configured() -> bool`
  - `bucket` / `region` 属性；`host = f"{bucket}.cos.{region}.myqcloud.com"`。
  - `_sign(method, key, headers, params, now_ts, expire_sec) -> str`：COS V5 签名。
    - key_time = sign_time = `"{start};{end}"`（start=now, end=now+expire）。
    - HttpParameters（预签名）或 HeaderList（PUT Authorization）按 q- 前缀字母序构造。
    - 签名串：`"{method}\n{path}\n{params}\n{headers}\n"`，params/headers 均为 `k=v&k2=v2` 且值 URL 编码、k 排序。
    - `q-signature = hex(hmac_sha1(secret_key, sign_str))`，注意 HMAC 的 key 是 SecretKey，msg 是签名串；**sign_time 签名用 SecretKey 直接做 key**（COS 签名是单层 HMAC-SHA1，不是双层）。
  - `put_object(key, data, content_type) -> None`：构造 PUT 请求（Authorization header 用 q-header-list=host），`urllib.request.urlopen`；非 2xx 抛 `CosError`。
  - `presigned_get_url(key, expire_sec=86400) -> str`：`https://{host}/{key}?{q-* 参数}&q-signature=...`（q-header-list=host）。
- `class CosError(Exception)`。

### 2. `agentboard/api.py`（新增 2 端点）

```python
@app.get("/api/projects/{pid}/cos/config")
def cos_config(pid, s):                      # 项目存在性校验 → 404
    return {"configured": c.is_configured(), "bucket": ..., "region": ..., "upload_endpoint": ...}

@app.post("/api/projects/{pid}/cos/upload", status_code=201)
async def cos_upload(pid, file: UploadFile, s):
    # 项目 404；COS 未配置 → 503（优雅降级）；文件 >10MB → 422；扩展名白名单校验（图片）
    # key = uploads/{pid}/{uuid4().hex}.{ext}
    # put_object → presigned_get_url
    # 返回 {key, url, size, content_type, original_name, cos_configured: True}
```

- 权限：路由 `^/api/projects/(\d+)` → `project_access_middleware` 自动覆盖（成员读写 / admin 绕过 / 匿名 401）。

## 关键实现细节

1. **COS V5 签名算法**（腾讯云官方规范）：
   - 签名串 = `"{HttpMethod}\n{UriPathname}\n{HttpParameters}\n{HttpHeaders}\n"`
   - HttpParameters 需按 key 字母序（`q-ak < q-header-list < q-key-time < q-sign-algorithm < q-sign-time < q-url-param-list`）。
   - 上传 PUT：Authorization header 中 `q-header-list=host`，q-url-param-list 为空；host 头列入签名（`host={host}`）。
   - 预签名 GET：query 带全部 q-* 参数（不含 q-signature），签名时 HttpParameters 为这些参数的 `k=v` 拼接（URL 编码、排序），q-header-list=host。
   - HMAC key 用 SecretKey；q-signature 是 hex 小写。
2. **降级**：`configured=False` 时 `config` 返回 `{"configured": false, "error": "..."}`；`upload` 返回 503。
3. **文件校验**：`max_size = 10 * 1024 * 1024`；MIME 白名单 `image/png|jpeg|gif|webp`（宽松，非严格，空 content_type 放行）。
4. **测试友好**：CosClient 可注入 `now_ts` 与 `urlopen`（monkeypatch），保证固定密钥回归。

## 影响面

- 新增：`agentboard/cos_client.py`、`tests/test_cos_upload.py`、openspec change 3 文档。
- 修改：`agentboard/api.py`（+2 端点）。
- 零既有 REST/DB 契约变更；零新增依赖。
