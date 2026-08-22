# .NET 10 WebAPI 代码结构设计

> 配套 `proposal.md` + `design.md`。
> 本文件聚焦**目录怎么摆、每层做什么、怎么给未来 Windows Service 和新 API 复用**。
> 阶段 0 的实现按此结构施工。

---

## 0. 分层（严格 6 层）

```
┌─────────────────────────────────────────────────────────────┐
│  AgentBoard.Api              WebAPI 入口 / SignalR Hub       │  ← API 层
│  Controller → BaseController → Provider                      │
├─────────────────────────────────────────────────────────────┤
│  AgentBoard.Application      业务编排 / 跨 Service 组合      │  ← 应用层
│  Provider → Service（接口 + 实现）                           │
├─────────────────────────────────────────────────────────────┤
│  AgentBoard.Domain           实体 / 值对象 / 领域事件        │  ← 领域层
│  Entity / ValueObject / DomainEvent / DomainException       │
├─────────────────────────────────────────────────────────────┤
│  AgentBoard.Infrastructure   Repository / DbContext / MQ     │  ← 基础设施层
│  IRepository<T> / IDbContext / IEventBus                    │
└─────────────────────────────────────────────────────────────┘

   未来新增 AgentBoard.WorkerService（Windows Service / Linux daemon）
       ↑ 直接依赖 Application + Domain，不走 API 层
   未来新增 AgentBoard.IntegrationApi（新外部 API）
       ↑ 直接依赖 Application + Domain
```

**核心约束**（架构测试 NetArchTest 强制）：

| 上层 | 严禁直接调用 | 允许调用 |
|------|--------------|----------|
| Controller | Service / Repository / DbContext | Provider |
| BaseController | Service / Repository | Provider |
| Provider | Repository / DbContext | Service（多个）、其他 Provider（受控） |
| Service | DbContext | IRepository<T>、其他 IService（受控） |
| Repository | — | DbContext |

> 允许层间访问 = 必须有合法理由；越层 = 架构测试 fail。

---

## 1. 完整目录结构

