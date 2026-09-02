"""Documents service:Document / Folder / Comment / Attachment。

Phase 4 第五段:从 service.py 拆分。本文件仅作 facade 装载新模块;老 import
路径由 service.py 末尾 ``from .features.X.service import *`` 重绑保持兼容。

本文件不实现业务逻辑,只是把 service.py 里同主题的函数搬家过来 + 加必要的
import,行为完全一致。
"""
from __future__ import annotations

import logging
import os as _os
import uuid as _uuid

from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session

from ... import models  # 顶层 facade,保持兼容

log = logging.getLogger("agentboard.features.documents.service")

from ...core.exceptions import (
    Conflict, InvalidValue, NotFound,
    IllegalTransition,
)

from ...core.service_helpers import (
    _commit, _invalidate_project_stats_cache, _paginate, _required,
)

from .models import (
    ALL_DOCUMENT_STATUSES,
    ALL_DOCUMENT_TYPES,
    DOCUMENT_TRANSITIONS,
    Document,
    DocumentComment,
    DocumentFolder,
    DocumentRevision,
    DocumentStatus,
)

from ..projects.models import (
    Epic,
    Project,
    ProjectMember,
    Story,
)
from ..projects.service import readable_project_ids  # noqa: E402 — T2.1 统一读门

from ..work_items.models import (
    ATTACHMENT_ALLOWED_TYPES,
    ATTACHMENT_DIR,
    ATTACHMENT_MAX_SIZE,
    Attachment,
    Task,
)


from ..identity.models import User


class RevisionConflict(Exception):
    """文档版本冲突（由 save_document_with_revision / restore_revision 抛）。

    Attributes:
        expected: 客户端期望的 revision_number
        current: 服务端实际的当前 revision_number（乐观锁失败时）
    """
    def __init__(self, message: str = "", *, expected: int | None = None,
                 current: int | None = None, payload: dict | None = None, **kwargs):
        super().__init__(message)
        self.expected = expected
        self.current = current
        self.payload = payload or {}


def _next_revision_number(s: Session, document_id: int) -> int:
    """取当前最大 revision_number + 1；空表时返回 1。"""
    last = (
        s.query(func.max(DocumentRevision.revision_number))
        .filter(DocumentRevision.document_id == document_id)
        .scalar()
    )
    return (last or 0) + 1


def list_attachments(s: Session, task_id: int) -> list:
    if not s.get(Task, task_id):
        raise NotFound(f"task {task_id} not found")
    return s.query(Attachment).filter(Attachment.task_id == task_id).order_by(Attachment.id).all()


def delete_document(s: Session, id: int) -> bool:
    d = s.get(Document, id)
    if not d:
        return False
    # 级联删除评论（外键 ondelete=CASCADE 也会兜底）
    s.query(DocumentComment).filter(DocumentComment.document_id == id).delete(synchronize_session=False)
    s.delete(d); _commit(s); return True


def get_document_project_id(s: Session, document_id: int) -> int | None:
    d = s.get(Document, document_id)
    return d.project_id if d else None


def update_document_comment(
    s: Session, id: int, content: str, *, author: str,
) -> DocumentComment | None:
    """编辑文档评论：仅作者（成员或 Agent 账号）可编辑自己的评论。"""
    c = s.get(DocumentComment, id)
    if not c:
        return None
    content = (content or "").strip()
    if not content:
        raise InvalidValue("content is required")
    if c.author != (author or "").strip():
        raise InvalidValue("only the author can edit this comment")
    c.content = content
    _commit(s); s.refresh(c); return c


def create_attachment(s: Session, *, task_id: int, content: bytes, original_name: str, mime_type: str) -> Attachment:
    if not s.get(Task, task_id):
        raise NotFound(f"task {task_id} not found")
    if mime_type not in ATTACHMENT_ALLOWED_TYPES:
        raise InvalidValue(f"unsupported MIME type: {mime_type}")
    if len(content) > ATTACHMENT_MAX_SIZE:
        raise InvalidValue(f"file exceeds {ATTACHMENT_MAX_SIZE // (1024*1024)} MB limit")
    stored = _uuid.uuid4().hex
    path = _os.path.join(_attachment_dir(), stored)
    with open(path, "wb") as f:
        f.write(content)
    att = Attachment(task_id=task_id, filename=stored, original_name=original_name,
                     size=len(content), mime_type=mime_type)
    s.add(att); _commit(s); s.refresh(att); return att


