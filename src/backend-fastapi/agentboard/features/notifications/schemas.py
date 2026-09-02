"""Request models owned by the notifications feature."""

from pydantic import BaseModel, Field


class NotificationIn(BaseModel):
	user_id: int = Field(gt=0)
	notif_type: str = Field(..., pattern=r"^(project_invite|join_request|task_assigned|status_changed|mentioned|owner_transferred)$")
	title: str = Field(min_length=1, max_length=300)
	content: str = ""
	link: str | None = Field(None, max_length=500)

