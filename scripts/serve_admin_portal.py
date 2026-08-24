#!/usr/bin/env python3
"""
Admin Portal 静态服务 + /api 反向代理。

将 `src/frontend/dist/admin-portal/browser` 以静态文件方式提供，
并将以 `/api` 开头的请求代理到本地 API (默认 http://127.0.0.1:58125)，
使 E2E 测试可在同源下访问 API (无 CORS 干扰)。

用法:
    python scripts/serve_admin_portal.py --port 4321

随后运行 E2E:
    ADMIN_PORTAL_URL=http://127.0.0.1:4321 python tests/admin_portal/run_all.py
"""
import os
import sys
import argparse
import urllib.request
import urllib.error
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.normpath(
    os.path.join(HERE, "..", "frontend", "dist", "admin-portal", "browser")
)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST, **kwargs)

    # ---- /api 反向代理 ----
    def _proxy(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None
        url = f"{TARGET}{self.path}"
        req = urllib.request.Request(url, data=body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in ("host", "content-length", "connection"):
                req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            data = resp.read()
            self.send_response(resp.status)
            for h, val in resp.getheaders():
                if h.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(h, val)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:  # noqa: BLE001
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def do_GET(self):
        if self.path.startswith("/api"):
            return self._proxy()
        # SPA 回退: 无扩展名的路径(路由) 回退到 index.html
        path = self.path.split("?")[0].split("#")[0]
        fs_path = self.translate_path(path)
        if not os.path.exists(fs_path) and "." not in os.path.basename(path):
            self.path = "/index.html"
        return super().do_GET()

    def do_HEAD(self):
        if self.path.startswith("/api"):
            return self._proxy()
        return super().do_HEAD()

    def do_POST(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_response(405)
        self.end_headers()

    def do_PATCH(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_response(405)
        self.end_headers()

    def do_PUT(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_response(405)
        self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api"):
            return self._proxy()
        self.send_response(405)
        self.end_headers()

    def log_message(self, *args):  # 静默访问日志
        pass


TARGET = "http://127.0.0.1:58125"


def main():
    global TARGET
    ap = argparse.ArgumentParser(description="Admin Portal 静态+代理服务")
    ap.add_argument("--port", type=int, default=4321)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--api", default=TARGET, help="上游 API 基址")
    args = ap.parse_args()

    TARGET = args.api

    if not os.path.isdir(DIST):
        sys.stderr.write(f"[serve_admin_portal] 未找到构建产物: {DIST}\n")
        sys.stderr.write("请先运行: npx ng build admin-portal --configuration development\n")
        sys.exit(2)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[serve_admin_portal] serving {DIST}")
    print(f"[serve_admin_portal] -> http://{args.host}:{args.port}  (proxy /api -> {TARGET})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