def delete_document_comment(s: Session, id: int) -> bool:
    c = s.get(DocumentComment, id)
    if not c:
        return False
    s.delete(c); _commit(s); return True


def create_document_comment(
    s: Session, *, document_id: int, author: str, content: str,
    author_id: int | None = None,
) -> DocumentComment:
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    author = (author or "").strip()
    content = (content or "").strip()
    if not author or not content:
        raise InvalidValue("author and content are required")
    if author_id is not None and not s.get(User, author_id):
        raise InvalidValue(f"author {author_id} not found")
    c = DocumentComment(
        document_id=document_id, author=author[:100], content=content, author_id=author_id,
    )
    s.add(c); _commit(s); s.refresh(c); return c


def get_document_comment_project_id(s: Session, comment_id: int) -> int | None:
    c = s.get(DocumentComment, comment_id)
    if not c:
        return None
    d = s.get(Document, c.document_id)
    return d.project_id if d else None


# ---------- Proposals (Epic 96 P0：Proposal 澄清回路 / 人机协同需求分析) ----------

def count_document_comments(s: Session, document_id: int) -> int:
    """返回指定文档的评论总数。文档不存在抛 NotFound。"""
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    return (
        s.query(func.count(DocumentComment.id))
        .filter(DocumentComment.document_id == document_id)
        .scalar()
        or 0
    )


# ---------------------------------------------------------------------------
# Epic 139：DocumentRevision（不可变快照）+ 乐观锁
# ---------------------------------------------------------------------------


def list_document_comments(s: Session, document_id: int):
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    return (
        s.query(DocumentComment)
        .filter(DocumentComment.document_id == document_id)
        .order_by(DocumentComment.created_at, DocumentComment.id)
        .all()
    )


def create_document_folder(
    s: Session, *, project_id: int, name: str, parent_id: int | None = None,
) -> DocumentFolder:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    name = _required(name, "name", 300)
    if parent_id is not None:
        _check_document_folder(s, parent_id, project_id)
    f = DocumentFolder(project_id=project_id, parent_id=parent_id, name=name)
    s.add(f); _commit(s); s.refresh(f); return f


def list_documents(
    s: Session, *, project_id: int | None = None, type: str | None = None,
    status: str | None = None, q: str | None = None,
    folder_id: int | None = None, author_id: int | None = None,
    epic_id: int | None = None, story_id: int | None = None,
    sort: str | None = None,
    limit: int | None = None, offset: int = 0, user_id: int | None = None,
):
    """列出文档，支持丰富的过滤与稳定的排序（向后兼容）。"""
    qry = s.query(Document)
    if project_id is not None:
        qry = qry.filter(Document.project_id == project_id)
    elif user_id is not None:
        # 未指定 project_id 但有用户身份：仅返回该用户有权限的项目文档
        # （T2.1：改走统一读门 readable_project_ids —— 原先这里内联了一份
        #  member_pids 查询，判据散第二份就会漂移）
        user = s.get(User, user_id)
        readable = readable_project_ids(
            s, user_id, is_admin=bool(user and user.is_admin))
        if readable is not None:
            qry = qry.filter(Document.project_id.in_(readable)) if readable \
                else qry.filter(False)  # 非 admin 无成员项目 → 空
    if type is not None:
        _check_document_type(type)
        qry = qry.filter(Document.type == type)
    if status is not None:
        _check_document_status(status)
        qry = qry.filter(Document.status == status)
    if folder_id is not None:
        qry = qry.filter(Document.folder_id == folder_id)
    if author_id is not None:
        qry = qry.filter(Document.author_id == author_id)
    if epic_id is not None:
        qry = qry.filter(Document.epic_id == epic_id)
    if story_id is not None:
        qry = qry.filter(Document.story_id == story_id)
    if q:
        like = f"%{q}%"
        qry = qry.filter(or_(Document.title.ilike(like), Document.content.ilike(like)))
    # 排序：白名单控制，缺省 updated 倒序（与历史行为一致）
    sort_key = (sort or "updated").lower()
    if sort_key not in _DOCUMENT_SORT_WHITELIST:
        raise InvalidValue(
            f"invalid sort '{sort}' (allowed: {sorted(_DOCUMENT_SORT_WHITELIST)})"
        )
    if sort_key == "updated":
        qry = qry.order_by(Document.updated_at.desc(), Document.id.desc())
    elif sort_key == "created":
        qry = qry.order_by(Document.created_at.desc(), Document.id.desc())
    else:  # title
        qry = qry.order_by(Document.title.asc(), Document.id.desc())
    return _paginate(qry, limit, offset).all()


