"""Deterministic Agent capability matching.

The module deliberately has no HTTP, queue, or executor dependencies so the
same ranking policy can be reused by claims, arbitration, schedules, and
review assignment.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from ...core.exceptions import InvalidValue
from ..learning.models import TaskOutcome
from ..projects.models import Agent
from ..work_items.models import Task
from .models import TaskAssignment


MAX_PROFILE_NAME_LENGTH = 64


@dataclass(frozen=True)
class MatchResult:
    eligible: bool
    score: float
    reason: str
    components: dict[str, Any]


@dataclass(frozen=True)
class RankedAgent:
    agent: Agent
    result: MatchResult


def _json_list(value: Any, field: str) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise InvalidValue(f"{field} must be a valid JSON array") from exc
    if not isinstance(value, list):
        raise InvalidValue(f"{field} must be a JSON array")
    return value


def _profile_name(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidValue(f"{field} name must be a string")
    name = value.strip().lower()
    if not name:
        raise InvalidValue(f"{field} name is required")
    if len(name) > MAX_PROFILE_NAME_LENGTH:
        raise InvalidValue(
            f"{field} name must be at most {MAX_PROFILE_NAME_LENGTH} characters"
        )
    return name


def normalize_capabilities(value: Any) -> list[dict[str, Any]]:
    """Normalize legacy string tags and structured Agent capabilities."""
    normalized: dict[str, dict[str, Any]] = {}
    for raw in _json_list(value, "capabilities"):
        if isinstance(raw, str):
            name, level, confidence = _profile_name(raw, "capability"), 3, 0.5
        elif isinstance(raw, dict):
            name = _profile_name(raw.get("name"), "capability")
            level = raw.get("level", 3)
            confidence = raw.get("confidence", 0.5)
            if isinstance(level, bool) or not isinstance(level, (int, float)):
                raise InvalidValue("capability level must be a number from 0 to 5")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise InvalidValue("capability confidence must be a number from 0 to 1")
            if not 0 <= float(level) <= 5:
                raise InvalidValue("capability level must be between 0 and 5")
            if not 0 <= float(confidence) <= 1:
                raise InvalidValue("capability confidence must be between 0 and 1")
            level = int(level) if float(level).is_integer() else float(level)
            confidence = float(confidence)
        else:
            raise InvalidValue("capabilities entries must be strings or objects")
        candidate = {"name": name, "level": level, "confidence": confidence}
        current = normalized.get(name)
        if current is None or (level, confidence) > (
            current["level"], current["confidence"]
        ):
            normalized[name] = candidate
    return list(normalized.values())


def normalize_required_capabilities(value: Any) -> list[dict[str, Any]]:
    """Normalize task requirements to name/minimum-level entries."""
    normalized: dict[str, dict[str, Any]] = {}
    for raw in _json_list(value, "needed_capabilities"):
        if isinstance(raw, str):
            name, minimum = _profile_name(raw, "needed_capability"), 1
        elif isinstance(raw, dict):
            name = _profile_name(raw.get("name"), "needed_capability")
            minimum = raw.get("minimum_level", 1)
            if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
                raise InvalidValue("minimum_level must be a number from 0 to 5")
            if not 0 <= float(minimum) <= 5:
                raise InvalidValue("minimum_level must be between 0 and 5")
            minimum = int(minimum) if float(minimum).is_integer() else float(minimum)
        else:
            raise InvalidValue(
                "needed_capabilities entries must be strings or objects"
            )
        previous = normalized.get(name)
        normalized[name] = {
            "name": name,
            "minimum_level": max(
                minimum, previous["minimum_level"] if previous else minimum
            ),
        }
    return list(normalized.values())


def normalize_domain_tags(value: Any) -> list[str]:
    """Normalize lower-cased, de-duplicated task domain tags."""
    result: list[str] = []
    seen: set[str] = set()
    for raw in _json_list(value, "domain_tags"):
        name = _profile_name(raw, "domain_tag")
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def normalize_complexity(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise InvalidValue("complexity must be an integer from 1 to 5")
    return value


def normalize_assignment_mode(value: Any) -> str:
    mode = str(value or "claim").strip().lower()
    if mode not in {"claim", "arbitrated"}:
        raise InvalidValue("assignment_mode must be 'claim' or 'arbitrated'")
    return mode


def _roles(agent: Agent) -> set[str]:
    try:
        values = _json_list(agent.roles, "roles")
    except InvalidValue:
        return set()
    return {str(value).strip().lower() for value in values if str(value).strip()}


def score_agent_for_task(
    s: Session, agent: Agent, task: Task, role: str = "developer",
) -> MatchResult:
    """Score one Agent for one task using auditable, fixed-weight components."""
    requirements = normalize_required_capabilities(task.needed_capabilities)
    declared = {entry["name"]: entry for entry in normalize_capabilities(agent.capabilities)}
    missing = [
        requirement["name"]
        for requirement in requirements
        if requirement["name"] not in declared
        or declared[requirement["name"]]["level"] < requirement["minimum_level"]
    ]
    matched = [declared[requirement["name"]] for requirement in requirements if requirement["name"] not in missing]
    coverage = len(matched) / len(requirements) if requirements else 1.0
    proficiency = (
        sum(float(entry["level"]) / 5 for entry in matched) / len(matched)
        if matched else 0.5 if not requirements else 0.0
    )
    confidence = (
        sum(float(entry["confidence"]) for entry in matched) / len(matched)
        if matched else 0.5 if not requirements else 0.0
    )
    history_value = (
        s.query(func.avg(TaskOutcome.score))
        .filter(
            TaskOutcome.agent_registry_id == agent.id,
            TaskOutcome.task_type == task.type,
        )
        .scalar()
    )
    history = round(float(history_value), 6) if history_value is not None else 0.5
    active_load = (
        s.query(func.count(TaskAssignment.id))
        .filter(
            TaskAssignment.agent_registry_id == agent.id,
            TaskAssignment.status == "active",
            TaskAssignment.active_slot == "active",
        )
        .scalar()
        or 0
    )
    load_factor = 1 / (1 + int(active_load))
    score = round(
        0.35 * coverage
        + 0.25 * proficiency
        + 0.10 * confidence
        + 0.20 * history
        + 0.10 * load_factor,
        6,
    )
    workload_name = role.strip().lower()
    # design/developer/reviewer/qa 是本次 workload，不是 Agent 永久身份。
    # roles 仅保留兼容/审计，不再作为 eligibility gate。
    eligible = bool(agent.enabled) and not missing
    components: dict[str, Any] = {
        "coverage": round(coverage, 6),
        "proficiency": round(proficiency, 6),
        "confidence": round(confidence, 6),
        "history": history,
        "active_load": int(active_load),
        "load_factor": round(load_factor, 6),
        "missing_capabilities": missing,
        "workload_type": workload_name,
        "legacy_role_present": workload_name in _roles(agent),
        "enabled": bool(agent.enabled),
    }
    reason = json.dumps(components, ensure_ascii=False, sort_keys=True)
    return MatchResult(eligible=eligible, score=score, reason=reason, components=components)


def rank_agents_for_task(
    s: Session,
    task: Task,
    role: str = "developer",
    agents: Iterable[Agent] | None = None,
) -> list[RankedAgent]:
    """Return eligible Agents ordered by score, load, then stable primary key."""
    candidates = list(agents) if agents is not None else s.query(Agent).all()
    ranked = [
        RankedAgent(agent=agent, result=score_agent_for_task(s, agent, task, role))
        for agent in candidates
    ]
    ranked = [entry for entry in ranked if entry.result.eligible]
    return sorted(
        ranked,
        key=lambda entry: (
            -entry.result.score,
            entry.result.components["active_load"],
            entry.agent.id,
        ),
    )
