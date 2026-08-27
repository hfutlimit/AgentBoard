"""Documents feature router (Phase 5 split from api.py)。

Phase 5:从 api.py 拆出的 FastAPI 路由。179 个端点按 2nd path segment 分组,
本文件包含本 feature 的所有 @router.X 端点。

老 import ``from agentboard import api; api.app`` 仍可用(api.py 末尾
``app.include_router(...)`` 装配所有 router)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request, UploadFile, File, WebSocket, WebSocketDisconnect, Body
from sqlalchemy.orm import Session

from ...core.infrastructure.database import get_session
from ...core.application import service
from ...core.api.schemas import StatusIn
from .schemas import (
	DocumentCommentIn,
	DocumentCommentPatch,
	DocumentFolderIn,
	DocumentFolderPatch,
	DocumentIn,
	DocumentPatch,
	DocumentRevisionRestoreIn,
	DocumentRevisionSaveIn,
)
from ... import api_helpers  # Phase 5: _current_user, _auth_is_required, etc.

router = APIRouter(tags=["documents"])


@router.post("/api/documents", status_code=201)
def create_document(body: DocumentIn, s: Session = Depends(get_session),
                    authorization: str | None = Header(None)):
    """新建文档（title/content/type/project_id 必填，status 默认 draft）。

    权限控制（2026-07-21）：需为目标项目成员或管理员。
    """
    # 权限检查：必须在目标项目中是成员或管理员
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if not is_admin and not service.user_is_project_member(s, body.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        d = service.create_document(
            s, project_id=body.project_id, title=body.title, content=body.content,
            type=body.type, status=body.status, epic_id=body.epic_id,
            story_id=body.story_id, folder_id=body.folder_id, author_id=body.author_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(d)



@router.get("/api/document-folders", response_model=None)
def list_document_folders(
    project_id: int | None = Query(None),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """列出项目文档文件夹（含全部层级，前端组装树）。

    权限：与文档列表一致——指定 project_id 时由中间件校验成员身份；
    未指定时仅返回当前用户有权限项目的文件夹。
    """
    uid = api_helpers._optional_user_id(authorization, s)
    return [service._ser(f) for f in service.list_document_folders(
        s, project_id=project_id, user_id=uid,
    )]



@router.post("/api/document-folders", status_code=201)
def create_document_folder(body: DocumentFolderIn, s: Session = Depends(get_session),
                           authorization: str | None = Header(None)):
    """新建文档文件夹（name 必填，parent_id 可选 = 创建子文件夹）。

    权限：需为目标项目成员或管理员。
    """
    uid, is_admin = api_helpers._caller_uid_admin(authorization)
    if not is_admin and not service.user_is_project_member(s, body.project_id, uid):
        raise HTTPException(status_code=403, detail="project membership required")
    try:
        f = service.create_document_folder(
            s, project_id=body.project_id, name=body.name, parent_id=body.parent_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(f)



@router.patch("/api/document-folders/{fid}")
def update_document_folder(fid: int, body: DocumentFolderPatch, s: Session = Depends(get_session)):
    """重命名 / 移动文件夹。parent_id=null 移动到根目录；防环校验由 service 完成。"""
    try:
        fields = body.model_dump(exclude_none=True)
        if "parent_id" in body.model_fields_set:
            fields["parent_id"] = body.parent_id  # 显式 null = 移动到根
        r = service.update_document_folder(s, fid, **fields)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(api_helpers._need(r, "document folder"))



@router.delete("/api/document-folders/{fid}")
def delete_document_folder(fid: int, s: Session = Depends(get_session)):
    """删除文件夹：直接子文档与子文件夹上提至父级，不级联删除子项。"""
    if not service.delete_document_folder(s, fid):
        raise HTTPException(status_code=404, detail="document folder not found")
    return {"ok": True}



@router.get("/api/documents")
def list_documents(
    project_id: int | None = Query(None),
    type: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    folder_id: int | None = Query(None),
    author_id: int | None = Query(None),
    epic_id: int | None = Query(None),
    story_id: int | None = Query(None),
    sort: str | None = Query(None),
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
    authorization: str | None = Header(None),
):
    """列出文档，支持按 project_id / folder_id / author_id / epic_id / story_id / type / status 过滤
    与关键词搜索；sort ∈ {updated, created, title}（默认 updated 倒序）。

    权限控制（2026-07-21）：
    - 指定 project_id 时：通过中间件校验项目成员身份
    - 未指定 project_id 时：仅返回用户有权限的项目文档
    """
    uid = api_helpers._optional_user_id(authorization, s)
    try:
        rows = service.list_documents(
            s, project_id=project_id, type=type, status=status, q=q,
            folder_id=folder_id, author_id=author_id, epic_id=epic_id, story_id=story_id,
            sort=sort, limit=limit, offset=offset, user_id=uid,
        )
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return [service._ser(d) for d in rows]



@router.get("/api/documents/{did}")
def get_document(did: int, s: Session = Depends(get_session)):
    return service._ser(api_helpers._need(service.get_document(s, did), "document"))



@router.patch("/api/documents/{did}")
def update_document(did: int, body: DocumentPatch, s: Session = Depends(get_session)):
    """编辑文档 title/content/type（状态流转请用 PUT /status）。

    folder_id 显式传 null 表示移出文件夹到根目录；未传该字段则保持不变。
    """
    try:
        fields = body.model_dump(exclude_none=True)
        if "folder_id" in body.model_fields_set:
            fields["folder_id"] = body.folder_id
        # epic_id/story_id 显式 null = 清空关联（exclude_none 会吞 null，需按 fields_set 还原）
        if "epic_id" in body.model_fields_set:
            fields["epic_id"] = body.epic_id
        if "story_id" in body.model_fields_set:
            fields["story_id"] = body.story_id
        r = service.update_document(s, did, **fields)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(api_helpers._need(r, "document"))



@router.put("/api/documents/{did}/status")
def set_document_status(did: int, body: StatusIn, s: Session = Depends(get_session)):
    """文档评审状态流转：draft→in_review→approved/cancelled/draft；approved→draft。非法迁移返回 400。"""
    try:
        result = service.set_document_status(s, did, body.status)
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.IllegalTransition as e:
        raise HTTPException(status_code=400, detail=str(e))
    return service._ser(api_helpers._need(result, "document"))



@router.delete("/api/documents/{did}")
def delete_document(did: int, s: Session = Depends(get_session)):
    if not service.delete_document(s, did):
        raise HTTPException(status_code=404, detail="document not found")
    return {"ok": True}



@router.post("/api/documents/{did}/comments", status_code=201)
def create_document_comment(
    did: int, body: DocumentCommentIn, authorization: str | None = Header(None),
    s: Session = Depends(get_session),
):
    """对文档添加评论（markdown），author 为成员或 Agent 账号名。"""
    try:
        author = api_helpers.resolve_comment_author(authorization, s, body.author)
        c = service.create_document_comment(
            s, document_id=did, author=author, content=body.content,
            author_id=body.author_id,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(c)



@router.get("/api/documents/{did}/comments")
def list_document_comments(did: int, s: Session = Depends(get_session)):
    """列出文档评论，按 created_at 正序。"""
    try:
        return [service._ser(x) for x in service.list_document_comments(s, did)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.get("/api/documents/{did}/comments/count")
def count_document_comments(did: int, s: Session = Depends(get_session)):
    """返回文档评论总数。供列表视图按需并发取数（Epic 138 文档列表 + 过滤增强）。"""
    try:
        return {"count": service.count_document_comments(s, did)}
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---------------------------------------------------------------------------
# Epic 139：DocumentRevision（不可变快照 + 乐观锁）
# ---------------------------------------------------------------------------


@router.get("/api/documents/{did}/revisions")
def list_document_revisions(
    did: int, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    s: Session = Depends(get_session),
):
    """按 revision_number 倒序列出历史快照。"""
    try:
        return [service._ser(r) for r in service.list_revisions(s, did, limit=limit, offset=offset)]
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.get("/api/documents/{did}/revisions/{revision_number}")
def get_document_revision(did: int, revision_number: int, s: Session = Depends(get_session)):
    """取指定 revision（用于 diff / 恢复）。"""
    try:
        return service._ser(service.get_revision(s, did, revision_number))
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))



@router.post("/api/documents/{did}/revisions", status_code=201)
def save_document_revision(did: int, body: DocumentRevisionSaveIn, s: Session = Depends(get_session)):
    """乐观锁保存（带 expected_revision_number）；并发冲突 → 409。

    Body 必须含 title 或 content 至少一项；change_note 必填（≤500 字）。
    """
    if body.title is None and body.content is None:
        raise HTTPException(status_code=422, detail="title or content is required")
    if not (body.change_note or "").strip():
        raise HTTPException(status_code=422, detail="change_note is required")
    try:
        doc = service.save_document_with_revision(
            s, id=did, expected_revision_number=body.expected_revision_number,
            title=body.title, content=body.content, change_note=body.change_note,
            author_id=body.author_id, author=body.author,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.RevisionConflict as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "revision_conflict",
                "message": str(e),
                "expected": e.expected,
                "current": e.current,
            },
        )
    return service._ser(doc)



@router.post("/api/documents/{did}/revisions/restore", status_code=200)
def restore_document_revision(did: int, body: DocumentRevisionRestoreIn, s: Session = Depends(get_session)):
    """把旧版内容复制为新 revision（不修改历史）。"""
    try:
        doc = service.restore_revision(
            s, id=did, revision_number=body.revision_number,
            change_note=body.change_note, author_id=body.author_id, author=body.author,
        )
    except service.NotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(doc)



@router.patch("/api/document-comments/{cid}")
def update_document_comment(cid: int, body: DocumentCommentPatch, s: Session = Depends(get_session)):
    """编辑文档评论：仅作者（成员或 Agent 账号）可编辑自己的评论。"""
    try:
        c = service.update_document_comment(s, cid, content=body.content, author=body.author)
    except service.InvalidValue as e:
        raise HTTPException(status_code=422, detail=str(e))
    return service._ser(api_helpers._need(c, "comment"))



@router.delete("/api/document-comments/{cid}")
def delete_document_comment(cid: int, s: Session = Depends(get_session)):
    if not service.delete_document_comment(s, cid):
        raise HTTPException(status_code=404, detail="comment not found")
    return {"ok": True}


# ---------- Proposals (Epic 96 P0：Proposal 澄清回路 / 人机协同需求分析) ----------