def delete_attachment(s: Session, id: int) -> bool:
    att = s.get(Attachment, id)
    if not att:
        return False
    path = _os.path.join(ATTACHMENT_DIR, att.filename)
    if _os.path.isfile(path):
        _os.unlink(path)
    s.delete(att); _commit(s); return True


# ---------- AgentSchedule / AgentRun ----------
import re as _re

_CRON_PATTERN = _re.compile(
    # 支持 */n 步长语法（如 */1 每分钟，*/5 每5分钟）
    r"^(\*(?:/\d+)?|[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?(?:,[0-5]?\d(?:-[0-5]?\d(?:/\d+)?)?)*)\s+"
    r"(\*(?:/\d+)?|1?\d|2[0-3])(?:-[1-2]?\d(?:/\d+)?)?(?:,(?:1?\d|#[0-3]))*\s+"
    r"(\*(?:/\d+)?|[1-2]?\d|3[01])(?:-[1-3]?\d(?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|1?\d|1[0-2])(?:-1[0-2](?:/\d+)?)?(?:,\d+(?:-\d+(?:/\d+)?)?)*\s+"
    r"(\*(?:/\d+)?|[0-7])(?:-[0-7](?:/\d+)?)?(?:,[0-7](?:-[0-7](?:/\d+)?)?)*$"
)


def _check_document_type(value: str) -> None:
    if value not in ALL_DOCUMENT_TYPES:
        raise InvalidValue(f"invalid document type '{value}'")


def get_attachment_path(att: Attachment) -> str:
    return _os.path.join(ATTACHMENT_DIR, att.filename)


def delete_document_folder(s: Session, id: int) -> bool:
    """删除文件夹：直接子文档与子文件夹上提至被删文件夹的父级（根则置 NULL）。

    不级联删除子项，避免用户误删文件夹时连带丢失文档。
    """
    f = s.get(DocumentFolder, id)
    if not f:
        return False
    parent_id = f.parent_id
    s.query(Document).filter(Document.folder_id == id).update(
        {Document.folder_id: parent_id}, synchronize_session=False,
    )
    s.query(DocumentFolder).filter(DocumentFolder.parent_id == id).update(
        {DocumentFolder.parent_id: parent_id}, synchronize_session=False,
    )
    s.delete(f); _commit(s); return True


def _check_document_status(value: str) -> None:
    if value not in ALL_DOCUMENT_STATUSES:
        raise InvalidValue(f"invalid document status '{value}'")


