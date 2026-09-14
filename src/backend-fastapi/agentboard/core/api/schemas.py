"""Transport models shared by more than one HTTP feature."""

from pydantic import BaseModel, Field


class CommentIn(BaseModel):
	author: str = Field(min_length=1, max_length=100)
	content: str = Field(min_length=1)
	# 2026-09-14 P2: optional cross-reference to a Document. When set, the UI
	# renders the comment body as a collapsed link card pointing at the
	# document; the body is still kept verbatim for search. ``content`` is
	# always required — even with a linked document, callers should put a
	# short summary in the body so the comment thread reads coherently
	# when the document is deleted (ON DELETE SET NULL).
	linked_document_id: int | None = Field(default=None, ge=1)


class StatusIn(BaseModel):
	status: str
	reason: str = ""
	status_reason: str | None = None


class LeaseReclaimIn(BaseModel):
	"""POST /api/{stories,tasks}/reclaim-stale 请求体（均可省略用默认租约）。"""
	lease_seconds: int | None = Field(default=None, ge=0, le=7 * 24 * 3600)

