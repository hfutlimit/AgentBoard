"""Prometheus-style metrics collector.

零依赖:不强制 prometheus_client,有则用之,无则降级为内存 Counter/Gauge。
- 业务指标:api_requests_total / api_request_duration_seconds / cache_*
- MQ 指标:queue_depth / consumer_lag / dispatch_total
- 状态机:transitions_total{entity, transition, result}

暴露端点:``GET /metrics``(本阶段先建收集器,Phase 5 在 ``core.api.app`` 挂路由)
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("agentboard.metrics")


class _Counter:
    __slots__ = ("_name", "_help", "_labelnames", "_lock", "_values")

    def __init__(self, name: str, help_: str, labelnames: tuple[str, ...] = ()) -> None:
        self._name = name
        self._help = help_
        self._labelnames = labelnames
        self._lock = threading.Lock()
        self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        with self._lock:
            self._values[key] += amount

    def render(self) -> str:
        out = [f"# HELP {self._name} {self._help}", f"# TYPE {self._name} counter"]
        with self._lock:
            for key, value in sorted(self._values.items()):
                if not key:
                    out.append(f"{self._name} {value}")
                else:
                    label_str = ",".join(f'{k}="{v}"' for k, v in key)
                    out.append(f"{self._name}{{{label_str}}} {value}")
        return "\n".join(out)


class _Histogram:
    __slots__ = ("_name", "_help", "_buckets", "_lock", "_counts", "_sum", "_total")

    def __init__(self, name: str, help_: str, buckets: tuple[float, ...] | None = None) -> None:
        self._name = name
        self._help = help_
        self._buckets = buckets or (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        self._lock = threading.Lock()
        self._counts: dict[float, int] = {b: 0 for b in self._buckets}
        self._counts[float("inf")] = 0
        self._sum = 0.0
        self._total = 0

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._total += 1
            for b in self._buckets:
                if value <= b:
                    self._counts[b] += 1
            self._counts[float("inf")] += 1

    def render(self) -> str:
        out = [f"# HELP {self._name} {self._help}", f"# TYPE {self._name} histogram"]
        with self._lock:
            cum = 0
            for b in self._buckets:
                cum += self._counts[b]
                out.append(f'{self._name}_bucket{{le="{b}"}} {self._counts[b]}')
            cum += self._counts[float("inf")]
            out.append(f'{self._name}_bucket{{le="+Inf"}} {self._counts[float("inf")]}')
            out.append(f"{self._name}_sum {self._sum}")
            out.append(f"{self._name}_count {self._total}")
        return "\n".join(out)


class _Gauge:
    __slots__ = ("_name", "_help", "_lock", "_value")

    def __init__(self, name: str, help_: str) -> None:
        self._name = name
        self._help = help_
        self._lock = threading.Lock()
        self._value = 0.0

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value -= amount

    def render(self) -> str:
        with self._lock:
            return f"# HELP {self._name} {self._help}\n# TYPE {self._name} gauge\n{self._name} {self._value}"


# ---- registry ---------------------------------------------------------------

class MetricsRecorder:
    """Metrics registry. Use the module-level ``metrics`` instance."""

    def __init__(self) -> None:
        self.api_requests_total = _Counter(
            "agentboard_api_requests_total",
            "API 请求总数,按 method/route/status 分类",
            ("method", "route", "status"),
        )
        self.api_request_duration = _Histogram(
            "agentboard_api_request_duration_seconds",
            "API 请求耗时(秒)",
        )
        self.cache_hits = _Counter("agentboard_cache_hits_total", "缓存命中")
        self.cache_misses = _Counter("agentboard_cache_misses_total", "缓存未命中")
        self.cache_evictions = _Counter("agentboard_cache_evictions_total", "缓存失效/淘汰")
        self.mq_queue_depth = _Gauge("agentboard_mq_queue_depth", "MQ 队列当前深度")
        self.mq_published = _Counter("agentboard_mq_published_total", "MQ 已发布消息数", ("queue",))
        self.mq_consumed = _Counter("agentboard_mq_consumed_total", "MQ 已消费消息数", ("queue", "result"))
        self.state_transitions = _Counter(
            "agentboard_state_transitions_total",
            "状态机迁移数,按 entity/transition/result",
            ("entity", "transition", "result"),
        )
        self.service_invocations = _Counter(
            "agentboard_service_invocations_total",
            "service 层方法调用计数",
            ("service", "method", "result"),
        )

    @contextmanager
    def time(self, histogram: _Histogram) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            histogram.observe(time.perf_counter() - start)

    def render_prometheus(self) -> str:
        """Render all metrics in Prometheus text format."""
        parts: list[str] = []
        for attr in (
            "api_requests_total", "api_request_duration",
            "cache_hits", "cache_misses", "cache_evictions",
            "mq_queue_depth", "mq_published", "mq_consumed",
            "state_transitions", "service_invocations",
        ):
            obj = getattr(self, attr)
            parts.append(obj.render())
        return "\n\n".join(parts) + "\n"


metrics = MetricsRecorder()
