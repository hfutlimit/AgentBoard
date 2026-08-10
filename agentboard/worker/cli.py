"""Worker CLI 入口（Epic 123 Step 2 · 从 worker.py 拆分，主循环瘦身）。

``python -m agentboard.worker --once|--loop|--mq [--agent-id ...]``
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from typing import Iterable

from .config import WorkerConfig
from .worker import ProposalWorker


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentboard.worker",
        description="AgentBoard Proposal 澄清 Worker（Epic 96 P1-2）",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="只跑一轮后退出")
    group.add_argument("--loop", action="store_true", help="常驻轮询（默认）")
    group.add_argument("--mq", action="store_true",
                       help="MQ 竞争消费模式（未配置 AGENTBOARD_MQ_URL 时自动回退轮询）")
    parser.add_argument("--agent-id", default=None,
                       help="Agent 身份（MQ 模式）：消费本 agent 定向 direct queue 接收指定任务；"
                            "同时竞争 task.available 广播任务")
    parser.add_argument("--mq-url", default=None, help="覆盖 AGENTBOARD_MQ_URL")
    parser.add_argument("--api-url", default=None, help="覆盖 AGENTBOARD_API_URL")
    parser.add_argument("--agent-cmd", default=None, help="覆盖无头 Agent 命令模板")
    parser.add_argument("--interval", type=float, default=None, help="轮询间隔（秒）")
    parser.add_argument("--max-rounds", type=int, default=None, help="澄清轮次上限")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = WorkerConfig.from_env()
    if args.api_url:
        cfg.api_url = args.api_url.rstrip("/")
    if args.agent_cmd:
        cfg.agent_cmd = args.agent_cmd
    if args.agent_id:
        cfg.agent_id = args.agent_id
    if args.interval is not None:
        cfg.poll_interval = args.interval
    if args.max_rounds is not None:
        cfg.max_rounds = args.max_rounds
    if args.mq_url:
        cfg.mq.url = args.mq_url

    try:
        worker = ProposalWorker(cfg)
    except ValueError as e:
        print(f"配置错误：{e}", file=sys.stderr)
        return 2

    with worker:
        if args.once:
            summary = worker.poll_once()
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        stop = threading.Event()
        try:
            if args.mq:
                if cfg.agent_id:
                    worker.run_agent_mq_forever(cfg.agent_id, stop)
                else:
                    worker.run_mq_forever(stop)
            else:
                worker.run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
            print("收到中断信号，Worker 已停止", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