def save_document_with_revision(
    s: Session, *, id: int, expected_revision_number: int,
    title: str | None = None, content: str | None = None,
    change_note: str, author_id: int | None = None, author: str | None = None,
    # 头部元数据透传（type / status / folder / epic / story 不算"内容变更"，
    # 不产生 revision；与 KV 决策一致；如需触发 revision，请走纯内容路径）
) -> Document:
    """乐观锁保存：在事务内校验 expected_revision_number、插入新 revision、更新头指针。

    - 标题 / 内容变更才形成新 revision；其他字段单独走 update_document()；
    - current_revision_number 不匹配 → 抛 RevisionConflict；
    - 返回更新后的 Document（含 current_revision_id / current_revision_number）。
    """
    d = s.get(Document, id)
    if not d:
        raise NotFound(f"document {id} not found")
    current_rev = d.current_revision_number or 0
    if expected_revision_number != current_rev:
        raise RevisionConflict(expected=expected_revision_number, current=current_rev)
    new_title = title if title is not None else d.title
    new_content = content if content is not None else d.content
    # 若标题/内容未变化，直接返回当前 Document（不浪费 revision_number）
    if new_title == d.title and new_content == d.content:
        return d
    # 1) 追加 revision
    rev = DocumentRevision(
        document_id=id,
        revision_number=_next_revision_number(s, id),
        title=_required(new_title, "title", 300),
        content=new_content or "",
        author_id=author_id, author=author,
        change_note=(change_note or "").strip()[:500],
        is_restore=False, restored_from_revision=None,
    )
    s.add(rev); s.flush()
    # 2) 更新头指针 + 冗余字段
    d.title = rev.title
    d.content = rev.content
    d.current_revision_id = rev.id
    d.current_revision_number = rev.revision_number
    _commit(s); s.refresh(d); s.refresh(rev)
    return d


def get_document_folder_project_id(s: Session, folder_id: int) -> int | None:
    f = s.get(DocumentFolder, folder_id)
    return f.project_id if f else None


def get_document(s: Session, id: int) -> Document | None:
    return s.get(Document, id)


_DOCUMENT_SORT_WHITELIST = {"updated", "created", "title"}


def set_document_status(s: Session, id: int, new_status: str) -> Document | None:
    d = s.get(Document, id)
    if not d:
        raise NotFound(f"document {id} not found")
    _check_document_status(new_status)
    new = DocumentStatus(new_status)
    current = DocumentStatus(d.status)
    if current != new and new not in DOCUMENT_TRANSITIONS.get(current, set()):
        raise IllegalTransition(f"{d.status} -> {new} 不合法")
    d.status = new
    _commit(s); s.refresh(d); return d


def get_attachment(s: Session, id: int) -> Attachment | None:
    return s.get(Attachment, id)


def _check_document_links(s: Session, *, project_id: int, epic_id: int | None,
                          story_id: int | None) -> None:
    """校验文档关联的 epic/story 存在且属于同一项目（目录结构一致性）。

    - epic_id：必须存在且 ``epic.project_id == project_id``；
    - story_id：必须存在且其所属 epic 属于该项目；若同时指定 epic_id，
      story 必须属于该 epic（防止文档的 story/epic 关联错位导致目录结构混乱）。
    """
    if epic_id is not None:
        e = s.get(Epic, epic_id)
        if not e:
            raise InvalidValue(f"epic {epic_id} not found")
        if e.project_id != project_id:
            raise InvalidValue(f"epic {epic_id} 不属于项目 {project_id}")
    if story_id is not None:
        st = s.get(Story, story_id)
        if not st:
            raise InvalidValue(f"story {story_id} not found")
        st_epic = s.get(Epic, st.epic_id)
        if not st_epic or st_epic.project_id != project_id:
            raise InvalidValue(f"story {story_id} 不属于项目 {project_id}")
        if epic_id is not None and st.epic_id != epic_id:
            raise InvalidValue(f"story {story_id} 不属于 epic {epic_id}")


def update_document(s: Session, id: int, **fields) -> Document | None:
    d = s.get(Document, id)
    if not d:
        return None
    allowed = {"title", "content", "type", "status", "folder_id", "epic_id", "story_id"}
    # 关联字段暂存新值：统一校验通过后再落对象，异常时不污染对象状态
    new_epic = fields.get("epic_id", d.epic_id)
    new_story = fields.get("story_id", d.story_id)
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("epic_id", "story_id"):
            continue  # 循环后统一校验并赋值
        if k == "folder_id":
            # 显式 null = 移出文件夹到根目录（合法值，不可跳过）
            if v is not None:
                _check_document_folder(s, v, d.project_id)
            d.folder_id = v
            continue
        if v is None:
            continue
        if k == "title":
            v = _required(v, "title", 300)
        elif k == "type":
            _check_document_type(v)
        elif k == "status":
            _check_document_status(v)
            new = DocumentStatus(v)
            current = DocumentStatus(d.status)
            if current != new and new not in DOCUMENT_TRANSITIONS.get(current, set()):
                raise IllegalTransition(f"{d.status} -> {new.value} 不合法")
            d.status = new.value
            status_changed = True
            continue
        setattr(d, k, v)
    # 关联一致性校验：仅本次修改了 epic/story 关联时执行（历史脏数据不阻塞普通更新）
    if "epic_id" in fields or "story_id" in fields:
        _check_document_links(s, project_id=d.project_id, epic_id=new_epic, story_id=new_story)
        d.epic_id = new_epic
        d.story_id = new_story
    _commit(s); s.refresh(d); return d