```
dotnet/
├── AgentBoard.sln
├── Directory.Build.props                # 全局编译：nullable、warnings as errors、分析器
├── Directory.Packages.props              # 集中包版本（Central Package Management）
├── .editorconfig
├── global.json                          # .NET 10 SDK pin
├── README.md
│
├── contracts/                            # OpenAPI 契约冻结（见 S0-4）
│   ├── openapi-v3.json                   # FastAPI 快照
│   ├── openapi-v3.sha256
│   └── README.md
│
├── src/
│   ├── AgentBoard.Domain/                # 领域层（纯 C#，无 EF Core / 无 ASP.NET）
│   │   ├── AgentBoard.Domain.csproj
│   │   ├── Common/
│   │   │   ├── Entity.cs                 # 实体基类（Id / RowVersion / AuditFields）
│   │   │   ├── ValueObject.cs
│   │   │   ├── IDomainEvent.cs
│   │   │   ├── DomainException.cs        # 业务异常基类
│   │   │   ├── NotFoundException.cs
│   │   │   ├── DuplicateException.cs
│   │   │   ├── InvalidValueException.cs
│   │   │   ├── IllegalTransitionException.cs
│   │   │   ├── IAuditableEntity.cs       # 审计接口
│   │   │   └── ISoftDeletable.cs        # 软删除接口
│   │   ├── Common/Enums/
│   │   │   ├── ItemType.cs               # task | bug
│   │   │   ├── Status.cs
│   │   │   ├── Priority.cs
│   │   │   ├── SprintStatus.cs
│   │   │   └── ...
│   │   ├── Identity/
│   │   │   ├── User.cs
│   │   │   ├── ApiKey.cs
│   │   │   └── Events/
│   │   │       └── UserLoggedInEvent.cs
│   │   ├── Projects/
│   │   │   ├── Project.cs
│   │   │   ├── ProjectMember.cs
│   │   │   ├── MemberRole.cs
│   │   │   └── Events/
│   │   ├── Epics/
│   │   ├── Stories/
│   │   ├── Tasks/
│   │   │   ├── Task.cs
│   │   │   ├── Comment.cs
│   │   │   ├── Attachment.cs
│   │   │   └── StateMachine/             # 状态机（与 FastAPI 1:1）
│   │   │       ├── TaskStatusMachine.cs
│   │   │       └── ITaskStatusMachine.cs
│   │   ├── Sprints/
│   │   ├── Documents/
│   │   ├── Proposals/                    # 阶段 1 之后才用，阶段 0 留空目录
│   │   ├── Notifications/
│   │   ├── Webhooks/
│   │   └── AuditLogs/
│   │
│   ├── AgentBoard.Application/           # 应用层（无 EF Core 引用）
│   │   ├── AgentBoard.Application.csproj
│   │   ├── Abstractions/                 # 跨层接口
│   │   │   ├── IService.cs               # 标记接口
│   │   │   ├── IProvider.cs              # 标记接口
│   │   │   ├── IRepository.cs            # 通用仓储接口
│   │   │   ├── IDbContext.cs             # DbContext 抽象
│   │   │   ├── IUnitOfWork.cs
│   │   │   ├── ICurrentUser.cs           # 当前用户上下文
│   │   │   ├── IEventBus.cs
│   │   │   ├── IBlobStorage.cs
│   │   │   ├── IClock.cs                 # 时间抽象（便于测试）
│   │   │   └── IOutbox.cs
│   │   ├── Common/
│   │   │   ├── PagedRequest.cs
│   │   │   ├── PagedResponse.cs
│   │   │   ├── QueryExtensions.cs        # WhereIf / OrderByDynamic / ToPagedAsync
│   │   │   ├── Mapping/
│   │   │   │   └── IMap.cs
│   │   │   └── Result.cs                 # Result<T> 模式（避免抛异常控流）
│   │   ├── Identity/                     # 业务能力模块（按 feature 切分）
│   │   │   ├── IUserService.cs           # 对 User domain 的操作集合
│   │   │   ├── UserService.cs
│   │   │   ├── IApiKeyService.cs
│   │   │   └── ApiKeyService.cs
│   │   ├── Projects/
│   │   │   ├── IProjectService.cs
│   │   │   ├── ProjectService.cs
│   │   │   ├── IProjectProvider.cs       # 业务编排（Controller 调）
│   │   │   ├── ProjectProvider.cs
│   │   │   ├── Specifications/           # 查询规约（替代裸 IQueryable 传参）
│   │   │   │   ├── ProjectByIdSpec.cs
│   │   │   │   ├── ProjectListSpec.cs
│   │   │   │   └── ...
│   │   │   └── Dtos/
│   │   │       ├── ProjectDto.cs
│   │   │       ├── ProjectCreateRequest.cs
│   │   │       └── ProjectUpdateRequest.cs
│   │   ├── Epics/                        # 同 Projects 结构
│   │   ├── Stories/
│   │   ├── Tasks/
│   │   ├── Sprints/
│   │   ├── Documents/
│   │   ├── Notifications/
│   │   ├── Webhooks/
│   │   ├── Search/
│   │   ├── Admin/
│   │   └── Auth/
│   │
│   ├── AgentBoard.Infrastructure/        # 基础设施层
│   │   ├── AgentBoard.Infrastructure.csproj
│   │   ├── Persistence/
│   │   │   ├── AppDbContext.cs
│   │   │   ├── DbContextAdapter.cs       # 包装 IDbContext 暴露给 Application
│   │   │   ├── Interceptors/
│   │   │   │   ├── AuditFieldsInterceptor.cs        # 自动填 CreatedAt/UpdatedAt/CreatedBy
│   │   │   │   ├── SoftDeleteInterceptor.cs         # 软删除转换
│   │   │   │   └── DomainEventDispatcherInterceptor.cs
│   │   │   ├── Configurations/           # IEntityTypeConfiguration 拆分
│   │   │   │   ├── ProjectConfiguration.cs
│   │   │   │   ├── TaskConfiguration.cs
│   │   │   │   └── ...
│   │   │   ├── Repositories/
│   │   │   │   ├── Repository.cs         # 通用实现
│   │   │   │   ├── IProjectRepository.cs
│   │   │   │   ├── ProjectRepository.cs  # 复杂查询用 Select 投影
│   │   │   │   ├── ITaskRepository.cs
│   │   │   │   ├── TaskRepository.cs
│   │   │   │   └── ...
│   │   │   ├── Specifications/
│   │   │   │   └── SpecificationEvaluator.cs         # 规约 → EF 查询
│   │   │   └── Migrations/               # EF Core 自动生成（不 apply）
│   │   ├── Migrations/Sql/               # 手工 SQL 脚本（与 Alembic 同步）
│   │   │   └── 001_init.sql
│   │   ├── Auth/
│   │   │   ├── HmacTokenValidator.cs     # 透传 FastAPI 同一 Token 校验
│   │   │   ├── ApiKeyValidator.cs
│   │   │   ├── PasswordHasher.cs         # PBKDF2（与 FastAPI 1:1）
│   │   │   └── CurrentUserService.cs     # ICurrentUser 实现
│   │   ├── Messaging/
│   │   │   ├── RabbitMqEventBus.cs
│   │   │   ├── RabbitMqOptions.cs
│   │   │   └── Outbox/
│   │   │       ├── OutboxMessage.cs
│   │   │       ├── OutboxRepository.cs
│   │   │       └── OutboxDispatcherHostedService.cs
│   │   ├── Storage/
│   │   │   └── CosBlobStorage.cs
│   │   ├── Observability/
│   │   │   ├── SerilogSetup.cs
│   │   │   ├── OpenTelemetrySetup.cs
│   │   │   └── AgentBoardActivitySource.cs
│   │   ├── Time/
│   │   │   └── SystemClock.cs            # IClock 实现
│   │   ├── DependencyInjection.cs        # AddInfrastructure(IConfiguration)
│   │   └── Migrations/                   # 启动时跑 alembic 对齐的占位
│   │
│   ├── AgentBoard.Api/                   # API 层
│   │   ├── AgentBoard.Api.csproj
│   │   ├── Program.cs
│   │   ├── appsettings.json
│   │   ├── appsettings.Development.json
│   │   ├── launchSettings.json           # 18099 端口
│   │   ├── Api/
│   │   │   ├── Base/
│   │   │   │   └── BaseController.cs     # 统一异常映射 + UserContext
│   │   │   ├── Common/
│   │   │   │   ├── ApiResult.cs          # 统一响应包装
│   │   │   │   ├── ApiError.cs
│   │   │   │   ├── DomainExceptionFilter.cs   # 全局异常 → HTTP
│   │   │   │   ├── ValidationFilter.cs
│   │   │   │   └── DtoMappingExtensions.cs
│   │   │   ├── Middleware/
│   │   │   │   ├── RequestIdMiddleware.cs
│   │   │   │   ├── TraceContextMiddleware.cs
│   │   │   │   ├── AuthMiddleware.cs
│   │   │   │   ├── ApiKeyMiddleware.cs
│   │   │   │   ├── CorsMiddleware.cs
│   │   │   │   └── AuditLogMiddleware.cs
│   │   │   └── Conventions/
│   │   │       └── ApiRouteConvention.cs # /api 前缀
│   │   ├── Features/                     # 按 feature 切（与 FastAPI features 1:1）
│   │   │   ├── Health/
│   │   │   │   ├── HealthController.cs
│   │   │   │   └── Dtos/
│   │   │   ├── Meta/
│   │   │   │   ├── MetaController.cs
│   │   │   │   ├── MetaConstants.cs      # 与 FastAPI 1:1 写死
│   │   │   │   └── Dtos/
│   │   │   ├── Auth/
│   │   │   │   ├── AuthController.cs
│   │   │   │   ├── AuthProvider.cs       # 业务编排
│   │   │   │   └── Dtos/
│   │   │   ├── Projects/
│   │   │   │   ├── ProjectsController.cs
│   │   │   │   ├── ProjectProvider.cs
│   │   │   │   └── Dtos/
│   │   │   ├── Epics/
│   │   │   ├── Stories/
│   │   │   ├── Tasks/
│   │   │   ├── Sprints/
│   │   │   ├── Documents/
│   │   │   ├── Notifications/
│   │   │   ├── Webhooks/
│   │   │   ├── Search/
│   │   │   ├── Identity/
│   │   │   ├── Admin/
│   │   │   └── SignalR/
│   │   │       ├── AgentStateHub.cs      # 替代 FastAPI /ws/agents
│   │   │       └── ...
│   │   ├── Clients/                      # NSwag 自动生成（commit 进仓）
│   │   │   ├── AgentBoardFastApiClient.cs
│   │   │   └── README.md
│   │   └── Hubs/                         # SignalR Hub（如果拆出 Features）
│   │       └── ...
│   │
│   └── AgentBoard.WorkerService/         # 未来 Windows Service / 后台守护（占位，阶段 0 不实现）
│       └── AgentBoard.WorkerService.csproj
│
└── tests/
    ├── AgentBoard.Domain.Tests/
    ├── AgentBoard.Application.Tests/
    ├── AgentBoard.Infrastructure.Tests/
    └── AgentBoard.Api.Tests/
        ├── Unit/                         # 单元测试（每层独立）
        ├── Integration/                  # 集成测试（WebApplicationFactory + InMemory DB）
        └── Contract/                     # 契约测试（FastAPI 真实进程 vs .NET 真实进程）
            ├── Fixtures/
            │   ├── FastApiFixture.cs
            │   └── DotNetApiFixture.cs
            ├── HealthContractTests.cs
            └── MetaContractTests.cs
```

