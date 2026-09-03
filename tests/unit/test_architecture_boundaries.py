"""Architecture guardrails for the second-stage package boundaries."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = REPO_ROOT / "src" / "backend-fastapi" / "agentboard"
sys.path.insert(0, str(PYTHON_ROOT.parent))


def _python_files(relative_root: str) -> list[Path]:

	return sorted((PYTHON_ROOT / relative_root).rglob("*.py"))


def _imports(path: Path) -> set[str]:
	tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
	imports: set[str] = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			imports.update(alias.name for alias in node.names)
		elif isinstance(node, ast.ImportFrom) and node.module:
			imports.add(node.module)
		elif isinstance(node, ast.ImportFrom) and node.level >= 3:
			for alias in node.names:
				if alias.name in {"service", "mq", "schemas"}:
					imports.add(f"agentboard.{alias.name}")
	return imports


def test_runtime_and_messaging_implementation_boundaries_exist() -> None:
	assert (PYTHON_ROOT / "processors").is_dir()
	assert (PYTHON_ROOT / "core" / "infrastructure" / "messaging").is_dir()
	assert (PYTHON_ROOT / "core" / "api" / "schemas.py").is_file()


def test_feature_schema_modules_exist() -> None:
	for feature in ("projects", "work_items", "proposals", "auth"):
		assert (PYTHON_ROOT / "features" / feature / "schemas.py").is_file()


def test_feature_schemas_are_exposed_by_the_legacy_facade() -> None:
	from agentboard import schemas as legacy
	from agentboard.features.auth.schemas import AuthLogin
	from agentboard.features.projects.schemas import ProjectIn
	from agentboard.features.proposals.schemas import ProposalIn
	from agentboard.features.work_items.schemas import TaskPatch

	assert legacy.AuthLogin is AuthLogin
	assert legacy.ProjectIn is ProjectIn
	assert legacy.ProposalIn is ProposalIn
	assert legacy.TaskPatch is TaskPatch


def test_feature_and_domain_modules_do_not_import_service_facade() -> None:
	violations: list[str] = []
	for relative_root in ("features", "domains"):
		for path in _python_files(relative_root):
			if "agentboard.service" in _imports(path):
				violations.append(path.relative_to(PYTHON_ROOT).as_posix())
	assert violations == [], f"service facade imports: {violations}"


def test_runtime_modules_do_not_import_legacy_mq_or_schema_facades() -> None:
	violations: list[str] = []
	for relative_root in ("features", "domains", "core"):
		for path in _python_files(relative_root):
			imports = _imports(path)
			if "agentboard.mq" in imports or "agentboard.schemas" in imports:
				violations.append(path.relative_to(PYTHON_ROOT).as_posix())
	assert violations == [], f"legacy facade imports: {violations}"


def test_processors_is_the_single_runtime_home() -> None:
	# P7b: ``agentboard.processors`` is the only runtime home. The old
	# ``agentboard.agent_runtime`` package and the two compat facades
	# (``agentboard.worker`` / ``agentboard.features.workers``) were
	# removed, so the legacy import paths must now fail.
	import importlib

	import pytest

	from agentboard.processors import ProposalProcessor  # noqa: F401

	for legacy in (
		"agentboard.agent_runtime",
		"agentboard.worker",
		"agentboard.features.workers",
	):
		try:
			importlib.import_module(legacy)
		except ImportError:
			pass
		else:
			raise AssertionError(
				f"legacy runtime path must be removed after P7b: {legacy}"
			)
