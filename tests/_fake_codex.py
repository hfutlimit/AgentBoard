"""Fake Codex CLI for E2E tests.

行为严格按 OpenAI Codex CLI 0.x ``codex exec --json`` 真实输出协议：
- stderr 写进度 chatter（``{"type":"progress","msg":"..."}``）;
- stdout 写最终决策 JSON（``{"type":"result",...}``）;
- 退出码 0 = 成功（即便决策是 ask/finalize/fail）；非 0 = 子进程错误。

控制方式（env）:
    FAKE_EXIT_CODE       默认 0；非 0 模拟子进程错误
    FAKE_DECISION        默认 ask；可选 finalize / fail
    FAKE_SLEEP           默认 0；非 0 让子进程 sleep 模拟耗时
    FAKE_FAIL_NO_PROMPT  默认 0；非 0 时忽略 prompt 直接报错
"""
from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    data = sys.stdin.read()  # 读全部 prompt

    if os.environ.get("FAKE_FAIL_NO_PROMPT") == "1" and not data.strip():
        sys.stderr.write("fake codex: empty prompt on stdin\n")
        return 11

    sleep = float(os.environ.get("FAKE_SLEEP", "0") or 0)
    if sleep:
        time.sleep(sleep)

    # 进度 chatter（写到 stderr，会被 CliLauncher stderr=STDOUT 合并进 output）
    for line in (
        '{"type":"progress","msg":"scanning repository"}',
        '{"type":"progress","msg":"drafting questions"}',
    ):
        sys.stderr.write(line + "\n")
    sys.stderr.flush()

    decision_kind = (os.environ.get("FAKE_DECISION") or "ask").strip().lower()
    if decision_kind == "ask":
        decision = {
            "type": "result",
            "action": "ask",
            "questions": [
                "目标用户群是？",
                "持久化方式偏好？",
                "MCP 工具集？",
            ],
            "summary": "fake codex 3 个澄清问题",
        }
    elif decision_kind == "finalize":
        decision = {
            "type": "result",
            "action": "finalize",
            "converged_spec": "## 需求\n做一件事\n## 验收\n- 有结果\n",
            "summary": "fake codex 收敛",
        }
    elif decision_kind == "fail":
        decision = {
            "type": "result",
            "action": "fail",
            "error": "fake codex 主动失败",
        }
    else:
        sys.stderr.write(f"fake codex: unknown FAKE_DECISION={decision_kind!r}\n")
        return 12

    # 把决策 JSON 写到 stdout（CliLauncher 会原样捕获）
    sys.stdout.write(json.dumps(decision, ensure_ascii=False) + "\n")
    sys.stdout.flush()

    # 真实 codex 在决策 JSON 后可能再写几行 chatter（也走 stderr）
    sys.stderr.write('{"type":"progress","msg":"finished"}\n')
    sys.stderr.flush()

    code = int(os.environ.get("FAKE_EXIT_CODE", "0") or 0)
    return code


if __name__ == "__main__":
    sys.exit(main())