---

## 2. 每层职责边界（单一职责）

### 2.1 Domain（领域层）

**职责**：定义业务实体、值对象、领域事件、领域异常、状态机。

**依赖**：纯 C#，不允许引用 `EntityFrameworkCore`、`AspNetCore`、任何 NuGet。

**典型代码**（Task 实体示意）：

```csharp
// src/AgentBoard.Domain/Tasks/Task.cs
public sealed class Task : Entity, IAuditableEntity, ISoftDeletable
{
    public int Id { get; private set; }
    public int StoryId { get; private set; }
    public ItemType Type { get; private set; }       // task | bug
    public string Title { get; private set; }
    public Status Status { get; private set; }
    public Priority Priority { get; private set; }
    public string? Description { get; private set; }
    public string? Spec { get; private set; }
    public int? SourceSpecId { get; private set; }
    public long RowVersion { get; private set; }     // 乐观锁
    public DateTime? DeletedAt { get; private set; }
    public DateTime CreatedAt { get; private set; }
    public DateTime UpdatedAt { get; private set; }
    public int? CreatedBy { get; private set; }
    public int? UpdatedBy { get; private set; }

    private Task() { }  // EF

    public static Task Create(int storyId, ItemType type, string title, int createdBy) { ... }
    public void ChangeStatus(Status next, ITaskStatusMachine machine) { ... }   // 状态机校验
    public void UpdateSpec(string spec) { ... }
    public void SoftDelete() { DeletedAt = DateTime.UtcNow; }
}
```

