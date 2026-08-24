"""Request models owned by the documents feature."""

from pydantic import BaseModel, Field


class DocumentIn(BaseModel):
	project_id: int = Field(gt=0)
	title: str = Field(min_length=1, max_length=300)
	content: str = ""
	type: str = "plan"
	status: str = "draft"
	epic_id: int | None = None
	story_id: int | None = None
	folder_id: int | None = None
	author_id: int | None = None


class DocumentPatch(BaseModel):
	title: str | None = Field(None, min_length=1, max_length=300)
	content: str | None = None
	type: str | None = None
	status: str | None = None
	folder_id: int | None = None
	epic_id: int | None = None
	story_id: int | None = None


class DocumentFolderIn(BaseModel):
	project_id: int = Field(gt=0)
	name: str = Field(min_length=1, max_length=300)
	parent_id: int | None = None


class DocumentFolderPatch(BaseModel):
	name: str | None = Field(None, min_length=1, max_length=300)
	parent_id: int | None = None


class DocumentCommentIn(BaseModel):
	author: str = Field(min_length=1, max_length=100)
	content: str = Field(min_length=1)
	author_id: int | None = None


class DocumentCommentPatch(BaseModel):
	content: str = Field(min_length=1)
	author: str = Field(min_length=1, max_length=100)


class DocumentRevisionSaveIn(BaseModel):
	expected_revision_number: int
	title: str | None = None
	content: str | None = None
	change_note: str = ""
	author: str | None = None
	author_id: int | None = None


class DocumentRevisionRestoreIn(BaseModel):
	revision_number: int
	change_note: str
	author: str | None = None
	author_id: int | None = None

