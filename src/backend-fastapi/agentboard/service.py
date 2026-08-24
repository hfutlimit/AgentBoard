"""Compatibility facade for the application service layer.

The implementation now lives in ``agentboard.core.application.service``.
This module preserves the historical ``agentboard.service`` import path,
including underscore-prefixed helpers used by older integrations.
"""

from .core.application import service as _implementation

globals().update({
	name: value
	for name, value in vars(_implementation).items()
	if not name.startswith("__")
})

__all__ = [
	name for name in vars(_implementation)
	if not name.startswith("__")
]