### 2.2 Application（应用层）

**职责**：Service 实现（纯业务方法，对某 domain 的操作集合）+ Provider（跨 Service 业务编排）+ DTO + 规约 + 抽象接口（IRepository / IDbContext / IEventBus）。

**依赖**：Domain + Application 自身的 Abstractions。**不允许引用 EF Core**（解耦，未来可换 ORM）。

**Service 示例**（UserService）：

```csharp
// src/AgentBoard.Application/Identity/UserService.cs
public interface IUserService : IService
{
    Task<User?> GetByIdAsync(int id, CancellationToken ct);
    Task<User?> GetByUsernameAsync(string username, CancellationToken ct);
    Task<User> CreateAsync(string username, string password, CancellationToken ct);
    Task UpdateProfileAsync(int id, string? displayName, string? email, CancellationToken ct);
    Task ChangePasswordAsync(int id, string currentPassword, string newPassword, CancellationToken ct);
    Task<bool> VerifyPasswordAsync(int id, string password, CancellationToken ct);
}

public sealed class UserService : IUserService
{
    private readonly IUserRepository _users;
    private readonly IPasswordHasher _hasher;
    private readonly IUnitOfWork _uow;
    private readonly IClock _clock;

    public UserService(IUserRepository users, IPasswordHasher hasher, IUnitOfWork uow, IClock clock)
    { _users = users; _hasher = hasher; _uow = uow; _clock = clock; }

    public async Task<User?> GetByIdAsync(int id, CancellationToken ct) =>
        await _users.GetByIdAsync(id, ct);

    public async Task<User> CreateAsync(string username, string password, CancellationToken ct)
    {
        if (await _users.ExistsByUsernameAsync(username, ct))
            throw new DuplicateException($"username '{username}' already exists");
        var user = User.Create(username, await _hasher.HashAsync(password), _clock.UtcNow);
        await _users.AddAsync(user, ct);
        await _uow.SaveChangesAsync(ct);
        return user;
    }
    // ...
}
```

**Provider 示例**（业务编排 + 跨 Service）：

```csharp
// src/AgentBoard.Application/Auth/AuthProvider.cs
public interface IAuthProvider : IProvider
{
    Task<AuthSessionDto> LoginAsync(string username, string password, CancellationToken ct);
    Task<UserDto> GetCurrentAsync(int uid, CancellationToken ct);
    Task ChangePasswordAsync(int uid, string currentPassword, string newPassword, CancellationToken ct);
}

public sealed class AuthProvider : IAuthProvider
{
    private readonly IUserService _users;
    private readonly IApiKeyService _apiKeys;
    private readonly IJwtIssuer _jwt;             // HMAC 签发（与 FastAPI 同一密钥）
    private readonly IEventBus _events;

    public AuthProvider(IUserService users, IApiKeyService apiKeys, IJwtIssuer jwt, IEventBus events)
    { _users = users; _apiKeys = apiKeys; _jwt = jwt; _events = events; }

    public async Task<AuthSessionDto> LoginAsync(string username, string password, CancellationToken ct)
    {
        var user = await _users.GetByUsernameAsync(username, ct)
            ?? throw new InvalidValueException("invalid credentials");
        if (!await _users.VerifyPasswordAsync(user.Id, password, ct))
            throw new InvalidValueException("invalid credentials");

        var token = await _jwt.IssueAsync(user.Id, ct);
        await _events.PublishAsync(new UserLoggedInEvent(user.Id, _clock.UtcNow), ct);
        return new AuthSessionDto(user.Id, user.Username, token);
    }
    // ...
}
```