def update_document_folder(
    s: Session, id: int, **fields,
) -> DocumentFolder | None:
    f = s.get(DocumentFolder, id)
    if not f:
        return None
    if "name" in fields and fields["name"] is not None:
        f.name = _required(fields["name"], "name", 300)
    if "parent_id" in fields:
        new_parent = fields["parent_id"]
        if new_parent is not None:
            _check_document_folder(s, new_parent, f.project_id)
            if _folder_is_descendant(s, id, new_parent):
                raise InvalidValue("cannot move a folder into itself or its descendant")
        f.parent_id = new_parent
    _commit(s); s.refresh(f); return f


def _check_document_folder(s: Session, folder_id: int, project_id: int) -> DocumentFolder:
    """校验文件夹存在且属于指定项目；通过则返回该文件夹，否则抛 InvalidValue。"""
    f = s.get(DocumentFolder, folder_id)
    if not f:
        raise InvalidValue(f"folder {folder_id} not found")
    if f.project_id != project_id:
        raise InvalidValue("folder does not belong to the document's project")
    return f


def get_attachment_project_id(s: Session, attachment_id: int) -> int | None:
    a = s.get(Attachment, attachment_id)
    if not a:
        return None
    # get_task_project_id 定义于 projects service（附件挂 task 取项目归属）
    from ..projects.service import get_task_project_id
    return get_task_project_id(s, a.task_id)


def list_document_folders(
    s: Session, *, project_id: int | None = None, user_id: int | None = None,
):
    """列出文件夹（含所有层级，由前端组装树）。

    权限口径与 list_documents 一致：指定 project_id 时按项目过滤；
    未指定但带用户身份时，仅返回该用户有权限项目的文件夹。
    """
    qry = s.query(DocumentFolder)
    if project_id is not None:
        qry = qry.filter(DocumentFolder.project_id == project_id)
    elif user_id is not None:
        # 同 list_documents：T2.1 统一读门（原先这里也内联了一份 member_pids）
        user = s.get(User, user_id)
        readable = readable_project_ids(
            s, user_id, is_admin=bool(user and user.is_admin))
        if readable is not None:
            qry = qry.filter(DocumentFolder.project_id.in_(readable)) if readable \
                else qry.filter(False)
    return qry.order_by(DocumentFolder.name, DocumentFolder.id).all()


def _attachment_dir() -> str:
    _os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    return ATTACHMENT_DIR


def create_document(
    s: Session, *, project_id: int, title: str, content: str = "",
    type: str = "plan", status: str = "draft",
    epic_id: int | None = None, story_id: int | None = None,
    folder_id: int | None = None, author_id: int | None = None,
) -> Document:
    if not s.get(Project, project_id):
        raise NotFound(f"project {project_id} not found")
    _check_document_links(s, project_id=project_id, epic_id=epic_id, story_id=story_id)
    if folder_id is not None:
        _check_document_folder(s, folder_id, project_id)
    _check_document_type(type)
    _check_document_status(status)
    if author_id is not None and not s.get(User, author_id):
        raise InvalidValue(f"author {author_id} not found")
    title = _required(title, "title", 300)
    doc = Document(
        project_id=project_id, epic_id=epic_id, story_id=story_id,
        title=title, content=content or "",
        type=type, status=status, folder_id=folder_id, author_id=author_id,
    )
    s.add(doc); s.flush()
    # Epic 139：创建文档时同步生成 revision 1；旧文档无 revision 也能正常工作（current_revision_id 留 NULL）。
    rev = DocumentRevision(
        document_id=doc.id, revision_number=1, title=title, content=content or "",
        author_id=author_id, change_note="初始版本",
    )
    s.add(rev); s.flush()
    doc.current_revision_id = rev.id
    doc.current_revision_number = rev.revision_number
    _commit(s); s.refresh(doc); return doc




