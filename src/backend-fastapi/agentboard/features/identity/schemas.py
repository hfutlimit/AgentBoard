"""Identity and authentication request models."""

import re

from pydantic import BaseModel, Field, field_validator


class AuthRegister(BaseModel):
	username: str = Field(min_length=1, max_length=64)
	password: str = Field(min_length=8, max_length=1024)

	@field_validator("username")
	@classmethod
	def normalize_username(cls, value: str) -> str:
		value = value.strip()
		if not value:
			raise ValueError("username is required")
		return value


class AuthLogin(BaseModel):
	username: str = Field(min_length=1, max_length=64)
	password: str = Field(min_length=1, max_length=1024)


class UserAdminPatch(BaseModel):
	is_admin: bool


class UserProfilePatch(BaseModel):
	display_name: str | None = Field(None, max_length=100)
	email: str | None = Field(None, max_length=254)
	avatar_url: str | None = Field(None, max_length=500)

	@field_validator("email")
	@classmethod
	def validate_email(cls, value: str | None) -> str | None:
		if value is None or not value.strip():
			return value
		normalized = value.strip().lower()
		if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
			raise ValueError("invalid email address")
		return normalized

	@field_validator("avatar_url")
	@classmethod
	def validate_avatar_url(cls, value: str | None) -> str | None:
		if value is None or not value.strip():
			return value
		if not re.fullmatch(r"https?://[^\s]+", value.strip()):
			raise ValueError("avatar_url must be an http(s) URL")
		return value.strip()


class PasswordChange(BaseModel):
	current_password: str = Field(min_length=1, max_length=1000)
	new_password: str = Field(min_length=8, max_length=1000)


_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_-]*(?::(?:[a-z0-9_*.-]+))+$")


class ApiKeyCreate(BaseModel):
	name: str = Field(min_length=1, max_length=100)
	permissions: list[str] = Field(default_factory=lambda: ["api:read"], max_length=100)
	agent_ref: str | None = Field(None, min_length=1, max_length=64)

	@field_validator("name")
	@classmethod
	def clean_name(cls, value: str) -> str:
		if not value.strip():
			raise ValueError("name is required")
		return value.strip()

	@field_validator("permissions")
	@classmethod
	def validate_permissions(cls, value: list[str]) -> list[str]:
		normalized = sorted(set(value))
		if any(len(p) > 120 or not _PERMISSION_RE.fullmatch(p) for p in normalized):
			raise ValueError("permissions must be namespaced strings such as 'mcp:tools:read'")
		return normalized


class ApiKeyPatch(BaseModel):
	name: str | None = Field(None, min_length=1, max_length=100)
	enabled: bool | None = None
	permissions: list[str] | None = Field(None, max_length=100)
	agent_ref: str | None = Field(None, min_length=1, max_length=64)

	@field_validator("name")
	@classmethod
	def clean_name(cls, value: str | None) -> str | None:
		if value is not None and not value.strip():
			raise ValueError("name is required")
		return value.strip() if value is not None else None

	@field_validator("permissions")
	@classmethod
	def validate_permissions(cls, value: list[str] | None) -> list[str] | None:
		return ApiKeyCreate.validate_permissions(value) if value is not None else None

