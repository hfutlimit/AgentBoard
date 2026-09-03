"""Processor CLI entrypoint (Epic 123 Step 2 - worker.py split, main loop slimmed).

Usage:
    python -m agentboard.processors --once|--loop|--mq [--agent-id ...]

P7b (2026-09-03): the package moved from ``agentboard.agent_runtime`` to
``agentboard.processors``. ASCII-only to avoid the historical GBK/UTF-8
mojibake in the original file. Behavior unchanged.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from typing import Iterable

from .config import ProcessorConfig
from .worker import ProposalProcessor


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentboard.processors",
        description="AgentBoard Proposal Processor main loop (P7b: renamed from Proposal Worker).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--once", action="store_true", help="Poll a single round then exit.")
    group.add_argument("--loop", action="store_true", help="Loop forever (default).")
    group.add_argument("--mq", action="store_true",
                       help="Consume from RabbitMQ instead of polling (AGENTBOARD_MQ_URL required).")
    parser.add_argument("--agent-id", default=None,
                       help="When --mq is set, pin to a specific agent's direct queue (skip task.available).")
    parser.add_argument("--worker-id", default=None,
                       help="Override the worker id (default: hostname; P1 2026-08-26 review follow-up Worker self-claim on overlapping instances).")
    parser.add_argument("--mq-url", default=None, help="Override AGENTBOARD_MQ_URL.")
    parser.add_argument("--api-url", default=None, help="Override AGENTBOARD_API_URL.")
    parser.add_argument("--agent-cmd", default=None, help="Override the agent CLI command.")
    parser.add_argument("--interval", type=float, default=None, help="Poll interval (seconds).")
    parser.add_argument("--max-rounds", type=int, default=None, help="Max poll rounds before exit.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = ProcessorConfig.from_env()
    if args.api_url:
        cfg.api_url = args.api_url.rstrip("/")
    if args.agent_cmd:
        cfg.agent_cmd = args.agent_cmd
    if args.agent_id:
        cfg.agent_id = args.agent_id
    if args.worker_id:
        cfg.worker_id = args.worker_id
    if args.interval is not None:
        cfg.poll_interval = args.interval
    if args.max_rounds is not None:
        cfg.max_rounds = args.max_rounds
    if args.mq_url:
        cfg.mq.url = args.mq_url

    try:
        processor = ProposalProcessor(cfg)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 2

    with processor:
        if args.once:
            summary = processor.poll_once()
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        stop = threading.Event()
        try:
            if args.mq:
                if cfg.agent_id:
                    processor.run_agent_mq_forever(cfg.agent_id, stop)
                else:
                    processor.run_mq_forever(stop)
            else:
                processor.run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
            print("Processor interrupted by user.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