# ---- 同步自 service.py ----
def _folder_is_descendant(s: Session, folder_id: int, ancestor_id: int) -> bool:
    """ancestor_id 是否为 folder_id 的祖先（含自身）？用于移动文件夹时防环。"""
    cur: int | None = ancestor_id
    seen: set[int] = set()
    while cur is not None:
        if cur == folder_id:
            return True
        if cur in seen:
            return False
        seen.add(cur)
        f = s.get(DocumentFolder, cur)
        cur = f.parent_id if f else None
    return False

# ---- 同步自 service.py ----
def create_revision(
    s: Session, *, document_id: int, title: str, content: str,
    change_note: str, author_id: int | None = None, author: str | None = None,
    is_restore: bool = False, restored_from_revision: int | None = None,
) -> DocumentRevision:
    """在事务中追加一条不可变 revision；调用方负责 _commit。文档不存在抛 NotFound。

    不会触碰 Document 头；如需同步 current_revision_id / current_revision_number，
    请走 save_document_with_revision()。
    """
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    change_note = (change_note or "").strip()[:500]
    rev = DocumentRevision(
        document_id=document_id,
        revision_number=_next_revision_number(s, document_id),
        title=_required(title, "title", 300),
        content=content or "",
        author_id=author_id, author=author,
        change_note=change_note,
        is_restore=is_restore, restored_from_revision=restored_from_revision,
    )
    s.add(rev); _commit(s); s.refresh(rev); return rev

# ---- 同步自 service.py ----
def list_revisions(
    s: Session, document_id: int, *, limit: int | None = None, offset: int = 0,
):
    """按 revision_number 倒序列出；含 current_revision_number 头指针信息。"""
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    qry = (
        s.query(DocumentRevision)
        .filter(DocumentRevision.document_id == document_id)
        .order_by(DocumentRevision.revision_number.desc())
    )
    return _paginate(qry, limit, offset).all()

# ---- 同步自 service.py ----
def get_revision(s: Session, document_id: int, revision_number: int) -> DocumentRevision:
    """取指定 revision；不存在抛 NotFound。"""
    if not s.get(Document, document_id):
        raise NotFound(f"document {document_id} not found")
    rev = (
        s.query(DocumentRevision)
        .filter(
            DocumentRevision.document_id == document_id,
            DocumentRevision.revision_number == revision_number,
        )
        .first()
    )
    if not rev:
        raise NotFound(f"document {document_id} revision {revision_number} not found")
    return rev

# ---- 同步自 service.py ----
def restore_revision(
    s: Session, *, id: int, revision_number: int,
    change_note: str, author_id: int | None = None, author: str | None = None,
) -> Document:
    """把旧版 content 复制为新 revision（不修改历史）。返回更新后的 Document。

    - 新 revision_number = max + 1；change_note 必填并自动加前缀「回滚自 r{N}」；
    - current_revision_id / current_revision_number 指向新 revision；
    - 旧 revision 保持不变。
    """
    d = s.get(Document, id)
    if not d:
        raise NotFound(f"document {id} not found")
    src = get_revision(s, id, revision_number)
    note = (change_note or "").strip()[:500]
    if not note:
        raise InvalidValue("change_note is required for restore")
    new_rev = DocumentRevision(
        document_id=id,
        revision_number=_next_revision_number(s, id),
        title=src.title,
        content=src.content,
        author_id=author_id, author=author,
        change_note=f"回滚自 r{revision_number}：{note}",
        is_restore=True, restored_from_revision=revision_number,
    )
    s.add(new_rev); s.flush()
    d.title = src.title
    d.content = src.content
    d.current_revision_id = new_rev.id
    d.current_revision_number = new_rev.revision_number
    _commit(s); s.refresh(d); s.refresh(new_rev)
    return d