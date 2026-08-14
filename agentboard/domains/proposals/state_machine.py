"""[FACADE] agentboard.domains.proposals.state_machine → agentboard.features.proposals.state_machine"""
from ...features.proposals.state_machine import *  # noqa: F401,F403
from ...features.proposals.state_machine import (  # noqa: F401
    ProposalStateMachine, TransitionSpec, bind_side_effects, SideEffect, Validator,
    IllegalTransitionError as _SM_IllegalTransitionError,
)