**Service 给未来 Windows Service 复用**：
- Windows Service 启动后用 `IUserService` 做后台清理任务（删过期 token / 跑统计）
- Windows Service 注入 `IUserService` → 跟 WebAPI Controller 调同一个 Service
- 不需要再写一份业务逻辑

### 2.3 Infrastructure（基础设施层）

**职责**：所有"不纯粹业务"的代码：EF Core DbContext、Repository 实现、MQ、对象存储、密码哈希、当前用户上下文实现、Serilog/OTel 配置。

**关键设计点**：

#### 2.3.1 IRepository<T>（通用 + 领域专用）

```csharp
// src/AgentBoard.Application/Abstractions/IRepository.cs
public interface IRepository<T> where T : Entity
{
    Task<T?> GetByIdAsync(object id, CancellationToken ct);
    Task<IReadOnlyList<T>> ListAsync(ISpecification<T>? spec = null, CancellationToken ct = default);
    Task<IReadOnlyList<T>> ListAsync(Expression<Func<T, bool>>? predicate, CancellationToken ct = default);
    Task<T> AddAsync(T entity, CancellationToken ct = default);
    Task UpdateAsync(T entity, CancellationToken ct = default);
    Task DeleteAsync(T entity, CancellationToken ct = default);
    Task<int> SaveChangesAsync(CancellationToken ct = default);
}

// src/AgentBoard.Application/Abstractions/ISpecification.cs
public interface ISpecification<T>
{
    Expression<Func<T, bool>>? Criteria { get; }
    List<Expression<Func<T, object>>> Includes { get; } = new();
    List<string> IncludeStrings { get; } = new();
    Expression<Func<T, object>>? OrderBy { get; }
    Expression<Func<T, object>>? OrderByDescending { get; }
    int? Take { get; }
    int? Skip { get; }
}
```

> **注意**：`ISpecification.Includes` 我们**不**在 Repository 用（避免 `Include` 笛卡尔积）。
> Specification 只承载 `Criteria / OrderBy / Take / Skip / Select 投影`，真正的关联查询由 Repository 用 LINQ Join + Select 显式实现。

#### 2.3.2 Repository 实现（避免 Include）

```csharp
// src/AgentBoard.Infrastructure/Persistence/Repositories/ProjectRepository.cs
public sealed class ProjectRepository : Repository<Project>, IProjectRepository
{
    public ProjectRepository(IDbContext db) : base(db) { }

    // 列表：只投影 DTO 字段，不拉整个实体
    public async Task<IReadOnlyList<ProjectListItemDto>> ListForOwnerAsync(
        int ownerId, PagedRequest page, CancellationToken ct)
    {
        return await Db.Projects
            .AsNoTracking()
            .Where(p => p.CreatedBy == ownerId && p.DeletedAt == null)
            .OrderByDescending(p => p.UpdatedAt)
            .Select(p => new ProjectListItemDto(
                p.Id,
                p.Name,
                p.Key,
                p.Description,
                p.UpdatedAt,
                p.CreatedBy))
            .Skip(page.Skip)
            .Take(page.Take)
            .ToListAsync(ct);
    }

    // 详情：单次查询拿全部信息（用 LEFT JOIN + 聚合，避免 N+1）
    public async Task<ProjectDetailDto?> GetDetailAsync(int id, CancellationToken ct)
    {
        var detail = await (
            from p in Db.Projects.AsNoTracking()
            where p.Id == id && p.DeletedAt == null
            join owner in Db.Users.AsNoTracking() on p.CreatedBy equals owner.Id into ownerJoin
            from owner in ownerJoin.DefaultIfEmpty()
            select new
            {
                Project = p,
                OwnerName = owner != null ? owner.Username : null
            }
        ).FirstOrDefaultAsync(ct);

        if (detail is null) return null;

        // 关联统计：单次聚合查询
        var stats = await Db.Stories
            .Where(s => s.Epic.ProjectId == id)
            .GroupBy(s => 1)
            .Select(g => new
            {
                Total = g.Count(),
                Done = g.Count(s => s.Status == Status.Done)
            })
            .FirstOrDefaultAsync(ct);

        return new ProjectDetailDto(
            detail.Project.Id,
            detail.Project.Name,
            detail.Project.Key,
            detail.Project.Description,
            detail.OwnerName,
            stats?.Total ?? 0,
            stats?.Done ?? 0,
            detail.Project.CreatedAt,
            detail.Project.UpdatedAt);
    }
}
```

