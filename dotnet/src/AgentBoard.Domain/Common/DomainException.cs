// SPDX-License-Identifier: MIT
namespace AgentBoard.Domain.Common;

/// <summary>
/// Base type for all domain-level exceptions. The <c>BaseController</c> in the
/// API layer maps each subclass to the corresponding HTTP status code.
/// </summary>
public abstract class DomainException : Exception
{
    protected DomainException(string message) : base(message) { }
    protected DomainException(string message, Exception inner) : base(message, inner) { }
}

/// <summary>Maps to HTTP 404.</summary>
public sealed class NotFoundException : DomainException
{
    public NotFoundException(string message) : base(message) { }
    public NotFoundException(string entity, object key)
        : base($"{entity} with key '{key}' was not found.") { }
}

/// <summary>Maps to HTTP 409.</summary>
public sealed class DuplicateException : DomainException
{
    public DuplicateException(string message) : base(message) { }
}

/// <summary>Maps to HTTP 422 (semantic validation failure).</summary>
public sealed class InvalidValueException : DomainException
{
    public InvalidValueException(string message) : base(message) { }
}

/// <summary>Maps to HTTP 400 (illegal state transition).</summary>
public sealed class IllegalTransitionException : DomainException
{
    public IllegalTransitionException(string message) : base(message) { }
}
