"""Transport models shared by more than one HTTP feature."""

from pydantic import BaseModel, Field


class CommentIn(BaseModel):
	author: str = Field(min_length=1, max_length=100)
	content: str = Field(min_length=1)


class StatusIn(BaseModel):
	status: str
	reason: str = ""
	status_reason: str | None = None

