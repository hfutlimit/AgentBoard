"""Compatibility facade for the former Python worker package.

The implementation is owned by :mod:`agentboard.agent_runtime`. New code
must import that package directly; this module remains for old callers.
"""

from agentboard.agent_runtime import *  # noqa: F401,F403
from agentboard.agent_runtime import ProposalWorker

__all__ = ["ProposalWorker"]