**为什么不用 `Include`**：
- `Include` 走笛卡尔积，多个 `Include` 组合时容易爆 O(N×M)
- 投影只 SELECT 用到的列，节省 IO
- 多次 `Include` 的同一 entity 可能产生重复行，需要 `Distinct()` 又回到性能坑
- 显式 Join + 聚合查询 1 次往返拿全部数据，N+1 消失

#### 2.3.3 IDbContext 抽象

```csharp
// src/AgentBoard.Application/Abstractions/IDbContext.cs
public interface IDbContext
{
    // 不暴露 DbSet<T>，由 Repository 自己注入 DbContext 强类型访问
    // 提供：SaveChangesAsync / ExecuteSqlAsync / BeginTransaction
    Task<int> SaveChangesAsync(CancellationToken ct = default);
    Task ExecuteSqlAsync(string sql, params object[] parameters);
    Task<IDbTransaction> BeginTransactionAsync(CancellationToken ct = default);
}
```

> **理由**：Application 层不依赖 EF Core，所以 `IQueryable<T>` 也不能在接口里出现。
> `DbContextAdapter`（Infrastructure 层实现 `IDbContext`）持有真正的 `AppDbContext`。
> Repository 内部用 `AppDbContext`（强类型），对外暴露 DTO。

#### 2.3.4 拦截器

```csharp
// src/AgentBoard.Infrastructure/Persistence/Interceptors/AuditFieldsInterceptor.cs
public sealed class AuditFieldsInterceptor : SaveChangesInterceptor
{
    private readonly ICurrentUser _current;
    private readonly IClock _clock;

    public AuditFieldsInterceptor(ICurrentUser current, IClock clock) { _current = current; _clock = clock; }

    public override InterceptionResult<int> SavingChanges(DbContextEventData e, InterceptionResult<int> r)
    {
        Apply(e.Context);
        return r;
    }

    private void Apply(DbContext db)
    {
        var now = _clock.UtcNow;
        var uid = _current.UserId;
        foreach (var entry in db.ChangeTracker.Entries())
        {
            if (entry.Entity is IAuditableEntity auditable)
            {
                if (entry.State == EntityState.Added)
                {
                    auditable.CreatedAt = now;
                    auditable.CreatedBy = uid;
                }
                auditable.UpdatedAt = now;
                auditable.UpdatedBy = uid;
            }
        }
    }
}
```

#### 2.3.5 软删除

```csharp
// ISoftDeletable 实现 + 拦截器自动转 Delete → SoftDelete
// Global query filter 自动过滤 DeletedAt != null
modelBuilder.Entity<Project>().HasQueryFilter(p => p.DeletedAt == null);
```

### 2.4 Api（API 层）

**职责**：HTTP 路由、DTO 映射、异常映射、鉴权中间件、SignalR Hub。

**Controller 极薄**（只做契约）：

```csharp
// src/AgentBoard.Api/Features/Auth/AuthController.cs
public sealed class AuthController : BaseController<IAuthProvider>
{
    public AuthController(IAuthProvider provider, ICurrentUser current) : base(provider, current) { }

    [HttpPost("login")]
    [AllowAnonymous]
    public async Task<ActionResult<AuthSessionDto>> Login(
        [FromBody] LoginRequest req,
        CancellationToken ct)
        => Ok(await Provider.LoginAsync(req.Username, req.Password, ct));

    [HttpGet("me")]
    public async Task<ActionResult<UserDto>> Me(CancellationToken ct)
        => Ok(await Provider.GetCurrentAsync(CurrentUser.UserId!.Value, ct));
}
```

**BaseController 统一异常映射**：

