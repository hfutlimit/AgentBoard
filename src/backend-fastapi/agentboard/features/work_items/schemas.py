"""Request models owned by the work-items feature."""

from pydantic import BaseModel, Field, field_validator


class TaskIn(BaseModel):
	project_id: int = Field(gt=0)
	title: str = Field(min_length=1, max_length=300)
	type: str = "dev"
	description: str = ""
	spec: str = ""
	priority: str = "medium"
	assignee_id: int | None = None
	due_date: str | None = None
	labels: str = "[]"
	estimate: float | None = None
	needed_capabilities: str | list[str | dict] = "[]"
	complexity: int | None = Field(None, ge=1, le=5)
	domain_tags: str | list[str] = "[]"
	assignment_mode: str = Field("claim", pattern=r"^(claim|arbitrated)$")


class TaskPatch(BaseModel):
	title: str | None = Field(None, min_length=1, max_length=300)
	type: str | None = None
	status: str | None = None
	description: str | None = None
	spec: str | None = None
	priority: str | None = None
	sprint_id: int | None = None
	assignee_id: int | None = None
	due_date: str | None = None
	labels: str | None = None
	estimate: float | None = None
	needed_capabilities: str | list[str | dict] | None = None
	complexity: int | None = Field(None, ge=1, le=5)
	domain_tags: str | list[str] | None = None
	assignment_mode: str | None = Field(None, pattern=r"^(claim|arbitrated)$")
	status_reason: str | None = None


class SpecAppendIn(BaseModel):
	text: str = Field(min_length=1)


class AgentReviewIn(BaseModel):
	verdict: str = Field(pattern="^(approve|reject)$")
	comment: str = Field(min_length=1, max_length=2000)


class ReassignTimeoutIn(BaseModel):
	timeout_minutes: int = Field(default=30, ge=1, le=1440)
	max_per_run: int = Field(default=20, ge=1, le=200)


class BulkTaskUpdate(BaseModel):
	task_ids: list[int] = Field(..., min_length=1, max_length=100)
	status: str | None = None
	status_reason: str | None = None
	priority: str | None = None
	sprint_id: int | None = None
	assignee_id: int | None = None
	clear_assignee: bool = False
	due_date: str | None = None
	clear_due_date: bool = False

	@field_validator("task_ids")
	@classmethod
	def validate_ids(cls, value: list[int]) -> list[int]:
		if not value:
			raise ValueError("task_ids cannot be empty")
		if len(set(value)) != len(value):
			raise ValueError("task_ids must be unique")
		return value


class BulkTaskDelete(BaseModel):
	task_ids: list[int] = Field(..., min_length=1, max_length=100)

	@field_validator("task_ids")
	@classmethod
	def validate_ids(cls, value: list[int]) -> list[int]:
		if not value:
			raise ValueError("task_ids cannot be empty")
		if len(set(value)) != len(value):
			raise ValueError("task_ids must be unique")
		return value

