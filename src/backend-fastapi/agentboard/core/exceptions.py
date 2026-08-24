"""Domain exception hierarchy.

所有业务异常继承 ``DomainError``,FastAPI 层在 ``core.api.errors`` 统一映射到 HTTP。
service 层只 ``raise``,不关心 HTTP。
"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """所有领域异常的基类。"""

    code: str = "domain_error"
    http_status: int = 500

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class NotFound(DomainError):
    """资源不存在。"""
    code = "not_found"
    http_status = 404


class InvalidValue(DomainError):
    """请求参数非法(类型/范围/格式错误)。"""
    code = "invalid_value"
    http_status = 400


class Conflict(DomainError):
    """资源冲突(唯一约束、状态机非法迁移)。"""
    code = "conflict"
    http_status = 409


# 旧 service.Duplicate 的别名(老代码大量使用,保持兼容)
Duplicate = Conflict


class Forbidden(DomainError):
    """权限不足。"""
    code = "forbidden"
    http_status = 403


class Unauthorized(DomainError):
    """未认证。"""
    code = "unauthorized"
    http_status = 401


class IllegalTransition(DomainError):
    """状态机非法迁移。"""
    code = "illegal_transition"
    http_status = 409


class PreconditionFailed(DomainError):
    """前置条件不满足(如 sprint 已 completed 不能加 task)。"""
    code = "precondition_failed"
    http_status = 412


class ExternalServiceError(DomainError):
    """外部依赖失败(COS、MQ、Agent CLI 等)。"""
    code = "external_service_error"
    http_status = 502


# 旧 service.py 里的同义异常名,保持兼容(已存在大量用法)
class ValidationError(InvalidValue):
    """兼容旧 service 抛的 ``ValidationError``。"""
