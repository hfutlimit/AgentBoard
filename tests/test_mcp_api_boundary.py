"""Guard the MCP-to-REST architectural boundary."""

import ast
from pathlib import Path


def test_mcp_server_does_not_import_database_or_service():
    source = Path("agentboard/mcp_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )

    assert not any(name.endswith(("database", "service")) for name in imported_modules)
    assert "SessionLocal" not in source
    assert "AGENTBOARD_MCP_BACKEND" not in source
