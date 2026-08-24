"""HTTP authentication contracts.

Identity services remain in ``features.identity``; these re-exports keep the
authentication adapter's public request contract local to the auth feature.
"""

from ..identity.schemas import (
	ApiKeyCreate,
	ApiKeyPatch,
	AuthLogin,
	AuthRegister,
	PasswordChange,
	UserProfilePatch,
)

__all__ = [
	"ApiKeyCreate",
	"ApiKeyPatch",
	"AuthLogin",
	"AuthRegister",
	"PasswordChange",
	"UserProfilePatch",
]

