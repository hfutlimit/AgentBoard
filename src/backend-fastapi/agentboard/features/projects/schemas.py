"""Request models owned by the projects feature."""

from pydantic import BaseModel, Field


class ProjectIn(BaseModel):
	name: str = Field(min_length=1, max_length=200)
	key: str | None = Field(None, max_length=20)
	description: str = ""


class ProjectPatch(BaseModel):
	name: str | None = Field(None, min_length=1, max_length=200)
	key: str | None = Field(None, max_length=20)
	description: str | None = None


class ProjectPatchExtended(BaseModel):
	name: str | None = Field(None, min_length=1, max_length=200)
	key: str | None = Field(None, max_length=20)
	description: str | None = None
	is_private: bool | None = None
	is_archived: bool | None = None


class EpicIn(BaseModel):
	title: str = Field(min_length=1, max_length=300)
	description: str = ""


class EpicPatch(BaseModel):
	title: str | None = Field(None, min_length=1, max_length=300)
	description: str | None = None
	status: str | None = None


class StoryIn(BaseModel):
	title: str = Field(min_length=1, max_length=300)
	description: str = ""
	needs_design: bool = True


class StoryPatch(BaseModel):
	title: str | None = Field(None, min_length=1, max_length=300)
	description: str | None = None
	status: str | None = None
	needs_design: bool | None = None
	in_kanban: bool | None = None


class MemberRoleIn(BaseModel):
	role: str = Field(..., pattern=r"^(owner|member)$")


class WorkerProjectMappingIn(BaseModel):
	enabled: bool = True


class StoryTransferIn(BaseModel):
	"""T2.3 移交 story 归属：免确认、即生效。"""
	new_owner_user_id: int = Field(..., gt=0)



class StoryClaimIn(BaseModel):
	"""Worker 认领 Story 请求体（可省略；agent = worker 身份串，写入租约）。"""
	agent: str = Field(default="worker", min_length=1, max_length=100)
