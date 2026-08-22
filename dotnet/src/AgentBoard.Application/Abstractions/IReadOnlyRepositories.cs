// SPDX-License-Identifier: MIT
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Abstractions;

/// <summary>Read-only repository contracts for the FastAPI-owned tables.</summary>
public interface IProjectRepository : IRepository<Project> { }
public interface IEpicRepository : IRepository<Epic> { }
public interface IStoryRepository : IRepository<Story> { }
public interface ITaskItemRepository : IRepository<TaskItem> { }
public interface ICommentRepository : IRepository<Comment> { }
