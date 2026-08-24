"""Request models owned by the webhooks feature."""

from pydantic import BaseModel, Field


class WebhookIn(BaseModel):
	name: str = Field(min_length=1, max_length=100)
	url: str = Field(min_length=1, max_length=2000)
	secret: str | None = Field(None, max_length=256)
	events: list[str] = Field(default_factory=list)

