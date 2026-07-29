# -*- coding: utf-8 -*-
"""MCP 权限验证客户端：对 http://127.0.0.1:18001/mcp 做 streamable HTTP 握手，
调用 list_projects 工具并打印项目清单（用于对比 admin key / 普通用户 key 作用域）。
用法: python mcp_perm_check.py <标签>
"""
import json
import os
import sys

import requests

DEBUG = os.environ.get("MCP_DEBUG") == "1"

MCP = "http://127.0.0.1:18001/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
PROXIES = {"http": None, "https": None}


def parse_body(resp):
    """兼容 JSON 与 SSE 两种响应体。"""
    ct = resp.headers.get("content-type", "")
    resp.encoding = "utf-8"  # SSE 流强制 UTF-8，避免 latin-1 误解码引入 \x85 等伪换行符
    if "text/event-stream" in ct:
        events, buf = [], []
        for line in resp.text.split("\n"):
            line = line.rstrip("\r")
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
            elif line == "" and buf:
                events.append("\n".join(buf))
                buf = []
        if buf:
            events.append("\n".join(buf))
        for ev in reversed(events):  # 取最后一个可解析为 JSON-RPC response 的事件
            try:
                obj = json.loads(ev)
                if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                    return obj
            except json.JSONDecodeError as e:
                if DEBUG:
                    print(f"[parse fail] {e}; tail={ev[-120:]!r}", file=sys.stderr)
                continue
        return None
    return resp.json() if resp.text else None


def rpc(session, method, params=None, sid=None, rid=1, notify=False):
    h = dict(HEADERS)
    if sid:
        h["Mcp-Session-Id"] = sid
    payload = {"jsonrpc": "2.0", "method": method}
    if not notify:
        payload["id"] = rid
    if params is not None:
        payload["params"] = params
    r = session.post(MCP, headers=h, json=payload, timeout=30, proxies=PROXIES)
    if DEBUG:
        print(f"--- {method} status={r.status_code} ct={r.headers.get('content-type')}\n{r.text[:2000]}\n---", file=sys.stderr)
    return r, (None if notify else parse_body(r))


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    s = requests.Session()
    r, body = rpc(s, "initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "perm-check", "version": "1.0"},
    })
    sid = r.headers.get("mcp-session-id")
    if not sid:
        print(f"[{label}] initialize 失败: {r.status_code} {r.text[:200]}")
        sys.exit(2)
    rpc(s, "notifications/initialized", {}, sid=sid, notify=True)
    r, body = rpc(s, "tools/call", {"name": "list_projects", "arguments": {}}, sid=sid, rid=2)
    if body is None or "error" in body:
        print(f"[{label}] list_projects 错误: {json.dumps(body, ensure_ascii=False)[:300]}")
        sys.exit(3)
    result = body.get("result", {})
    if result.get("isError"):
        txt = (result.get("content") or [{}])[0].get("text", "")
        print(f"[{label}] 工具返回错误: {txt[:300]}")
        sys.exit(4)
    # structuredContent 或 content[0].text
    projects = result.get("structuredContent", {}).get("result")
    if projects is None:
        txt = (result.get("content") or [{}])[0].get("text", "[]")
        projects = json.loads(txt)
    for _ in range(2):  # 兼容双重编码
        if isinstance(projects, str):
            projects = json.loads(projects)
    if isinstance(projects, dict):
        projects = projects.get("items") or projects.get("result") or []
    projects = [json.loads(p) if isinstance(p, str) else p for p in projects]
    names = [(p.get("id"), p.get("name")) for p in projects]
    print(f"[{label}] 可见项目数 = {len(names)}")
    for pid, name in names[:10]:
        print(f"  - #{pid} {name}")
    if len(names) > 10:
        print(f"  ... 共 {len(names)} 个")


if __name__ == "__main__":
    main()
