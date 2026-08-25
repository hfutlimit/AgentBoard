// SPDX-License-Identifier: MIT
using AgentBoard.Application.Abstractions;
using AgentBoard.Application.Identity;
using AgentBoard.Application.Identity.Dtos;
using AgentBoard.Domain.Common;
using AgentBoard.Domain.Identity;
using FluentAssertions;
using NSubstitute;

namespace AgentBoard.Application.Tests.Identity;

/// <summary>
/// Service-layer unit tests (S0-3 acceptance: each layer tested in
/// isolation with the layer below replaced by NSubstitute mocks).
/// The repository / unit-of-work / clock / hasher seams are all mocked,
/// so these tests pin UserService's business rules without any database.
/// </summary>
public sealed class UserServiceTests
{
    private static readonly DateTime Now = new(2026, 8, 25, 0, 0, 0, DateTimeKind.Utc);

    private readonly IUserRepository _users = Substitute.For<IUserRepository>();
    private readonly IUnitOfWork _uow = Substitute.For<IUnitOfWork>();
    private readonly IClock _clock = Substitute.For<IClock>();
    private readonly IPasswordHasher _hasher = Substitute.For<IPasswordHasher>();

    private UserService CreateSut() => new(_users, _uow, _clock, _hasher);

    public UserServiceTests() => _clock.UtcNow.Returns(Now);

    [Fact]
    public async Task GetByUsernameAsync_Delegates_ToRepository()
    {
        var alice = User.Create("alice", "hash", false, Now);
        _users.GetByUsernameAsync("alice", Arg.Any<CancellationToken>()).Returns(alice);

        var found = await CreateSut().GetByUsernameAsync("alice");

        found.Should().BeSameAs(alice);
    }

    [Fact]
    public async Task CreateAsync_RejectsShortPassword_WithoutTouchingRepository()
    {
        var act = () => CreateSut().CreateAsync(new CreateUserRequest("bob", "short"));

        await act.Should().ThrowAsync<InvalidValueException>()
            .WithMessage("password must be at least 8 characters*");
        await _users.DidNotReceiveWithAnyArgs().ExistsByUsernameAsync(default!, default);
    }

    [Fact]
    public async Task CreateAsync_ThrowsDuplicate_WhenUsernameTaken()
    {
        _users.ExistsByUsernameAsync("alice", Arg.Any<CancellationToken>()).Returns(true);

        var act = () => CreateSut().CreateAsync(new CreateUserRequest("alice", "longenough"));

        await act.Should().ThrowAsync<DuplicateException>();
        _hasher.DidNotReceiveWithAnyArgs().Hash(default!);
    }

    [Fact]
    public async Task CreateAsync_HashesPassword_PersistsUser_AndReturnsDto()
    {
        _users.ExistsByUsernameAsync("alice", Arg.Any<CancellationToken>()).Returns(false);
        _hasher.Hash("longenough").Returns("pbkdf2-hash");

        var dto = await CreateSut().CreateAsync(new CreateUserRequest("alice", "longenough"));

        dto.Username.Should().Be("alice");
        dto.IsAdmin.Should().BeFalse();
        dto.CreatedAt.Should().Be(Now);
        await _users.Received(1).AddAsync(
            Arg.Is<User>(u => u.Username == "alice" && u.PasswordHash == "pbkdf2-hash"),
            Arg.Any<CancellationToken>());
        await _uow.Received(1).SaveChangesAsync(Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task VerifyPasswordAsync_ReturnsFalse_WhenUserMissing()
    {
        _users.GetByIdAsync(42, Arg.Any<CancellationToken>()).Returns((User?)null);

        var ok = await CreateSut().VerifyPasswordAsync(42, "whatever");

        ok.Should().BeFalse();
        _hasher.DidNotReceiveWithAnyArgs().Verify(default!, default!);
    }

    [Fact]
    public async Task VerifyPasswordAsync_Delegates_ToHasher()
    {
        var alice = User.Create("alice", "stored-hash", false, Now);
        _users.GetByIdAsync(1, Arg.Any<CancellationToken>()).Returns(alice);
        _hasher.Verify("stored-hash", "secret").Returns(true);

        var ok = await CreateSut().VerifyPasswordAsync(1, "secret");

        ok.Should().BeTrue();
    }

    [Fact]
    public async Task ChangePasswordAsync_RejectsWrongCurrentPassword()
    {
        _users.GetByIdAsync(1, Arg.Any<CancellationToken>())
            .Returns(User.Create("alice", "stored-hash", false, Now));
        _hasher.Verify("stored-hash", "wrong").Returns(false);

        var act = () => CreateSut().ChangePasswordAsync(1, "wrong", "newlongenough");

        await act.Should().ThrowAsync<InvalidValueException>()
            .WithMessage("current password is incorrect*");
        _users.DidNotReceiveWithAnyArgs().Update(default!);
    }

    [Fact]
    public async Task ChangePasswordAsync_Rehashes_AndSaves()
    {
        var alice = User.Create("alice", "stored-hash", false, Now);
        _users.GetByIdAsync(1, Arg.Any<CancellationToken>()).Returns(alice);
        _hasher.Verify("stored-hash", "oldsecret").Returns(true);
        _hasher.Hash("newlongenough").Returns("new-hash");

        await CreateSut().ChangePasswordAsync(1, "oldsecret", "newlongenough");

        alice.PasswordHash.Should().Be("new-hash");
        _users.Received(1).Update(alice);
        await _uow.Received(1).SaveChangesAsync(Arg.Any<CancellationToken>());
    }

    [Fact]
    public async Task UpdateProfileAsync_ThrowsNotFound_WhenUserMissing()
    {
        _users.GetByIdAsync(9, Arg.Any<CancellationToken>()).Returns((User?)null);

        var act = () => CreateSut().UpdateProfileAsync(9, "display", null, null);

        await act.Should().ThrowAsync<NotFoundException>();
    }
}
