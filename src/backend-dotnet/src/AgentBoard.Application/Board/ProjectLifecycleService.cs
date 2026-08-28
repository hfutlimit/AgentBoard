// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Board.Dtos;
using AgentBoard.Application.Events;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Entities;

namespace AgentBoard.Application.Board;

/// <summary>
/// Owns multi-aggregate project create/delete operations and their transaction
/// boundary. The board facade delegates here instead of coordinating every
/// project child repository itself.
/// </summary>
public sealed class ProjectLifecycleService : IProjectLifecycleService
{
	private readonly IProjectRepository _projects;
	private readonly IEpicRepository _epics;
	private readonly IStoryRepository _stories;
	private readonly ITaskItemRepository _tasks;
	private readonly ICommentRepository _comments;
	private readonly IProjectMemberRepository _members;
	private readonly ITaskDependencyRepository _dependencies;
	private readonly IStoryStatusHistoryRepository _storyHistory;
	private readonly ITaskStatusHistoryRepository _taskHistory;
	private readonly IAttachmentRepository _attachments;
	private readonly ISprintRepository _sprints;
	private readonly IAgentScheduleRepository _schedules;
	private readonly IAgentRunRepository _runs;
	private readonly IWebhookConfigRepository _webhooks;
	private readonly IDocumentRepository _documents;
	private readonly IDocumentRevisionRepository _documentRevisions;
	private readonly IDocumentFolderRepository _documentFolders;
	private readonly IDocumentCommentRepository _documentComments;
	private readonly IUnitOfWork _uow;
	private readonly IApplicationEventPublisher _events;

	public ProjectLifecycleService(
		IProjectRepository projects,
		IEpicRepository epics,
		IStoryRepository stories,
		ITaskItemRepository tasks,
		ICommentRepository comments,
		IProjectMemberRepository members,
		ITaskDependencyRepository dependencies,
		IStoryStatusHistoryRepository storyHistory,
		ITaskStatusHistoryRepository taskHistory,
		IAttachmentRepository attachments,
		ISprintRepository sprints,
		IAgentScheduleRepository schedules,
		IAgentRunRepository runs,
		IWebhookConfigRepository webhooks,
		IDocumentRepository documents,
		IDocumentRevisionRepository documentRevisions,
		IDocumentFolderRepository documentFolders,
		IDocumentCommentRepository documentComments,
		IUnitOfWork uow,
		IApplicationEventPublisher events)
	{
		_projects = projects ?? throw new ArgumentNullException(nameof(projects));
		_epics = epics ?? throw new ArgumentNullException(nameof(epics));
		_stories = stories ?? throw new ArgumentNullException(nameof(stories));
		_tasks = tasks ?? throw new ArgumentNullException(nameof(tasks));
		_comments = comments ?? throw new ArgumentNullException(nameof(comments));
		_members = members ?? throw new ArgumentNullException(nameof(members));
		_dependencies = dependencies ?? throw new ArgumentNullException(nameof(dependencies));
		_storyHistory = storyHistory ?? throw new ArgumentNullException(nameof(storyHistory));
		_taskHistory = taskHistory ?? throw new ArgumentNullException(nameof(taskHistory));
		_attachments = attachments ?? throw new ArgumentNullException(nameof(attachments));
		_sprints = sprints ?? throw new ArgumentNullException(nameof(sprints));
		_schedules = schedules ?? throw new ArgumentNullException(nameof(schedules));
		_runs = runs ?? throw new ArgumentNullException(nameof(runs));
		_webhooks = webhooks ?? throw new ArgumentNullException(nameof(webhooks));
		_documents = documents ?? throw new ArgumentNullException(nameof(documents));
		_documentRevisions = documentRevisions ?? throw new ArgumentNullException(nameof(documentRevisions));
		_documentFolders = documentFolders ?? throw new ArgumentNullException(nameof(documentFolders));
		_documentComments = documentComments ?? throw new ArgumentNullException(nameof(documentComments));
		_uow = uow ?? throw new ArgumentNullException(nameof(uow));
		_events = events ?? throw new ArgumentNullException(nameof(events));
	}

	public async Task<ProjectDto> CreateAsync(
		CreateProjectRequest request, int? currentUserId,
		CancellationToken ct = default)
	{
		var name = (request.Name ?? string.Empty).Trim();
		if (name.Length == 0 || name.Length > 200)
			throw new InvalidValueException("name must be 1-200 characters");
		var key = request.Key;
		var description = request.Description;
		if (!string.IsNullOrWhiteSpace(key))
		{
			key = key.Trim();
			if (key.Length > 20)
				throw new InvalidValueException("key must be at most 20 characters");
		}
		else
		{
			key = null;
		}

		var project = new Project
		{
			Name = name,
			Key = key,
			Description = description ?? string.Empty,
			IsPrivate = true,
			CreatedAt = DateTime.UtcNow,
		};

		if (project.Key is not null && (await _projects.ListAsync(p => p.Key == project.Key, ct)).Count != 0)
			throw new DuplicateException($"project key '{project.Key}' already exists");

		await using var transaction = await _uow.BeginTransactionAsync(ct);
		try
		{
			await _projects.AddAsync(project, ct);
			await _uow.SaveChangesAsync(ct);
			if (currentUserId is not null)
			{
				await _members.AddAsync(new ProjectMember
				{
					ProjectId = project.Id,
					UserId = currentUserId.Value,
					Role = "owner",
					JoinedAt = DateTime.UtcNow,
				}, ct);
				await _uow.SaveChangesAsync(ct);
			}
			await transaction.CommitAsync(ct);
		}
		catch
		{
			await transaction.RollbackAsync(ct);
			throw;
		}

		await _events.PublishAsync(new ProjectCreatedEvent(project.Id, project.Name, currentUserId, DateTime.UtcNow), ct);
		return ToProjectDto(project);
	}