```csharp
// src/AgentBoard.Api/Api/Base/BaseController.cs
public abstract class BaseController : ControllerBase
{
    protected ICurrentUser CurrentUser { get; }

    protected BaseController(ICurrentUser current) { CurrentUser = current; }

    protected ActionResult HandleException(DomainException ex) => ex switch
    {
        NotFoundException e        => NotFound(new ApiError(e.Message)),
        DuplicateException e       => Conflict(new ApiError(e.Message)),
        InvalidValueException e    => UnprocessableEntity(new ApiError(e.Message)),
        IllegalTransitionException e => BadRequest(new ApiError(e.Message)),
        _                          => StatusCode(500, new ApiError(ex.Message))
    };
}

public abstract class BaseController<TProvider> : BaseController where TProvider : IProvider
{
    protected TProvider Provider { get; }
    protected BaseController(TProvider provider, ICurrentUser current) : base(current)
    { Provider = provider; }
}
```

> **Controller 不做异常 try-catch**：用全局 `DomainExceptionFilter`（`IExceptionFilter`）统一拦截。

---

## 3. 复用规则

### 3.1 Service 给 WebAPI 和 Windows Service 共用

```
┌────────────────────┐  ┌──────────────────────┐
│ AgentBoard.Api     │  │ AgentBoard.WorkerSvc │  (未来)
│ Controllers        │  │ BackgroundServices   │
│   ↓                │  │   ↓                  │
│ Providers          │  │ Providers (相同)     │
│   ↓                │  │   ↓                  │
│ Services  ←────────────────────┐            │
│   ↓                            │            │
│ Repositories  ←────────────────┘            │
│   ↓                                         │
│ DbContext                                    │
└──────────────────────────────────────────────┘
```

**复用机制**：
- `AgentBoard.Application` 是唯一的应用层包
- WebAPI 启动：`AddApplication()` + `AddInfrastructure()` + `AddWebApi()`
- WorkerService 启动：`AddApplication()` + `AddInfrastructure()` + `AddWorker()`
- 两者**完全共享** Service / Repository / DbContext / Domain

### 3.2 Domain 给未来新 API 共享

```
未来：AgentBoard.IntegrationApi（新外部 API）
   ↓
AgentBoard.Application（直接引用）
   ↓
AgentBoard.Domain（直接引用）
```

**机制**：
- Domain 没有任何外部依赖（纯 C#）
- Application 依赖 Domain + Abstractions
- 新 API 项目只需要 `<ProjectReference>` Application + Domain + Infrastructure（不引用 AgentBoard.Api）
- Feature 切分已经按 business capability（`Projects/`、`Tasks/`），新 API 直接复用 Service

### 3.3 关键设计：Provider 是"API 形状"

不同 API 入口的 Provider 可以不同：

```csharp
// AgentBoard.Api/Features/Projects/ProjectProvider.cs       （WebAPI Provider）
// AgentBoard.IntegrationApi/Projects/ProjectIntegrationProvider.cs  （集成 API Provider，复用 ProjectService）
```

但底层 `ProjectService` 100% 复用，只在 Provider 层做"业务编排差异"。

---

## 4. 命名规范

| 元素 | 规范 | 示例 |
|------|------|------|
| 解决方案 | `AgentBoard.sln` | - |
| 业务包 | `AgentBoard.{Layer}` | `AgentBoard.Domain` |
| 实体 | PascalCase，单数 | `Project`, `Task`, `Comment` |
| DbContext | `AppDbContext` | - |
| Repository 接口 | `I{Domain}Repository` | `IProjectRepository` |
| Repository 实现 | `{Domain}Repository` | `ProjectRepository` |
| Service 接口 | `I{Domain}Service : IService` | `IProjectService` |
| Service 实现 | `{Domain}Service` | `ProjectService` |
| Provider 接口 | `I{Feature}Provider : IProvider` | `IAuthProvider` |
| Provider 实现 | `{Feature}Provider` | `AuthProvider` |
| Controller | `{Feature}Controller` | `AuthController`, `ProjectsController` |
| DTO | `{Domain}Dto` / `{Domain}CreateRequest` | `ProjectDto`, `ProjectCreateRequest` |
| 中间件 | `{Name}Middleware` | `RequestIdMiddleware` |
| 过滤器 | `{Name}Filter` | `DomainExceptionFilter` |
| 异常 | `{Reason}Exception : DomainException` | `NotFoundException` |
| 事件 | `{PastTense}Event` | `UserLoggedInEvent`, `TaskStatusChangedEvent` |
| 规约 | `{Description}Spec` | `ProjectByOwnerSpec` |

---

## 5. 关键 NuGet 包（阶段 0 必装）

