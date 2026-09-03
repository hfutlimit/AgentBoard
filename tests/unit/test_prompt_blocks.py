import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest
from agentboard.processors.behavior.prompt_blocks import (
    PROMPT_BLOCK_REGISTRY,
    block_checkout_branch,
    block_inspect_code,
    block_leave_summary,
    block_read_comments,
    block_reply_to_review,
    block_sync_code,
    get_prompt_block,
)


def test_registry_contains_all_core_blocks():
    expected = [
        "checkout_branch",
        "sync_code",
        "inspect_code",
        "read_documents",
        "load_memory",
        "read_comments",
        "leave_summary",
        "reply_to_review",
        "learn_from_accepted_correction",
        "learn_from_judgment_reversal",
    ]
    for key in expected:
        assert key in PROMPT_BLOCK_REGISTRY
        block = get_prompt_block(key)
        assert block is not None
        assert len(block({})) > 0


def test_block_checkout_branch_with_hint():
    txt = block_checkout_branch({"branch": "feature/ab-123"})
    assert "feature/ab-123" in txt
    assert "分支准备" in txt


def test_block_sync_code_truthfulness():
    txt = block_sync_code({})
    assert "代码同步" in txt
    assert "严禁虚假声称" in txt


def test_block_leave_summary():
    txt = block_leave_summary({})
    assert "工作总结" in txt
    assert "变更内容" in txt
    assert "受影响的文件与组件" in txt
    assert "测试与验证" in txt