	public async Task<bool> DeleteAsync(int id, CancellationToken ct = default)
	{
		var project = await _projects.GetByIdAsync(id, ct);
		if (project is null) return false;

		var epics = await _epics.ListAsync(e => e.ProjectId == id, ct);
		var epicIds = epics.Select(e => e.Id).ToHashSet();
		var stories = epicIds.Count == 0
			? Array.Empty<Story>()
			: await _stories.ListAsync(s => epicIds.Contains(s.EpicId), ct);
		var storyIds = stories.Select(s => s.Id).ToHashSet();
		var tasks = await _tasks.ListAsync(t => t.ProjectId == id, ct);
		var taskIds = tasks.Select(t => t.Id).ToHashSet();
		var documents = await _documents.ListAsync(d => d.ProjectId == id, ct);
		var documentIds = documents.Select(d => d.Id).ToHashSet();
		var folders = await _documentFolders.ListAsync(f => f.ProjectId == id, ct);
		var comments = await _comments.ListAsync(c =>
			(c.TaskId.HasValue && taskIds.Contains(c.TaskId.Value))
			|| (c.StoryId.HasValue && storyIds.Contains(c.StoryId.Value))
			|| (c.EpicId.HasValue && epicIds.Contains(c.EpicId.Value)), ct);
		var attachments = taskIds.Count == 0 ? Array.Empty<Attachment>() : await _attachments.ListAsync(a => taskIds.Contains(a.TaskId), ct);
		var dependencies = taskIds.Count == 0 ? Array.Empty<TaskDependency>() : await _dependencies.ListAsync(d => taskIds.Contains(d.TaskId) || taskIds.Contains(d.DependsOnId), ct);
		var taskHistory = taskIds.Count == 0 ? Array.Empty<TaskStatusHistory>() : await _taskHistory.ListAsync(h => taskIds.Contains(h.TaskId), ct);
		var storyHistory = storyIds.Count == 0 ? Array.Empty<StoryStatusHistory>() : await _storyHistory.ListAsync(h => storyIds.Contains(h.StoryId), ct);
		var documentRevisions = documentIds.Count == 0 ? Array.Empty<DocumentRevision>() : await _documentRevisions.ListAsync(r => documentIds.Contains(r.DocumentId), ct);
		var documentComments = documentIds.Count == 0 ? Array.Empty<DocumentComment>() : await _documentComments.ListAsync(c => documentIds.Contains(c.DocumentId), ct);
		var webhooks = await _webhooks.ListAsync(w => w.ProjectId == id, ct);
		var sprints = await _sprints.ListAsync(s => s.ProjectId == id, ct);
		var schedules = await _schedules.ListAsync(s => s.ProjectId == id, ct);
		var scheduleIds = schedules.Select(s => s.Id).ToHashSet();
		var runs = scheduleIds.Count == 0
			? Array.Empty<AgentRun>()
			: await _runs.ListAsync(r => scheduleIds.Contains(r.ScheduleId), ct);
		var members = await _members.ListAsync(m => m.ProjectId == id, ct);

		await using var transaction = await _uow.BeginTransactionAsync(ct);
		try
		{
			_comments.RemoveRange(comments);
			_attachments.RemoveRange(attachments);
			_dependencies.RemoveRange(dependencies);
			_taskHistory.RemoveRange(taskHistory);
			_storyHistory.RemoveRange(storyHistory);
			_documentComments.RemoveRange(documentComments);
			_documentRevisions.RemoveRange(documentRevisions);
			_documents.RemoveRange(documents);
			_documentFolders.RemoveRange(folders);
			_webhooks.RemoveRange(webhooks);
			_sprints.RemoveRange(sprints);
			_runs.RemoveRange(runs);
			_schedules.RemoveRange(schedules);
			_tasks.RemoveRange(tasks);
			_stories.RemoveRange(stories);
			_epics.RemoveRange(epics);
			_members.RemoveRange(members);
			_projects.Remove(project);
			await _uow.SaveChangesAsync(ct);
			await transaction.CommitAsync(ct);
		}
		catch
		{
			await transaction.RollbackAsync(ct);
			throw;
		}

		await _events.PublishAsync(new ProjectDeletedEvent(id, DateTime.UtcNow), ct);
		return true;
	}

	private static ProjectDto ToProjectDto(Project p) =>
		new(p.Id, p.Name, p.Key, p.Description, p.IsPrivate, p.CreatedAt, p.IsArchived);
}
