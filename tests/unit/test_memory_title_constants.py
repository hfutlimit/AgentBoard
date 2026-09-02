"""回归测试（2026-09-02）：记忆文档 title 常量的唯一真源必须在 documents.py。

背景：Epic 78 Story 107 的 Phase 6b 拆分把 ``_memory_title`` 移到
``agentboard/features/mcp/documents.py``，但 ``_MEMORY_PROJECT_TITLE`` /
``_MEMORY_AGENT_PREFIX`` 两个常量留在了 ``mcp_server.py``——远端 124 部署的
``append_agent_memory`` / ``get_project_memory`` 因此稳定抛
``NameError: name '_MEMORY_PROJECT_TITLE' is not defined``（title 拼接在任何
HTTP 调用之前，uvicorn 层面 500）。修复后常量真源在 documents.py，
mcp_server.py 仅别名引用。

验证：
1. ``_memory_title(None)`` → 项目级 title；``_memory_title(agent)`` → Agent 级前缀 title；
2. ``mcp_server`` 的同名名字与 documents 真源一致（防两处定义漂移）；
3. ``append_agent_memory`` 函数体在 stub 掉文档 HTTP 后可完整跑通（create 路径）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from agentboard.features.mcp import documents as docs  # noqa: E402


def test_memory_title_levels():
    assert docs._memory_title(None) == "项目记忆"
    assert docs._memory_title("wb_main") == "Agent 记忆 · wb_main"


def test_mcp_server_aliases_match_source_of_truth():
    import agentboard.mcp_server as ms

    assert ms._MEMORY_PROJECT_TITLE == docs._MEMORY_PROJECT_TITLE
    assert ms._MEMORY_AGENT_PREFIX == docs._MEMORY_AGENT_PREFIX


def test_append_agent_memory_create_path_end_to_end(monkeypatch):
    """stub 掉文档 HTTP 助手后，append_agent_memory 全函数体可跑通 create 路径。

    修复前该函数在拼 title 时即 NameError，任何 HTTP 都发不出去。
    """
    import agentboard.mcp_server as ms

    calls: list[tuple] = []

    def fake_doc_list(project_id, type=None, limit=100):
        calls.append(("list", project_id, type))
        return []

    def fake_doc_create(project_id, title, content="", type="plan", status="draft",
                        epic_id=None, story_id=None, author_id=None, folder_id=None):
        calls.append(("create", project_id, title, type))
        return {"id": 42, "title": title}

    monkeypatch.setattr(docs, "_doc_list", fake_doc_list)
    monkeypatch.setattr(docs, "_doc_create", fake_doc_create)

    fn = getattr(ms.append_agent_memory, "fn", ms.append_agent_memory)

    r = fn(project_id=8, content="约定：示例")
    assert r["appended"] is False
    assert r["document_id"] == 42
    assert r["title"] == "项目记忆"

    r2 = fn(project_id=8, content="x", agent="wb_main")
    assert r2["title"] == "Agent 记忆 · wb_main"

    assert ("create", 8, "项目记忆", "memory") in calls
