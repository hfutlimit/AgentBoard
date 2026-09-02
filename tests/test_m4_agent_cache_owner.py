"""Implementation Plan T4.1 · agent 缓存补 owner + presence-only 回归测试。

三个断言方向：
1. 缓存 entry 带 ``user_id``，且来自 **server 侧鉴权**（WS token 解析出的
   uid），不是 worker 自报 —— 归属判定不能信客户端；
2. ``pick_eligible`` / ``has_online_agent`` 按 owner 过滤 —— ephemeral 派发
   候选源必须与 DB 执行门口径一致，否则 ephemeral 模式会绕过归属；
3. 缓存不放 ``cli_command``（presence-only，143 宪法：执行面配置不上云）。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m4_agent_cache_owner.py -q
"""
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _BACKEND)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard.agent_registry_cache import (  # noqa: E402
    AgentCacheEntry, get_default_cache,
)

_OWNER_A = 101
_OWNER_B = 202


@pytest.fixture()
def cache():
    """拿到默认缓存并保证每个测试从空开始（全局单例，测试间会串）。"""
    c = get_default_cache()
    c._by_pair.clear()  # 单例清理：测试专用路径，业务代码不要这么干
    yield c
    c._by_pair.clear()


# ---------- 1. entry 归属 ----------

def test_hello_stamps_user_id_from_server_auth(cache):
    """HELLO 带 user_id → entry 记归属（server 鉴权结果）。"""
    applied = cache.apply_hello(
        "w1", [{"agent_id": "a1", "model": "m"}], user_id=_OWNER_A)
    assert applied == 1
    entry = cache.get("w1", "a1")
    assert entry.user_id == _OWNER_A


def test_entry_has_no_cli_command():
    """presence-only：缓存不存 cli_command（执行面配置不上云，143 宪法）。"""
    c = get_default_cache()
    c.apply_hello("w1", [{"agent_id": "a1", "cli_command": "secret-cmd"}],
                  user_id=_OWNER_A)
    entry = c.get("w1", "a1")
    assert not hasattr(entry, "cli_command"), \
        "cli_command 是执行面配置，不该进 server 缓存"
    assert "cli_command" not in entry.to_public_dict()
    # worker 自报的 cli_command 被丢弃，不落任何字段
    assert "secret-cmd" not in str(entry.to_public_dict())


# ---------- 2. owner 过滤 ----------

def test_pick_eligible_filters_by_owner(cache):
    c = get_default_cache()
    c.apply_hello("w-a", [{"agent_id": "a-a"}], user_id=_OWNER_A)
    c.apply_hello("w-b", [{"agent_id": "a-b"}], user_id=_OWNER_B)

    assert c.pick_eligible(user_id=_OWNER_A) == ("w-a", "a-a")
    assert c.pick_eligible(user_id=_OWNER_B) == ("w-b", "a-b")
    # 内部路径（无用户上下文）不过滤，两个都可选
    assert c.pick_eligible() in {("w-a", "a-a"), ("w-b", "a-b")}


def test_pick_eligible_owner_without_agents_gets_none(cache):
    """owner B 名下没有 agent → None，而不是捡到别人的（绕过归属）。"""
    c = get_default_cache()
    c.apply_hello("w-a", [{"agent_id": "a-a"}], user_id=_OWNER_A)
    assert c.pick_eligible(user_id=_OWNER_B) is None


def test_pick_eligible_pinned_also_respects_owner(cache):
    """pinned 命中别人的 agent → 不返回（pin 不能越权）。"""
    c = get_default_cache()
    c.apply_hello("w-a", [{"agent_id": "a-a"}], user_id=_OWNER_A)
    assert c.pick_eligible(pinned="a-a", user_id=_OWNER_A) == ("w-a", "a-a")
    assert c.pick_eligible(pinned="a-a", user_id=_OWNER_B) is None


def test_delta_reapply_keeps_owner(cache):
    """DELTA 更新同一 agent 后归属不丢（每次 apply 都带 uid）。"""
    c = get_default_cache()
    c.apply_hello("w-a", [{"agent_id": "a-a"}], user_id=_OWNER_A)
    c.apply_delta("w-a", add_or_update=[{"agent_id": "a-a", "online": False}],
                  user_id=_OWNER_A)
    assert c.get("w-a", "a-a").user_id == _OWNER_A


# ---------- 3. presence 探针（真实派发接线） ----------

def test_has_online_agent_presence_and_owner(cache):
    c = get_default_cache()
    c.apply_hello("w-a", [{"agent_id": "a-a", "online": True}],
                  user_id=_OWNER_A)
    assert c.has_online_agent("a-a", user_id=_OWNER_A)
    assert not c.has_online_agent("a-a", user_id=_OWNER_B)
    assert c.has_online_agent("a-a")  # 无用户上下文：只查 presence


def test_has_online_agent_respects_offline_and_disabled(cache):
    c = get_default_cache()
    c.apply_hello("w-a", [{"agent_id": "a-a", "online": False}],
                  user_id=_OWNER_A)
    assert not c.has_online_agent("a-a", user_id=_OWNER_A)
    c.apply_delta("w-a", add_or_update=[{"agent_id": "a-a", "online": True}],
                  user_id=_OWNER_A)
    assert c.has_online_agent("a-a", user_id=_OWNER_A)
    c.apply_delta("w-a", add_or_update=[{"agent_id": "a-a", "enabled": False}],
                  user_id=_OWNER_A)
    assert not c.has_online_agent("a-a", user_id=_OWNER_A)


def test_presence_probe_semantics_for_dispatch(cache, monkeypatch):
    """T4.1 接进真实派发的探针语义：flag 开启时 list_runnable_candidates
    用 has_online_agent(agent_id, user_id=owner) 把 DB 候选与缓存求交 ——
    这里验证该探针在 flag 开关下的行为契约。"""
    monkeypatch.setenv("AGENTBOARD_EPHEMERAL_AGENTS", "1")
    from agentboard.agent_registry_cache import ephemeral_agents_enabled
    assert ephemeral_agents_enabled()

    c = get_default_cache()
    # DB agent 在线但缓存无 presence → 探针 False → 候选被过滤
    assert not c.has_online_agent("not-in-cache")
    c.apply_hello("w-x", [{"agent_id": "in-cache"}], user_id=_OWNER_A)
    assert c.has_online_agent("in-cache", user_id=_OWNER_A)
    assert not c.has_online_agent("in-cache", user_id=_OWNER_B)