```xml
<!-- Directory.Packages.props 集中版本管理 -->
<PackageVersion Include="Microsoft.AspNetCore.OpenApi" Version="10.0.0-*" />
<PackageVersion Include="Microsoft.EntityFrameworkCore" Version="10.0.0-*" />
<PackageVersion Include="Microsoft.EntityFrameworkCore.Design" Version="10.0.0-*" />
<PackageVersion Include="Microsoft.EntityFrameworkCore.InMemory" Version="10.0.0-*" />
<PackageVersion Include="Pomelo.EntityFrameworkCore.MySql" Version="9.0.0-*" />
<PackageVersion Include="Serilog.AspNetCore" Version="9.0.0" />
<PackageVersion Include="Serilog.Sinks.Console" Version="6.0.0" />
<PackageVersion Include="Serilog.Sinks.File" Version="6.0.0" />
<PackageVersion Include="OpenTelemetry.Extensions.Hosting" Version="1.10.0" />
<PackageVersion Include="OpenTelemetry.Instrumentation.AspNetCore" Version="1.10.1" />
<PackageVersion Include="OpenTelemetry.Instrumentation.Http" Version="1.10.0" />
<PackageVersion Include="OpenTelemetry.Instrumentation.EntityFrameworkCore" Version="1.10.0-beta-1" />
<PackageVersion Include="NSwag.AspNetCore" Version="14.0.0" />
<PackageVersion Include="Polly" Version="8.5.0" />
<PackageVersion Include="Polly.Extensions.Http" Version="3.0.0" />

<!-- Test -->
<PackageVersion Include="xunit" Version="2.9.2" />
<PackageVersion Include="xunit.runner.visualstudio" Version="2.8.2" />
<PackageVersion Include="Microsoft.AspNetCore.Mvc.Testing" Version="9.0.0" />
<PackageVersion Include="FluentAssertions" Version="7.0.0" />
<PackageVersion Include="NSubstitute" Version="5.3.0" />
<PackageVersion Include="NetArchTest.Rules" Version="1.3.2" />
<PackageVersion Include="coverlet.collector" Version="6.0.2" />
```

---

## 6. 阶段 0 落地检查表

| Story | 落地的代码/文件 | 验证 |
|-------|----------------|------|
| **S0-1 脚手架** | `dotnet/AgentBoard.sln` + `src/AgentBoard.Api/*` + `tests/AgentBoard.Api.Tests/*` + `Dockerfile.dotnet` | `dotnet build` 成功；`docker build` 成功 |
| **S0-2 Repository** | `Application/Abstractions/IRepository.cs` + `Infrastructure/Persistence/Repository.cs` + `AppDbContext.cs` + 3 个拦截器 | xUnit InMemory 测试 100% 绿；性能基线 |
| **S0-3 分层骨架** | `BaseController.cs` + `IAuthProvider` + `AuthProvider` + `IUserService` + `UserService` + `IUserRepository` + `UserRepository` + 架构测试 | 5 层独立单测 + 端到端跑通 + NetArchTest 全绿 |
| **S0-4 契约冻结** | `scripts/sync-openapi.ps1` + `schema-drift-check.py` + `generate-fastapi-client.ps1` + CI workflow | 快照生成 + 0 drift + NSwag client 编译通过 |
| **S0-5 health/meta** | `Features/Health/HealthController.cs` + `Features/Meta/MetaController.cs` + Contract Tests | 双栈 1:1；contract test 全绿 |
| **S0-6 docker** | `docker-compose.yml` 新增 `api-dotnet` + nginx 注释 | 6 服务全绿；.NET 18099 / FastAPI 18000 端口可达 |
| **S0-7 OTel** | `SerilogSetup` + `OpenTelemetrySetup` + 3 个 Middleware | 日志带 request_id；trace 可见 |
| **S0-8 文档** | `docs/dual-stack-bff-runbook.md` + `dotnet/README.md` + `README.md` 架构图 | 新人 30 分钟跑通 |

---

## 7. 验收标准（阶段 0 收官）

- [ ] `dotnet build` 全绿
- [ ] `dotnet test` 100% 绿（含 contract test）
- [ ] `dotnet run` 启动后 `curl http://localhost:18099/api/health` 返 200 `{"status":"ok"}`
- [ ] `curl http://localhost:18099/api/meta` 返与 FastAPI 完全一致
- [ ] `docker compose up -d` 6 个服务全绿
- [ ] `docker build -f Dockerfile.dotnet .` 成功
- [ ] Serilog 日志带 `request_id`
- [ ] OpenTelemetry trace 自动注入
- [ ] NSwag client 编译通过 + commit 进仓
- [ ] `docs/dual-stack-bff-runbook.md` 完整
- [ ] 5 层架构测试（NetArchTest）全绿
- [ ] FastAPI 一行代码未改
- [ ] 阶段 0 退出后，文档 + commit + push 完毕，openspec/tasks.md 阶段 0 全勾
