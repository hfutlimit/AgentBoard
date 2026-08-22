// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Domain.Entities;
using Microsoft.EntityFrameworkCore;

namespace AgentBoard.Infrastructure.Persistence.Repositories;

public sealed class ProjectRepository : Repository<Project>, IProjectRepository
{
    public ProjectRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Project> Set => Db.Set<Project>();
}

public sealed class EpicRepository : Repository<Epic>, IEpicRepository
{
    public EpicRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Epic> Set => Db.Set<Epic>();
}

public sealed class StoryRepository : Repository<Story>, IStoryRepository
{
    public StoryRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Story> Set => Db.Set<Story>();
}

public sealed class TaskItemRepository : Repository<TaskItem>, ITaskItemRepository
{
    public TaskItemRepository(AppDbContext db) : base(db) { }
    protected override DbSet<TaskItem> Set => Db.Set<TaskItem>();
}

public sealed class CommentRepository : Repository<Comment>, ICommentRepository
{
    public CommentRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Comment> Set => Db.Set<Comment>();
}

public sealed class ProjectMemberRepository : Repository<ProjectMember>, IProjectMemberRepository
{
    public ProjectMemberRepository(AppDbContext db) : base(db) { }
    protected override DbSet<ProjectMember> Set => Db.Set<ProjectMember>();
}

public sealed class NotificationRepository : Repository<Notification>, INotificationRepository
{
    public NotificationRepository(AppDbContext db) : base(db) { }
    protected override DbSet<Notification> Set => Db.Set<Notification>();
}
