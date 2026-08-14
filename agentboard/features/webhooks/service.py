"""Webhooks service。

Phase 4 第五段:从 service.py 拆分。本文件仅作 facade 装载新模块;老 import
路径由 service.py 末尾 ``from .features.X.service import *`` 重绑保持兼容。

本文件不实现业务逻辑,只是把 service.py 里同主题的函数搬家过来 + 加必要的
import,行为完全一致。
"""
from __future__ import annotations

import logging

from sqlalchemy import or_, and_
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容

log = logging.getLogger("agentboard.features.webhooks.service")

from ...core.exceptions import (
    Conflict, InvalidValue, NotFound,
    InvalidValue,
    NotFound,
)

from ...core.service_helpers import (
    _commit, _invalidate_project_stats_cache, _paginate, _required,
)


from ..work_items.models import WebhookConfig
from ..projects.models import Project

def list_webhooks(s: Session, *, project_id: int | None = None) -> list[WebhookConfig]:
    """列出 Webhook 配置。"""
    qry = s.query(WebhookConfig)
    if project_id is not None:
        qry = qry.filter(WebhookConfig.project_id == project_id)
    return qry.order_by(WebhookConfig.created_at.desc()).all()


def fire_webhook(webhook: WebhookConfig, event: str, payload: dict) -> bool:
    """触发 Webhook（异步发送 HTTP POST）。调用方需自行处理异常。"""
    import hashlib, hmac, json, time
    import httpx
    headers = {"Content-Type": "application/json", "User-Agent": "AgentBoard-Webhook/1.0"}
    if webhook.secret:
        timestamp = str(int(time.time()))
        body = json.dumps({"event": event, "timestamp": timestamp, "data": payload})
        signature = hmac.new(
            webhook.secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-AgentBoard-Signature"] = signature
        headers["X-AgentBoard-Timestamp"] = timestamp
    else:
        body = json.dumps({"event": event, "data": payload})
    try:
        resp = httpx.post(webhook.url, content=body, headers=headers, timeout=10.0)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


def get_webhook_project_id(s: Session, webhook_id: int) -> int | None:
    wh = s.get(WebhookConfig, webhook_id)
    return wh.project_id if wh else None


# ---------- Notification ----------

def fire_webhooks_for_event(s: Session, *, project_id: int, event: str,
                            payload: dict | None = None) -> dict:
    """按事件向项目下的 Webhook 配置派发（Epic 122 切片 3 M1）。

    过滤语义：
    - 仅派发 ``enabled=True`` 的 WebhookConfig；
    - ``events`` 配置（JSON 数组）为空列表 → 订阅全部事件；非空 → 精确包含
      ``event`` 才派发（与 RabbitMQ workflow 事件名同构，见 mq.EVENT_*）；
    - 单 Webhook 失败（网络异常 / 非 2xx）隔离，不影响其它 Webhook 派发。

    Webhook 事件只携带定位信息（实体 id / status / ref），状态一律以 DB 为准，
    与 workflow 事件总线铁律一致。本函数不抛异常（best-effort），返回统计：:

        {"matched": 命中并尝试派发的 webhook 数, "succeeded": 2xx 成功的 webhook 数}

    注意：HTTP 派发是同步的（单发超时 10s）。调用方若在请求路径上，应评估
    Webhook 数量与耗时；MVP 量级（项目级 webhook 通常个位数）可接受。
    """
    import json
    if payload is None:
        payload = {}
    matched = succeeded = 0
    try:
        rows = s.query(WebhookConfig).filter(
            or_(WebhookConfig.project_id == project_id,
                WebhookConfig.project_id.is_(None)),  # 项目级 + 全局（project_id=NULL）
            WebhookConfig.enabled.is_(True),
        ).all()
    except Exception:
        # DB 异常不阻断主业务（best-effort）
        return {"matched": 0, "succeeded": 0}
    for wh in rows:
        try:
            subscribed = json.loads(wh.events or "[]")
        except (TypeError, ValueError):
            subscribed = []
        if subscribed and event not in subscribed:
            continue  # 空列表 = 订阅全部；非空需精确匹配
        matched += 1
        try:
            if fire_webhook(wh, event, payload):
                succeeded += 1
        except Exception:
            # 单 webhook 异常隔离：不影响其它 webhook 派发
            log.warning("webhook %s（%s）派发 %s 失败：%s",
                        wh.id, wh.name, event, traceback.format_exc(limit=2))
    return {"matched": matched, "succeeded": succeeded}


# ---------- Epic 22 Story 22.3: 数据导入 ----------

def create_webhook(
    s: Session, *, project_id: int | None, name: str, url: str,
    secret: str | None = None, events: list[str] | None = None,
    created_by: int | None = None,
) -> WebhookConfig:
    """创建 Webhook 配置。"""
    import json
    name = _required(name, "name", 100)
    url_val = _required(url, "url", 2000)
    if not url_val.startswith(("http://", "https://")):
        raise InvalidValue("url must start with http:// or https://")
    wh = WebhookConfig(
        project_id=project_id, name=name, url=url_val, secret=secret or None,
        events=json.dumps(events or []), created_by=created_by,
    )
    s.add(wh)
    _commit(s)
    s.refresh(wh)
    return wh


def toggle_webhook(s: Session, webhook_id: int, enabled: bool) -> WebhookConfig:
    """启用/停用 Webhook。"""
    wh = s.get(WebhookConfig, webhook_id)
    if not wh:
        raise NotFound(f"webhook {webhook_id} not found")
    wh.enabled = enabled
    _commit(s)
    return wh


def delete_webhook(s: Session, webhook_id: int) -> None:
    """删除 Webhook 配置。"""
    wh = s.get(WebhookConfig, webhook_id)
    if not wh:
        raise NotFound(f"webhook {webhook_id} not found")
    s.delete(wh)
    _commit(s)


