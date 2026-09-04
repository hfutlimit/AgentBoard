"""Request models owned by scheduling and agent coordination."""

from pydantic import BaseModel, Field


class AgentRegisterIn(BaseModel):
	agent_id: str = Field(min_length=1, max_length=64)
	name: str = Field(min_length=1, max_length=100)
	roles: str = "[]"
	# Registration is an upsert.  Omitting capabilities must preserve an
	# operator-managed AgentProfile; an explicit [] is the only way to clear it.
	capabilities: str | list[str | dict] | None = None
	cli_command: str = ""
	model: str = ""
	auth_key: str = ""


class AgentUpdateIn(BaseModel):
	name: str | None = Field(default=None, min_length=1, max_length=100)
	roles: str | None = None
	capabilities: str | list[str | dict] | None = None
	cli_command: str | None = None
	model: str | None = Field(default=None, max_length=100)
	enabled: bool | None = None
	user_id: int | None = None


class AgentHeartbeatIn(BaseModel):
	probe_ok: bool | None = None
	probe_message: str = ""


class AgentProbeIn(BaseModel):
	timeout: int = Field(default=8, ge=1, le=30)


class SprintIn(BaseModel):
	title: str = Field(min_length=1, max_length=300)
	goal: str = ""
	start_date: str | None = None
	end_date: str | None = None


class SprintPatch(BaseModel):
	title: str | None = Field(None, min_length=1, max_length=300)
	goal: str | None = None
	start_date: str | None = None
	end_date: str | None = None


class ScheduleIn(BaseModel):
	title: str = Field(min_length=1, max_length=300)
	schedule_type: str = "cron"
	cron_expr: str | None = None
	agent: str | None = None
	task_id: int | None = None
	task_priority: str | None = None
	task_type: str | None = None
	epic_id: int | None = None


class SchedulePatch(BaseModel):
	title: str | None = Field(None, min_length=1, max_length=300)
	schedule_type: str | None = None
	cron_expr: str | None = None
	enabled: bool | None = None
	next_run_at: str | None = None
	agent: str | None = None
	task_id: int | None = None
	task_priority: str | None = None
	task_type: str | None = None
	epic_id: int | None = None


class RunIn(BaseModel):
	task_id: int | None = None
	idempotency_key: str | None = Field(None, max_length=128)


class RunPatch(BaseModel):
	status: str | None = None
	output: str | None = None
	error_message: str | None = None
	summary: str | None = None
	log_ref: str | None = None
	started_at: str | None = None
	finished_at: str | None = None
	task_id: int | None = None


class RunReportIn(BaseModel):
	status: str
	summary: str | None = None
	log_ref: str | None = None


# ---------- Worker + AgentInstance（2026-08-26 P1：多 Worker 部署隔离） ----------

class WorkerRegisterIn(BaseModel):
	"""Worker 启动时自报身份（POST /api/workers/register）。"""
	worker_id: str = Field(min_length=1, max_length=64)
	hostname: str = Field(default="", max_length=200)
	status: str = Field(default="active", max_length=20)


class AgentInstanceUpsertIn(BaseModel):
	"""Worker 给某 logical agent 挂本机 instance（POST /api/agents/{agent_id}/instances）。"""
	worker_id: str = Field(min_length=1, max_length=64)
	cli_command: str = Field(default="", max_length=500)
	model: str = Field(default="", max_length=100)
	executor_type: str | None = Field(default=None, max_length=40)
	auth_key: str = Field(default="", max_length=100)
	enabled: bool = True


class AgentInstanceHeartbeatIn(BaseModel):
	"""Instance 心跳上报（POST /api/agent-instances/{id}/heartbeat）。"""
	probe_ok: bool | None = None
	probe_message: str = Field(default="", max_length=300)

