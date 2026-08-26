"""Transport models shared by more than one HTTP feature."""

from pydantic import BaseModel, Field


class CommentIn(BaseModel):
	author: str = Field(min_length=1, max_length=100)
	content: str = Field(min_length=1)


class StatusIn(BaseModel):
	status: str
	reason: str = ""
	status_reason: str | None = None


class LeaseReclaimIn(BaseModel):
	"""POST /api/{stories,tasks}/reclaim-stale 请求体（均可省略用默认租约）。"""
	lease_seconds: int | None = Field(default=None, ge=0, le=7 * 24 * 3600)

