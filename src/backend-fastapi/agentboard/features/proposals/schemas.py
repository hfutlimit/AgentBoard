"""Request models owned by the proposals and ticket workflow."""

from pydantic import BaseModel, Field


class ProposalIn(BaseModel):
	project_id: int
	title: str = Field(min_length=1, max_length=300)
	content: str = ""
	author_id: int | None = None
	auto_create_ticket: bool = False
	target_epic_id: int | None = None


class ProposalPatch(BaseModel):
	title: str | None = Field(default=None, min_length=1, max_length=300)
	content: str | None = None
	converged_spec: str | None = None
	story_id: int | None = None
	auto_create_ticket: bool | None = None
	target_epic_id: int | None = None


class ProposalStatusIn(BaseModel):
	status: str
	error: str | None = None


class ProposalClaimIn(BaseModel):
	agent: str = ""


class ProposalReclaimIn(BaseModel):
	lease_seconds: int | None = Field(default=None, ge=0)


class RecoverFailedIn(BaseModel):
	window_seconds: int | None = Field(default=None, ge=0)
	max_retries: int | None = Field(default=None, ge=1)


class ProposalAskIn(BaseModel):
	questions: list[str] = Field(min_length=1)
	round: int | None = None
	summary: str = ""
	agent: str = ""


class ProposalAnswerIn(BaseModel):
	answer: str = ""
	unsure: bool = False


class ProposalConvertIn(BaseModel):
	epic_id: int
	title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketRequestSpec(BaseModel):
	type: str
	epic_id: int | None = None
	story_id: int | None = None
	title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketRequestExecuteSpec(BaseModel):
	proposal_id: int
	type: str
	request_id: int | None = None
	epic_id: int | None = None
	story_id: int | None = None
	title: str | None = Field(default=None, min_length=1, max_length=300)


class TicketFailIn(BaseModel):
	error: str = ""


class TicketReclaimIn(BaseModel):
	lease_seconds: int | None = None


ProposalTicketIn = TicketRequestSpec
TicketRequestExecuteIn = TicketRequestSpec

